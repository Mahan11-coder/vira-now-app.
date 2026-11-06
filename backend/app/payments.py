"""درگاه پرداخت زرین‌پال به روش «لینک امضاشده».

سرور برنامه (خارج از ایران) هرگز به زرین‌پال یا هاست ایران وصل نمی‌شود:
  ۱) فاکتور می‌سازد و لینک امضاشده (pay.php) را به کاربر می‌دهد؛
  ۲) مرورگر کاربر (IP ایران) pay.php / back.php را روی هاست ایرانی باز می‌کند؛
  ۳) کاربر با کد امضاشده zpd_<id>_<ref>_<sig2> برمی‌گردد و این‌جا شارژ idempotent انجام می‌شود.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from urllib.parse import urlencode

from .config import env, env_int
from .db import audit, get_db, utc_now
from .security import ApiError, safe_equal

CODE_RE = re.compile(r"^zpd_(\d{1,12})_(\d{1,20})_([0-9a-f]{16})$")


def _secret() -> str:
    value = env("PAY_SHARED_SECRET")
    if len(value) < 24:
        raise ApiError(503, "درگاه پرداخت هنوز روی سرور تنظیم نشده است.")
    return value


def pay_ready() -> bool:
    return bool(env("PAY_BASE_URL") and env("ZARINPAL_MERCHANT_ID") and len(env("PAY_SHARED_SECRET")) >= 24)


def _hmac(text: str) -> str:
    return hmac.new(_secret().encode(), text.encode(), hashlib.sha256).hexdigest()


def link_signature(d, u, t, m, b, sb) -> str:
    return _hmac("{}|{}|{}|{}|{}|{}".format(d, u, t, m, b, sb))[:32]


def return_signature(invoice_id, ref_id, amount) -> str:
    return _hmac("ok|{}|{}|{}".format(invoice_id, ref_id, amount))[:16]


def make_return_code(invoice_id, ref_id, amount) -> str:  # برای تست/شبیه‌سازی
    return "zpd_{}_{}_{}".format(invoice_id, ref_id, return_signature(invoice_id, ref_id, amount))


def amount_limits() -> tuple:
    return env_int("PAY_MIN_TOMAN", 10000), env_int("PAY_MAX_TOMAN", 50000000)


def build_links(invoice_id: int, user_id: int, amount_toman: int) -> dict:
    merchant = env("ZARINPAL_MERCHANT_ID")
    app_id = env("PAY_APP_ID", "vira")
    sandbox = "1" if env("ZARINPAL_SANDBOX") in ("1", "true", "yes") else "0"
    sig = link_signature(invoice_id, user_id, amount_toman, merchant, app_id, sandbox)
    query = urlencode([("d", invoice_id), ("u", user_id), ("t", amount_toman), ("m", merchant),
                       ("b", app_id), ("sb", sandbox), ("s", sig)])
    base = env("PAY_BASE_URL").rstrip("/")
    return {"pay_url": "{}/pay.php?{}".format(base, query),
            "check_url": "{}/back.php?{}&chk=1".format(base, query)}


def create_invoice(user_id: int, amount_toman: int) -> dict:
    low, high = amount_limits()
    if not isinstance(amount_toman, int) or isinstance(amount_toman, bool) or not low <= amount_toman <= high:
        raise ApiError(400, "مبلغ باید بین {:,} و {:,} تومان باشد.".format(low, high))
    if not pay_ready():
        raise ApiError(503, "درگاه پرداخت هنوز روی سرور تنظیم نشده است.")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO payment_invoices(user_id,amount_toman,status,authority,created_at) "
            "VALUES(?,?,'pending','L',?)", (user_id, amount_toman, utc_now()))
        invoice_id = cur.lastrowid
        audit(conn, "user:{}".format(user_id), "invoice_created", "{}:{}".format(invoice_id, amount_toman))
    links = build_links(invoice_id, user_id, amount_toman)
    links.update({"invoice_id": invoice_id, "amount_toman": amount_toman, "status": "pending"})
    return links


def get_invoice(invoice_id: int, user_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT id,amount_toman,status,ref_id,created_at,paid_at FROM payment_invoices "
                           "WHERE id=? AND user_id=?", (invoice_id, user_id)).fetchone()
    if not row:
        raise ApiError(404, "فاکتور پیدا نشد.")
    data = dict(row)
    if data["status"] == "pending":
        data.update(build_links(data["id"], user_id, data["amount_toman"]))
    return data


def process_return_code(code: str, expected_user_id: int | None = None) -> dict:
    """کد بازگشت را بررسی و شارژ می‌کند. تکراری‌بودن کد دوباره شارژ نمی‌کند."""
    match = CODE_RE.fullmatch(str(code or "").strip())
    if not match:
        raise ApiError(400, "کد پرداخت معتبر نیست.")
    invoice_id, ref_id, sig = int(match.group(1)), match.group(2), match.group(3)
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        inv = conn.execute("SELECT * FROM payment_invoices WHERE id=?", (invoice_id,)).fetchone()
        if not inv or not safe_equal(sig, return_signature(invoice_id, ref_id, inv["amount_toman"])):
            raise ApiError(400, "کد پرداخت معتبر نیست.")
        if expected_user_id is not None and inv["user_id"] != expected_user_id:
            raise ApiError(403, "این پرداخت متعلق به حساب دیگری است.")
        if inv["status"] == "paid":
            balance = conn.execute("SELECT balance_toman FROM users WHERE id=?", (inv["user_id"],)).fetchone()
            return {"status": "already_paid", "invoice_id": invoice_id, "amount_toman": inv["amount_toman"],
                    "balance_toman": balance["balance_toman"] if balance else 0}
        updated = conn.execute(
            "UPDATE payment_invoices SET status='paid',ref_id=?,paid_at=? WHERE id=? AND status='pending'",
            (ref_id, utc_now(), invoice_id)).rowcount
        if updated != 1:
            raise ApiError(409, "وضعیت این فاکتور قابل تغییر نیست.")
        conn.execute("INSERT INTO wallet_ledger(user_id,invoice_id,amount_toman,kind,created_at) "
                     "VALUES(?,?,?,'topup',?)", (inv["user_id"], invoice_id, inv["amount_toman"], utc_now()))
        conn.execute("UPDATE users SET balance_toman=balance_toman+? WHERE id=?",
                     (inv["amount_toman"], inv["user_id"]))
        audit(conn, "user:{}".format(inv["user_id"]), "payment_paid", "{}:{}:{}".format(invoice_id, inv["amount_toman"], ref_id))
        balance = conn.execute("SELECT balance_toman FROM users WHERE id=?", (inv["user_id"],)).fetchone()
    return {"status": "paid", "invoice_id": invoice_id, "amount_toman": inv["amount_toman"],
            "balance_toman": balance["balance_toman"]}
