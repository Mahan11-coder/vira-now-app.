from datetime import datetime, timedelta, timezone

from conftest import login_user


def setup_tg(fake_tg, user_status="administrator", bot_can_post=True):
    fake_tg.responses["getChat"] = lambda b: {"id": -1001234567890, "type": "channel", "title": "کانال من", "username": "mychan"}
    fake_tg.responses["getMe"] = {"id": 222, "is_bot": True}

    def member(b):
        if b["user_id"] == 222:
            return {"status": "administrator", "can_post_messages": bot_can_post}
        return {"status": user_status}
    fake_tg.responses["getChatMember"] = member
    fake_tg.responses["sendMessage"] = {"message_id": 55}


def test_verify_requires_user_admin_of_channel(client, user, fake_tg):
    setup_tg(fake_tg, user_status="member")
    r = client.post("/api/channels/verify", headers=user["headers"], json={"chat_ref": "@mychan"})
    assert r.status_code == 403


def test_verify_requires_bot_admin(client, user, fake_tg):
    setup_tg(fake_tg, bot_can_post=False)
    assert client.post("/api/channels/verify", headers=user["headers"], json={"chat_ref": "@mychan"}).status_code == 400


def test_verify_bad_ref(client, user, fake_tg):
    setup_tg(fake_tg)
    assert client.post("/api/channels/verify", headers=user["headers"], json={"chat_ref": "http://evil/?x"}).status_code == 400


def test_channel_post_lifecycle(client, user, fake_tg):
    setup_tg(fake_tg)
    ch = client.post("/api/channels/verify", headers=user["headers"], json={"chat_ref": "@mychan"}).json
    assert ch["title"] == "کانال من"
    assert client.post("/api/channels/verify", headers=user["headers"], json={"chat_ref": "mychan"}).json["id"] == ch["id"]
    assert len(client.get("/api/channels", headers=user["headers"]).json) == 1
    post = client.post("/api/channels/%d/posts" % ch["id"], headers=user["headers"],
                       json={"title": "سلام <b>", "body": "متن & پست"}).json
    assert post["status"] == "draft"
    pub = client.post("/api/channels/posts/%s/publish" % post["id"], headers=user["headers"])
    assert pub.status_code == 200 and pub.json["status"] == "published"
    sent = fake_tg.sent("sendMessage")[-1]
    assert "&lt;b&gt;" in sent["text"] and "&amp;" in sent["text"]  # HTML escape شده
    assert client.post("/api/channels/posts/%s/publish" % post["id"], headers=user["headers"]).status_code == 409
    assert client.delete("/api/channels/posts/%s" % post["id"], headers=user["headers"]).status_code == 409


def test_other_user_cannot_touch_channel(client, user, fake_tg):
    setup_tg(fake_tg)
    ch = client.post("/api/channels/verify", headers=user["headers"], json={"chat_ref": "@mychan"}).json
    token, _ = login_user(client, fake_tg, tg_id=2002)
    other = {"Authorization": "Bearer " + token}
    assert client.get("/api/channels/%d/posts" % ch["id"], headers=other).status_code == 404
    assert client.delete("/api/channels/%d" % ch["id"], headers=other).status_code == 404
    assert client.post("/api/channels/%d/posts" % ch["id"], headers=other, json={"body": "x"}).status_code == 404


def test_failed_publish_then_retry_and_schedule(client, user, fake_tg):
    setup_tg(fake_tg)
    ch = client.post("/api/channels/verify", headers=user["headers"], json={"chat_ref": "@mychan"}).json
    post = client.post("/api/channels/%d/posts" % ch["id"], headers=user["headers"], json={"body": "hi"}).json
    fake_tg.responses["sendMessage"] = lambda b: (_ for _ in ()).throw(RuntimeError("x"))
    # تلگرام جعلی خطا نمی‌دهد؛ پس خطای ساختگی را از طریق monkeypatch ساده می‌سازیم
    import app.channels as chmod
    orig = chmod._send
    chmod._send = lambda p, c: (_ for _ in ()).throw(chmod.tg.TgError("chat not found"))
    r = client.post("/api/channels/posts/%s/publish" % post["id"], headers=user["headers"])
    chmod._send = orig
    assert r.status_code == 502
    posts = client.get("/api/channels/%d/posts" % ch["id"], headers=user["headers"]).json
    assert posts[0]["status"] == "failed" and "chat not found" in posts[0]["last_error"]
    fake_tg.responses["sendMessage"] = {"message_id": 9}
    assert client.post("/api/channels/posts/%s/publish" % post["id"], headers=user["headers"]).json["status"] == "published"
    # زمان‌بندی: گذشته رد می‌شود، آینده ثبت و با run_due_posts منتشر می‌شود
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert client.post("/api/channels/%d/posts" % ch["id"], headers=user["headers"], json={"body": "p", "scheduled_at": past}).status_code == 400
    soon = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    sch = client.post("/api/channels/%d/posts" % ch["id"], headers=user["headers"], json={"body": "later", "scheduled_at": soon}).json
    assert sch["status"] == "scheduled"
    import time
    time.sleep(1.5)
    assert chmod.run_due_posts() == 1
    assert chmod.run_due_posts() == 0
