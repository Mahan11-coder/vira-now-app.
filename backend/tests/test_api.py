import sqlite3

from conftest import login_user


def test_health_and_public_config(client):
    assert client.get("/health").json["ok"] is True
    cfg = client.get("/api/public/config").json
    assert cfg["telegram_login_ready"] is True and cfg["auth_bot_username"] == "vira_login_bot"
    assert "AUTH_BOT_TOKEN" not in str(cfg) and "111:AUTH" not in str(cfg)


def test_security_headers_and_cors(client):
    r = client.get("/health", headers={"Origin": "https://localhost"})
    assert r.headers["Access-Control-Allow-Origin"] == "https://localhost"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    evil = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in evil.headers
    pre = client.open("/api/auth/me", method="OPTIONS", headers={"Origin": "https://localhost"})
    assert pre.status_code == 204 and "Authorization" in pre.headers["Access-Control-Allow-Headers"]


def test_unknown_route_and_errors_are_json(client):
    r = client.get("/nope")
    assert r.status_code == 404 and "detail" in r.json
    assert client.post("/api/admin/login", data="x" * 200000, content_type="application/json").status_code == 413


def test_admin_login_wrong_then_rate_limited(client):
    for _ in range(5):
        assert client.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    r = client.post("/api/admin/login", json={"password": "correct-horse-battery"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_admin_requires_token_and_user_token_is_not_admin(client, user):
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/api/admin/overview", headers=user["headers"]).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer va_fake"}).status_code == 401


def test_admin_not_configured(client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD")
    import app as pkg
    flask_app = pkg.create_app()
    r = flask_app.test_client().post("/api/admin/login", json={"password": "anything-long-enough"})
    assert r.status_code == 503


def test_settings_validation_and_save(client, admin_headers):
    r = client.put("/api/admin/settings", headers=admin_headers,
                   json={"settings": {"support_url": "javascript:alert(1)"}})
    assert r.status_code == 400
    r = client.put("/api/admin/settings", headers=admin_headers,
                   json={"settings": {"announcement_text": "سلام", "auth_bot_username": "@my_bot_x",
                                      "evil_key": "x", "free_ai_daily_limit": "9"}})
    assert r.status_code == 200
    assert r.json["announcement_text"] == "سلام" and r.json["auth_bot_username"] == "my_bot_x"
    assert "evil_key" not in r.json
    assert client.put("/api/admin/settings", headers=admin_headers, json={"settings": {"free_ai_daily_limit": "-3"}}).status_code == 400
    assert client.get("/api/public/config").json["announcement_text"] == "سلام"


def test_services_crud_and_public_list(client, admin_headers):
    r = client.post("/api/admin/services", headers=admin_headers,
                    json={"title": "ربات", "url": "https://t.me/x_bot_1", "sort_order": 2})
    assert r.status_code == 201
    sid = r.json["id"]
    client.post("/api/admin/services", headers=admin_headers, json={"title": "مخفی", "is_active": False})
    assert [s["title"] for s in client.get("/api/public/services").json] == ["ربات"]
    r = client.put("/api/admin/services/%d" % sid, headers=admin_headers,
                   json={"title": "ربات ۲", "url": "", "is_active": True})
    assert r.json["title"] == "ربات ۲"
    assert client.post("/api/admin/services", headers=admin_headers, json={"title": ""}).status_code == 400
    assert client.delete("/api/admin/services/%d" % sid, headers=admin_headers).status_code == 200
    assert client.delete("/api/admin/services/%d" % sid, headers=admin_headers).status_code == 404


def test_overview_runtime_users_audit_backup(client, admin_headers, user):
    ov = client.get("/api/admin/overview", headers=admin_headers).json
    assert ov["users"] == 1 and ov["active_sessions"] == 1
    rt = client.get("/api/admin/runtime", headers=admin_headers).json
    assert rt["telegram_login_ready"] is True and rt["channel_bot_ready"] is True and rt["ai_ready"] is False
    users = client.get("/api/admin/users", headers=admin_headers).json
    assert users["total"] == 1 and users["items"][0]["telegram_id"] == "1001"
    audit = client.get("/api/admin/audit?limit=100", headers=admin_headers).json["items"]
    assert any(a["action"] == "login_telegram" for a in audit)
    b = client.post("/api/admin/backup", headers=admin_headers).json
    assert b["ok"] and b["size_bytes"] > 0


def test_ai_not_configured_and_quota(client, user, monkeypatch, admin_headers):
    r = client.post("/api/ai/generate", headers=user["headers"], json={"topic": "سلام دنیا"})
    assert r.status_code == 503
    import app.ai as ai
    monkeypatch.setenv("AI_API_KEY", "k")
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return '{"choices":[{"message":{"content":"متن تولیدی"}}]}'.encode("utf-8")
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *a, **k: Resp())
    client.put("/api/admin/settings", headers=admin_headers, json={"settings": {"free_ai_daily_limit": "2"}})
    for _ in range(2):
        out = client.post("/api/ai/generate", headers=user["headers"], json={"topic": "یک موضوع خوب"})
        assert out.status_code == 200 and out.json["text"] == "متن تولیدی"
    assert client.post("/api/ai/generate", headers=user["headers"], json={"topic": "یک موضوع خوب"}).status_code == 429
    assert client.post("/api/ai/generate", headers=user["headers"], json={"topic": "x"}).status_code in (400, 429)
