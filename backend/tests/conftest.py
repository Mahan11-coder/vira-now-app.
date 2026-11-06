import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PHP_DIR = BACKEND.parent / "php_payment"
sys.path.insert(0, str(BACKEND))

SECRET = "test-shared-secret-0123456789-abcdef"
MERCHANT = "11111111-2222-3333-4444-555555555555"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeTelegram:
    """Bot API تقلبی: همه فراخوانی‌ها ثبت می‌شود؛ پاسخ‌ها با responses قابل تنظیم است."""

    def __init__(self):
        self.calls = []
        self.responses = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                method = self.path.rsplit("/", 1)[-1]
                outer.calls.append((method, body))
                handler = outer.responses.get(method)
                result = handler(body) if callable(handler) else (handler if handler is not None else True)
                data = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:{}".format(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def sent(self, method):
        return [b for m, b in self.calls if m == method]


@pytest.fixture()
def fake_tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setenv("TELEGRAM_API_BASE", fake.url)
    yield fake
    fake.server.shutdown()


@pytest.fixture()
def app(tmp_path, monkeypatch, fake_tg):
    monkeypatch.setenv("VIRA_DB_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("AUTH_BOT_TOKEN", "111:AUTH")
    monkeypatch.setenv("AUTH_BOT_USERNAME", "vira_login_bot")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret-1234567890")
    monkeypatch.setenv("CHANNEL_BOT_TOKEN", "222:CHAN")
    monkeypatch.setenv("PAY_SHARED_SECRET", SECRET)
    monkeypatch.setenv("ZARINPAL_MERCHANT_ID", MERCHANT)
    monkeypatch.setenv("PAY_BASE_URL", "https://pay.example.ir/pay")
    monkeypatch.setenv("TRUST_PROXY_HOPS", "0")
    from app import create_app
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    flask_app.config["PROPAGATE_EXCEPTIONS"] = False
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin_headers(client):
    r = client.post("/api/admin/login", json={"password": "correct-horse-battery"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json["access_token"]}


def login_user(client, fake_tg, tg_id=1001, first="Ali", username="ali"):
    start = client.post("/api/auth/telegram/start").json
    code = start["bot_url"].split("start=")[1]
    hook = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret-1234567890"}
    client.post("/api/telegram/webhook", headers=hook, json={
        "message": {"chat": {"id": tg_id, "type": "private"}, "text": "/start " + code,
                    "from": {"id": tg_id}}})
    client.post("/api/telegram/webhook", headers=hook, json={
        "callback_query": {"id": "cb1", "data": "ok:" + code,
                           "from": {"id": tg_id, "first_name": first, "username": username},
                           "message": {"chat": {"id": tg_id}}}})
    status = client.post("/api/auth/telegram/status",
                         json={"session_id": start["session_id"], "poll_secret": start["poll_secret"]}).json
    assert status["status"] == "approved", status
    return status["access_token"], status["user"]


@pytest.fixture()
def user(client, fake_tg):
    token, info = login_user(client, fake_tg)
    return {"headers": {"Authorization": "Bearer " + token}, "info": info}


@pytest.fixture()
def php_site(tmp_path, monkeypatch):
    """هاست ایرانی شبیه‌سازی‌شده: php -S + زرین‌پال mock."""
    if subprocess.run(["which", "php"], capture_output=True).returncode != 0:
        pytest.skip("php not installed")
    modules = subprocess.run(["php", "-m"], capture_output=True, text=True).stdout.lower().splitlines()
    if "curl" not in modules:
        pytest.skip("PHP cURL extension not installed; PHP gateway integration tests require it")
    state = {"authority_n": 0, "verified": set(), "pay_ok": True, "last_request": None, "last_verify": None}

    class ZP(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if self.path.endswith("/request.json"):
                state["authority_n"] += 1
                state["last_request"] = body
                out = {"data": {"code": 100, "authority": "A{:035d}".format(state["authority_n"])}}
            elif self.path.endswith("/verify.json"):
                state["last_verify"] = body
                if state["pay_ok"] and body.get("authority"):
                    first = body["authority"] not in state["verified"]
                    state["verified"].add(body["authority"])
                    out = {"data": {"code": 100 if first else 101, "ref_id": 700000 + state["authority_n"]}}
                else:
                    out = {"data": {"code": -51}, "errors": []}
            else:
                out = {}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

    zp = ThreadingHTTPServer(("127.0.0.1", 0), ZP)
    threading.Thread(target=zp.serve_forever, daemon=True).start()
    zp_url = "http://127.0.0.1:{}".format(zp.server_address[1])

    site = tmp_path / "site"
    site.mkdir()
    for name in ("common.php", "pay.php", "back.php"):
        (site / name).write_text((PHP_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    (site / "store").mkdir()
    port = free_port()
    base = "http://127.0.0.1:{}".format(port)
    (site / "config.php").write_text(
        "<?php return ['SECRET'=>%r,'BASE_URL'=>%r,'ALLOW_HTTP'=>true,'ZP_BASE'=>%r,'MERCHANT_ID'=>%r,"
        "'RETURN_URL'=>'vira://payment?code={code}'];" % (SECRET, base, zp_url, MERCHANT), encoding="utf-8")
    proc = subprocess.Popen(["php", "-S", "127.0.0.1:{}".format(port), "-t", str(site)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/nonexistent.php", timeout=1)
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(0.1)
    yield {"base": base, "state": state, "site": site}
    proc.terminate()
    zp.shutdown()
