from conftest import login_user

HOOK = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret-1234567890"}


def test_full_login_flow_and_token_is_one_time(client, fake_tg):
    start = client.post("/api/auth/telegram/start").json
    assert start["bot_url"].startswith("https://t.me/vira_login_bot?start=") and start["expires_in"] > 0
    code = start["bot_url"].split("start=")[1]
    body = {"session_id": start["session_id"], "poll_secret": start["poll_secret"]}
    assert client.post("/api/auth/telegram/status", json=body).json["status"] == "pending"
    client.post("/api/telegram/webhook", headers=HOOK, json={
        "message": {"chat": {"id": 5, "type": "private"}, "text": "/start " + code, "from": {"id": 5}}})
    kb = fake_tg.sent("sendMessage")[-1]["reply_markup"]["inline_keyboard"][0]
    assert kb[0]["callback_data"] == "ok:" + code
    assert client.post("/api/auth/telegram/status", json=body).json["status"] == "pending"  # هنوز تأیید نشده
    client.post("/api/telegram/webhook", headers=HOOK, json={
        "callback_query": {"id": "c", "data": "ok:" + code, "from": {"id": 5, "first_name": "سارا"},
                           "message": {"chat": {"id": 5}}}})
    done = client.post("/api/auth/telegram/status", json=body).json
    assert done["status"] == "approved" and done["user"]["telegram_id"] == "5"
    me = client.get("/api/auth/me", headers={"Authorization": "Bearer " + done["access_token"]})
    assert me.status_code == 200 and me.json["first_name"] == "سارا"
    assert client.post("/api/auth/telegram/status", json=body).json["status"] == "expired"  # توکن فقط یک بار


def test_wrong_poll_secret_and_webhook_secret(client, fake_tg):
    start = client.post("/api/auth/telegram/start").json
    r = client.post("/api/auth/telegram/status", json={"session_id": start["session_id"], "poll_secret": "x" * 20})
    assert r.status_code == 404
    assert client.post("/api/telegram/webhook", json={}).status_code == 403
    assert client.post("/api/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": "bad"}, json={}).status_code == 403


def test_deny_and_unknown_code(client, fake_tg):
    start = client.post("/api/auth/telegram/start").json
    code = start["bot_url"].split("start=")[1]
    client.post("/api/telegram/webhook", headers=HOOK, json={
        "callback_query": {"id": "c", "data": "no:" + code, "from": {"id": 9}, "message": {"chat": {"id": 9}}}})
    st = client.post("/api/auth/telegram/status", json={"session_id": start["session_id"], "poll_secret": start["poll_secret"]}).json
    assert st["status"] == "denied" and "access_token" not in st
    client.post("/api/telegram/webhook", headers=HOOK, json={
        "callback_query": {"id": "c", "data": "ok:" + code, "from": {"id": 9}, "message": {"chat": {"id": 9}}}})
    assert client.get("/api/admin/users").status_code == 401


def test_group_chat_and_bot_senders_ignored(client, fake_tg):
    start = client.post("/api/auth/telegram/start").json
    code = start["bot_url"].split("start=")[1]
    before = len(fake_tg.calls)
    client.post("/api/telegram/webhook", headers=HOOK, json={
        "message": {"chat": {"id": 1, "type": "group"}, "text": "/start " + code, "from": {"id": 1}}})
    client.post("/api/telegram/webhook", headers=HOOK, json={
        "callback_query": {"id": "c", "data": "ok:" + code, "from": {"id": 2, "is_bot": True}, "message": {"chat": {"id": 2}}}})
    assert len(fake_tg.calls) == before
    st = client.post("/api/auth/telegram/status", json={"session_id": start["session_id"], "poll_secret": start["poll_secret"]}).json
    assert st["status"] == "pending"


def test_logout_revokes_and_delete_account(client, fake_tg):
    token, _ = login_user(client, fake_tg, tg_id=77)
    h = {"Authorization": "Bearer " + token}
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    token2, _ = login_user(client, fake_tg, tg_id=77)
    h2 = {"Authorization": "Bearer " + token2}
    assert client.delete("/api/auth/account", headers=h2).status_code == 200
    assert client.get("/api/auth/me", headers=h2).status_code == 401


def test_login_start_rate_limited(client):
    codes = [client.post("/api/auth/telegram/start").status_code for _ in range(13)]
    assert codes[:12] == [200] * 12 and codes[12] == 429


def test_login_not_ready(client, monkeypatch):
    monkeypatch.delenv("AUTH_BOT_TOKEN")
    assert client.post("/api/auth/telegram/start").status_code == 503
