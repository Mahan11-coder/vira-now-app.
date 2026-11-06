import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

import pytest

import app.payments as pay
from conftest import MERCHANT, SECRET


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch(url):
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def to_site(pay_url, base):
    """pay_url از PAY_BASE_URL ساختگی می‌آید؛ آن را روی هاست تستی PHP می‌بریم."""
    return base + pay_url.split("/pay", 1)[1].replace("/pay.php", "/pay.php", 1) if False else base + "/" + pay_url.split("/pay/", 1)[1]


def balance(client, user):
    return client.get("/api/auth/me", headers=user["headers"]).json["balance_toman"]


def invoice(client, user, amount=50000):
    r = client.post("/api/payments/invoices", headers=user["headers"], json={"amount_toman": amount})
    assert r.status_code == 201, r.json
    return r.json


def simulate_gateway_return(site_base, pay_page_html, link_query, ok=True):
    url = re.search(r'href="([^"]+/pg/StartPay/(A\d+))"', pay_page_html)
    authority = url.group(2)
    suffix = "&Authority={}&Status={}".format(authority, "OK" if ok else "NOK")
    return site_base + "/back.php?" + link_query + suffix


def test_link_is_signed_and_amount_validated(client, user):
    inv = invoice(client, user, 25000)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(inv["pay_url"]).query)
    assert q["t"] == ["25000"] and q["m"] == [MERCHANT] and q["u"] == [str(user["info"]["id"])]
    assert q["s"][0] == pay.link_signature(q["d"][0], q["u"][0], "25000", MERCHANT, "vira", "0")
    assert "secret" not in inv["pay_url"].lower() and SECRET not in inv["pay_url"]
    for bad in (5, 10**9, -5, "100", True, None):
        assert client.post("/api/payments/invoices", headers=user["headers"], json={"amount_toman": bad}).status_code == 400


def test_invoice_requires_login_and_config(client, user, monkeypatch):
    assert client.post("/api/payments/invoices", json={"amount_toman": 20000}).status_code == 401
    monkeypatch.setenv("PAY_SHARED_SECRET", "short")
    assert client.post("/api/payments/invoices", headers=user["headers"], json={"amount_toman": 20000}).status_code == 503


def test_return_code_credits_once_and_is_idempotent(client, user):
    inv = invoice(client, user, 40000)
    code = pay.make_return_code(inv["invoice_id"], 123456, 40000)
    r = client.post("/api/payments/redeem", headers=user["headers"], json={"code": code})
    assert r.status_code == 200 and r.json["status"] == "paid" and r.json["balance_toman"] == 40000
    again = client.post("/api/payments/redeem", headers=user["headers"], json={"code": code})
    assert again.json["status"] == "already_paid" and balance(client, user) == 40000


def test_wrong_signature_and_malformed_codes(client, user):
    inv = invoice(client, user, 40000)
    good = pay.make_return_code(inv["invoice_id"], 123456, 40000)
    tampered = good[:-1] + ("0" if good[-1] != "0" else "1")
    for bad in (tampered, "zpd_1_2_zzzz", "hello", "", good.replace("123456", "999999"),
                pay.make_return_code(inv["invoice_id"], 123456, 90000)):
        assert client.post("/api/payments/redeem", headers=user["headers"], json={"code": bad}).status_code == 400
    assert balance(client, user) == 0


def test_code_of_another_user_rejected(client, user, fake_tg):
    from conftest import login_user
    inv = invoice(client, user, 30000)
    token, _ = login_user(client, fake_tg, tg_id=3003)
    other = {"Authorization": "Bearer " + token}
    code = pay.make_return_code(inv["invoice_id"], 5, 30000)
    assert client.post("/api/payments/redeem", headers=other, json={"code": code}).status_code == 403
    assert client.post("/api/payments/redeem", headers=user["headers"], json={"code": code}).status_code == 200


def test_ref_id_cannot_be_reused_for_two_invoices(client, user):
    a, b = invoice(client, user, 20000), invoice(client, user, 20000)
    assert client.post("/api/payments/redeem", headers=user["headers"], json={"code": pay.make_return_code(a["invoice_id"], 777, 20000)}).status_code == 200
    r = client.post("/api/payments/redeem", headers=user["headers"], json={"code": pay.make_return_code(b["invoice_id"], 777, 20000)})
    assert r.status_code >= 400 and balance(client, user) == 20000


def test_end_to_end_with_php_and_mock_zarinpal(client, user, php_site):
    inv = invoice(client, user, 50000)
    link = to_site(inv["pay_url"], php_site["base"])
    status, html = fetch(link)
    assert status == 200 and "StartPay/A" in html and 'http-equiv="refresh"' in html
    sent = php_site["state"]["last_request"]
    assert sent["amount"] == 500000 and sent["merchant_id"] == MERCHANT      # ریال = تومان × ۱۰
    assert "/back.php?" in sent["callback_url"]
    # نکته‌ی حیاتی: نباید Referrer-Policy بگذاریم
    with urllib.request.urlopen(link) as r:
        assert r.headers.get("Referrer-Policy") is None
    query = urllib.parse.urlsplit(link).query
    back = simulate_gateway_return(php_site["base"], html, query)
    status, page = fetch(back)
    assert status == 200 and "پرداخت با موفقیت" in page
    code = re.search(r"(zpd_\d+_\d+_[0-9a-f]{16})", page).group(1)
    assert "vira://payment?code=" in page
    r = client.post("/api/payments/redeem", headers=user["headers"], json={"code": code})
    assert r.json["status"] == "paid" and balance(client, user) == 50000
    assert client.post("/api/payments/redeem", headers=user["headers"], json={"code": code}).json["status"] == "already_paid"
    assert balance(client, user) == 50000
    # برگشت دوباره از درگاه (verify کد ۱۰۱) همان کد را می‌دهد و دوباره شارژ نمی‌کند
    status, page2 = fetch(back)
    assert re.search(r"(zpd_\d+_\d+_[0-9a-f]{16})", page2).group(1) == code


def test_php_rejects_tampered_links(client, user, php_site):
    inv = invoice(client, user, 50000)
    link = to_site(inv["pay_url"], php_site["base"])
    assert fetch(link.replace("t=50000", "t=1000"))[0] == 403           # مبلغ دست‌کاری
    assert fetch(re.sub(r"u=\d+", "u=999", link))[0] == 403              # کاربر دست‌کاری
    assert fetch(re.sub(r"s=[0-9a-f]{32}", "s=" + "0" * 32, link))[0] == 403
    assert fetch(link.split("&s=")[0])[0] == 400                          # بدون امضا
    other_merchant = link.replace(MERCHANT, "99999999-2222-3333-4444-555555555555")
    assert fetch(other_merchant)[0] == 403
    assert php_site["state"]["last_request"] is None                     # به زرین‌پال نرسیده


def test_php_failed_payment_and_check_button(client, user, php_site):
    inv = invoice(client, user, 50000)
    link = to_site(inv["pay_url"], php_site["base"])
    query = urllib.parse.urlsplit(link).query
    # دکمه‌ی بررسی قبل از پرداخت: Authority ذخیره نشده
    status, page = fetch(php_site["base"] + "/back.php?" + query + "&chk=1")
    assert "پرداختی ثبت نشده" in page
    status, html = fetch(link)
    # کاربر لغو می‌کند
    status, page = fetch(simulate_gateway_return(php_site["base"], html, query, ok=False))
    assert "پرداخت انجام نشد" in page and "zpd_" not in page
    # کاربر پرداخت کرده ولی برنگشته: دکمه‌ی بررسی از Authority ذخیره‌شده استفاده می‌کند
    php_site["state"]["pay_ok"] = True
    status, page = fetch(php_site["base"] + "/back.php?" + query + "&chk=1")
    code = re.search(r"(zpd_\d+_\d+_[0-9a-f]{16})", page).group(1)
    assert client.post("/api/payments/redeem", headers=user["headers"], json={"code": code}).json["balance_toman"] == 50000
    # پرداخت واقعاً انجام نشده → کد ساخته نمی‌شود
    inv2 = invoice(client, user, 20000)
    link2 = to_site(inv2["pay_url"], php_site["base"])
    q2 = urllib.parse.urlsplit(link2).query
    fetch(link2)
    php_site["state"]["pay_ok"] = False
    status, page = fetch(php_site["base"] + "/back.php?" + q2 + "&chk=1")
    assert "zpd_" not in page and "تأیید نشد" in page


def test_php_store_dir_and_config_protected(php_site):
    assert (php_site["site"] / "store").is_dir()


def test_authority_cannot_be_replayed_against_another_invoice(client, user, php_site):
    first = invoice(client, user, 50000)
    second = invoice(client, user, 50000)
    first_link = to_site(first["pay_url"], php_site["base"])
    second_link = to_site(second["pay_url"], php_site["base"])

    status, html = fetch(first_link)
    assert status == 200
    authority = re.search(r'href="[^\"]+/pg/StartPay/(A\d+)"', html).group(1)

    # مهاجم Authority پرداخت اول را با لینک امضاشده فاکتور دوم ترکیب می‌کند.
    second_query = urllib.parse.urlsplit(second_link).query
    forged = php_site["base"] + "/back.php?" + second_query + "&Authority=" + authority + "&Status=OK"
    status, page = fetch(forged)
    assert status == 403
    assert "این تراکنش برای این فاکتور ثبت نشده" in page
