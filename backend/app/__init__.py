"""VIRA backend (Flask). همه مسیرها مطابق قرارداد APK فعلی هستند."""
from __future__ import annotations

import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, request

from . import ai, channels, payments, settings_store, subscriptions, tg
from .config import cors_origins, env, public_base_url
from .db import audit, db_path, get_db, init_db, utc_now
from .security import (ApiError, audit_event, client_ip, create_session, hash_password, public_user,
                       rate_limit, record_failure, require_admin, require_user, revoke_current_session,
                       safe_equal, sha256_hex, verify_password)

LOGIN_TTL_SEC = 600


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 128 * 1024
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False
    init_db()

    # ── امنیت پاسخ‌ها و CORS ─────────────────────────────────────────────────
    @app.after_request
    def add_headers(response):
        origin = request.headers.get("Origin", "")
        if origin and origin in cors_origins():
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Max-Age"] = "600"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    @app.before_request
    def preflight():
        if request.method == "OPTIONS":
            return ("", 204)

    @app.errorhandler(ApiError)
    def api_error(exc):
        return jsonify({"detail": exc.detail}), exc.status, exc.headers

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"detail": "مسیر پیدا نشد."}), 404

    @app.errorhandler(405)
    def bad_method(_):
        return jsonify({"detail": "روش درخواست مجاز نیست."}), 405

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"detail": "حجم درخواست بیش از حد مجاز است."}), 413

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception("unhandled error: %s", type(exc).__name__)
        return jsonify({"detail": "خطای داخلی سرور."}), 500

    # ── عمومی ────────────────────────────────────────────────────────────────
    @app.get("/health")
    def health():
        with get_db() as conn:
            conn.execute("SELECT 1")
        return jsonify({"ok": True, "time": utc_now()})

    @app.get("/api/public/config")
    def public_config():
        values = settings_store.get_all()
        values["auth_bot_username"] = settings_store.login_bot_username()
        values["telegram_login_ready"] = settings_store.telegram_login_ready()
        values["payments_ready"] = payments.pay_ready()
        return jsonify(values)

    @app.get("/api/public/services")
    def public_services():
        with get_db() as conn:
            rows = conn.execute("SELECT id,title,description,icon,url,sort_order FROM services "
                                "WHERE is_active=1 ORDER BY sort_order,id").fetchall()
        return jsonify([dict(r) for r in rows])

    # ── ورود با تلگرام ───────────────────────────────────────────────────────
    @app.post("/api/auth/telegram/start")
    def telegram_start():
        if not settings_store.telegram_login_ready():
            raise ApiError(503, "ورود با تلگرام هنوز روی سرور پیکربندی نشده است.")
        rate_limit("tgstart:" + client_ip(), 12, 600)
        session_id = secrets.token_urlsafe(16)
        poll_secret = secrets.token_urlsafe(32)
        code = secrets.token_urlsafe(24)  # حروف مجاز start در تلگرام: A-Za-z0-9_-
        expires = (_now_dt() + timedelta(seconds=LOGIN_TTL_SEC)).isoformat(timespec="seconds")
        with get_db() as conn:
            conn.execute("DELETE FROM login_sessions WHERE expires_at < ?",
                         ((_now_dt() - timedelta(hours=1)).isoformat(timespec="seconds"),))
            conn.execute("INSERT INTO login_sessions(id,secret_hash,code_hash,status,created_at,expires_at) "
                         "VALUES(?,?,?,'pending',?,?)",
                         (session_id, sha256_hex(poll_secret), sha256_hex(code), utc_now(), expires))
        bot = settings_store.login_bot_username()
        return jsonify({"session_id": session_id, "poll_secret": poll_secret,
                        "bot_url": "https://t.me/{}?start={}".format(bot, code), "expires_in": LOGIN_TTL_SEC})

    @app.post("/api/auth/telegram/status")
    def telegram_status():
        data = _json()
        session_id, secret = str(data.get("session_id") or "")[:64], str(data.get("poll_secret") or "")[:128]
        if not session_id or not secret:
            raise ApiError(400, "اطلاعات ورود ناقص است.")
        rate_limit("tgpoll:" + session_id, 90, 600)
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM login_sessions WHERE id=?", (session_id,)).fetchone()
            if not row or not safe_equal(row["secret_hash"], sha256_hex(secret)):
                raise ApiError(404, "درخواست ورود پیدا نشد.")
            if row["status"] == "consumed":
                return jsonify({"status": "expired"})
            if row["expires_at"] <= utc_now():
                return jsonify({"status": "expired"})
            if row["status"] != "approved":
                return jsonify({"status": row["status"] if row["status"] in ("pending", "denied") else "pending"})
            user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            conn.execute("UPDATE login_sessions SET status='consumed',consumed_at=? WHERE id=? AND status='approved'",
                         (utc_now(), session_id))
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (utc_now(), user["id"]))
            token = create_session(conn, "user", user["id"])
            audit(conn, "user:{}".format(user["id"]), "login_telegram", "")
            user = dict(conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone())
        return jsonify({"status": "approved", "approved": True, "access_token": token,
                        "user": public_user(user)})

    @app.post("/api/telegram/webhook")
    def telegram_webhook():
        secret = env("TELEGRAM_WEBHOOK_SECRET")
        if not secret or not hmac.compare_digest(
                request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").encode(), secret.encode()):
            raise ApiError(403, "دسترسی مجاز نیست.")
        update = _json()
        token = env("AUTH_BOT_TOKEN")
        try:
            _handle_update(update, token)
        except tg.TgError:
            pass
        return jsonify({"ok": True})

    def _handle_update(update: dict, token: str) -> None:
        message = update.get("message")
        callback = update.get("callback_query")
        if message:
            chat = message.get("chat") or {}
            text = str(message.get("text") or "")
            if chat.get("type") != "private":
                return
            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                code = parts[1].strip() if len(parts) == 2 else ""
                if not code or len(code) > 64:
                    tg.call(token, "sendMessage", {"chat_id": chat["id"],
                            "text": "برای ورود، از داخل برنامه VIRA دکمه «ورود با تلگرام» را بزن."})
                    return
                with get_db() as conn:
                    row = conn.execute("SELECT id,status,expires_at FROM login_sessions WHERE code_hash=?",
                                       (sha256_hex(code),)).fetchone()
                if not row or row["status"] != "pending" or row["expires_at"] <= utc_now():
                    tg.call(token, "sendMessage", {"chat_id": chat["id"],
                            "text": "این لینک ورود منقضی شده است. از برنامه دوباره شروع کن."})
                    return
                tg.call(token, "sendMessage", {
                    "chat_id": chat["id"],
                    "text": "آیا می‌خواهی وارد برنامه VIRA شوی؟\nاگر خودت این کار را شروع نکرده‌ای «لغو» را بزن.",
                    "reply_markup": {"inline_keyboard": [[
                        {"text": "✅ تأیید ورود", "callback_data": "ok:" + code},
                        {"text": "❌ لغو", "callback_data": "no:" + code}]]}})
        elif callback:
            data = str(callback.get("data") or "")
            sender = callback.get("from") or {}
            chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id")
            action, _, code = data.partition(":")
            if action not in ("ok", "no") or not code or sender.get("is_bot"):
                return
            with get_db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT id,status,expires_at FROM login_sessions WHERE code_hash=?",
                                   (sha256_hex(code),)).fetchone()
                ok = bool(row and row["status"] == "pending" and row["expires_at"] > utc_now())
                if ok and action == "ok":
                    tg_id = str(sender["id"])
                    now = utc_now()
                    conn.execute(
                        "INSERT INTO users(telegram_id,first_name,last_name,username,created_at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(telegram_id) DO UPDATE SET first_name=excluded.first_name,"
                        "last_name=excluded.last_name,username=excluded.username",
                        (tg_id, str(sender.get("first_name", ""))[:80], str(sender.get("last_name", ""))[:80],
                         str(sender.get("username", ""))[:64], now))
                    uid = conn.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()["id"]
                    conn.execute("UPDATE login_sessions SET status='approved',tg_user_id=?,user_id=? WHERE id=?",
                                 (tg_id, uid, row["id"]))
                elif ok:
                    conn.execute("UPDATE login_sessions SET status='denied' WHERE id=?", (row["id"],))
            answer = ("ورود تأیید شد؛ به برنامه برگرد." if ok and action == "ok"
                      else "ورود لغو شد." if ok else "این درخواست منقضی شده است.")
            try:
                tg.call(token, "answerCallbackQuery", {"callback_query_id": callback["id"]})
                if chat_id:
                    tg.call(token, "sendMessage", {"chat_id": chat_id, "text": answer})
            except tg.TgError:
                pass

    @app.get("/api/auth/me")
    @require_user
    def me():
        return jsonify(public_user(g.user))

    @app.post("/api/auth/logout")
    @require_user
    def logout():
        revoke_current_session()
        return jsonify({"ok": True})

    @app.delete("/api/auth/account")
    @require_user
    def delete_account():
        uid = g.user["id"]
        with get_db() as conn:
            audit(conn, "user:{}".format(uid), "account_deleted", "")
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
        return jsonify({"ok": True})

    # ── مدیر ─────────────────────────────────────────────────────────────────
    def _admin_hash() -> str:
        stored = env("ADMIN_PASSWORD_HASH")
        if stored:
            return stored
        plain = env("ADMIN_PASSWORD")
        return hash_password(plain) if len(plain) >= 12 else ""

    _admin_cache = {}

    def _admin_ready_hash() -> str:
        if "h" not in _admin_cache:
            _admin_cache["h"] = _admin_hash()
        return _admin_cache["h"]

    @app.post("/api/admin/login")
    def admin_login():
        ip = client_ip()
        rate_limit("adminfail:" + ip, 5, 900, count_only_failures=True)
        rate_limit("adminfail:global", 30, 900, count_only_failures=True)
        stored = _admin_ready_hash()
        if not stored:
            raise ApiError(503, "رمز مدیر روی سرور تنظیم نشده است.")
        password = str(_json().get("password") or "")[:200]
        if not verify_password(password, stored):
            record_failure("adminfail:" + ip)
            record_failure("adminfail:global")
            audit_event("admin", "admin_login_failed", ip)
            raise ApiError(401, "رمز مدیر درست نیست.")
        with get_db() as conn:
            token = create_session(conn, "admin", None)
            audit(conn, "admin", "admin_login", ip)
        return jsonify({"access_token": token, "expires_in": 8 * 3600})

    @app.get("/api/admin/overview")
    @require_admin
    def admin_overview():
        now = utc_now()
        with get_db() as conn:
            one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
            return jsonify({
                "users": one("SELECT COUNT(*) FROM users"),
                "active_sessions": one("SELECT COUNT(*) FROM sessions WHERE kind='user' AND revoked_at IS NULL AND expires_at>?", now),
                "services": one("SELECT COUNT(*) FROM services WHERE is_active=1"),
                "active_subscriptions": one("SELECT COUNT(*) FROM subscriptions WHERE status='active' AND (expires_at IS NULL OR expires_at>?)", now),
                "channels": one("SELECT COUNT(*) FROM channels"),
                "scheduled_posts": one("SELECT COUNT(*) FROM channel_posts WHERE status='scheduled'"),
            })

    @app.get("/api/admin/runtime")
    @require_admin
    def admin_runtime():
        return jsonify(settings_store.runtime_flags())

    @app.get("/api/admin/settings")
    @require_admin
    def admin_get_settings():
        return jsonify(settings_store.get_all())

    @app.put("/api/admin/settings")
    @require_admin
    def admin_put_settings():
        updates = _json().get("settings")
        if not isinstance(updates, dict):
            raise ApiError(400, "تنظیمات ارسال‌شده معتبر نیست.")
        changed = settings_store.save(updates)
        audit_event("admin", "settings_saved", ",".join(changed)[:250])
        return jsonify(settings_store.get_all())

    def _service_payload(data: dict) -> tuple:
        title = " ".join(str(data.get("title") or "").split())[:80]
        if not title:
            raise ApiError(400, "عنوان خدمت الزامی است.")
        description = str(data.get("description") or "").strip()[:400]
        icon = str(data.get("icon") or "✨").strip()[:8]
        url = settings_store.normalize("support_url", data.get("url") or "") if data.get("url") else ""
        try:
            order = max(0, min(100000, int(data.get("sort_order", 100))))
        except (TypeError, ValueError):
            raise ApiError(400, "ترتیب نمایش باید عدد باشد.") from None
        active = 1 if data.get("is_active", True) in (True, 1, "1", "true") else 0
        return title, description, icon, url, order, active

    @app.get("/api/admin/services")
    @require_admin
    def admin_services():
        with get_db() as conn:
            rows = conn.execute("SELECT id,title,description,icon,url,sort_order,is_active FROM services "
                                "ORDER BY sort_order,id").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/admin/services")
    @require_admin
    def admin_service_create():
        title, description, icon, url, order, active = _service_payload(_json())
        now = utc_now()
        with get_db() as conn:
            cur = conn.execute("INSERT INTO services(title,description,icon,url,sort_order,is_active,created_at,updated_at) "
                               "VALUES(?,?,?,?,?,?,?,?)", (title, description, icon, url, order, active, now, now))
            audit(conn, "admin", "service_created", str(cur.lastrowid))
            row = conn.execute("SELECT id,title,description,icon,url,sort_order,is_active FROM services WHERE id=?",
                               (cur.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201

    @app.put("/api/admin/services/<int:service_id>")
    @require_admin
    def admin_service_update(service_id):
        title, description, icon, url, order, active = _service_payload(_json())
        with get_db() as conn:
            if conn.execute("UPDATE services SET title=?,description=?,icon=?,url=?,sort_order=?,is_active=?,"
                            "updated_at=? WHERE id=?", (title, description, icon, url, order, active, utc_now(),
                                                        service_id)).rowcount != 1:
                raise ApiError(404, "خدمت پیدا نشد.")
            audit(conn, "admin", "service_updated", str(service_id))
            row = conn.execute("SELECT id,title,description,icon,url,sort_order,is_active FROM services WHERE id=?",
                               (service_id,)).fetchone()
        return jsonify(dict(row))

    @app.delete("/api/admin/services/<int:service_id>")
    @require_admin
    def admin_service_delete(service_id):
        with get_db() as conn:
            if conn.execute("DELETE FROM services WHERE id=?", (service_id,)).rowcount != 1:
                raise ApiError(404, "خدمت پیدا نشد.")
            audit(conn, "admin", "service_deleted", str(service_id))
        return jsonify({"ok": True})

    @app.get("/api/admin/users")
    @require_admin
    def admin_users():
        with get_db() as conn:
            rows = conn.execute("SELECT telegram_id,first_name,last_name,username,created_at,last_login_at "
                                "FROM users ORDER BY id DESC LIMIT 200").fetchall()
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return jsonify({"items": [dict(r) for r in rows], "total": total})

    @app.get("/api/admin/audit")
    @require_admin
    def admin_audit():
        try:
            limit = max(1, min(200, int(request.args.get("limit", 100))))
        except ValueError:
            limit = 100
        with get_db() as conn:
            rows = conn.execute("SELECT id,actor,action,detail,created_at FROM audit_log ORDER BY id DESC LIMIT ?",
                                (limit,)).fetchall()
        return jsonify({"items": [dict(r) for r in rows]})

    @app.post("/api/admin/backup")
    @require_admin
    def admin_backup():
        folder = db_path().parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        name = "vira-{}.sqlite3".format(datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
        target = folder / name
        source = sqlite3.connect(str(db_path()))
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        try:
            target.chmod(0o600)
        except OSError:
            pass
        for old in sorted(folder.glob("vira-*.sqlite3"))[:-14]:  # فقط ۱۴ نسخه‌ی آخر
            old.unlink(missing_ok=True)
        audit_event("admin", "backup_created", name)
        return jsonify({"ok": True, "file": name, "size_bytes": target.stat().st_size})

    # ── کانال‌ها ─────────────────────────────────────────────────────────────
    @app.get("/api/channels")
    @require_user
    def channels_list():
        return jsonify(channels.list_channels(g.user["id"]))

    @app.post("/api/channels/verify")
    @require_user
    def channels_verify():
        rate_limit("chverify:{}".format(g.user["id"]), 10, 600)
        return jsonify(channels.verify_channel(g.user, _json().get("chat_ref")))

    @app.delete("/api/channels/<int:channel_id>")
    @require_user
    def channels_remove(channel_id):
        channels.remove_channel(g.user["id"], channel_id)
        return jsonify({"ok": True})

    @app.get("/api/channels/<int:channel_id>/posts")
    @require_user
    def posts_list(channel_id):
        return jsonify(channels.list_posts(g.user["id"], channel_id))

    @app.post("/api/channels/<int:channel_id>/posts")
    @require_user
    def posts_create(channel_id):
        rate_limit("postsave:{}".format(g.user["id"]), 60, 3600)
        return jsonify(channels.save_post(g.user["id"], channel_id, _json())), 201

    @app.post("/api/channels/posts/<post_id>/publish")
    @require_user
    def posts_publish(post_id):
        rate_limit("postpub:{}".format(g.user["id"]), 30, 3600)
        return jsonify(channels.publish_post(g.user["id"], post_id[:40]))

    @app.delete("/api/channels/posts/<post_id>")
    @require_user
    def posts_delete(post_id):
        channels.delete_post(g.user["id"], post_id[:40])
        return jsonify({"ok": True})

    # ── هوش مصنوعی ───────────────────────────────────────────────────────────
    @app.post("/api/ai/generate")
    @require_user
    def ai_generate():
        rate_limit("ai:{}".format(g.user["id"]), 20, 600)
        return jsonify(ai.generate(g.user, _json(), settings_store.get_all()))

    # ── پرداخت ───────────────────────────────────────────────────────────────
    @app.post("/api/payments/invoices")
    @require_user
    def invoice_create():
        rate_limit("invoice:{}".format(g.user["id"]), 20, 3600)
        amount = _json().get("amount_toman")
        return jsonify(payments.create_invoice(g.user["id"], amount)), 201

    @app.get("/api/payments/invoices/<int:invoice_id>")
    @require_user
    def invoice_get(invoice_id):
        return jsonify(payments.get_invoice(invoice_id, g.user["id"]))

    @app.post("/api/payments/redeem")
    @require_user
    def payment_redeem():
        rate_limit("redeem:{}".format(g.user["id"]), 30, 600)
        return jsonify(payments.process_return_code(str(_json().get("code") or ""), g.user["id"]))

    # ── اشتراک و کیف پول ─────────────────────────────────────────────────────
    @app.get("/api/public/subscription-plans")
    def subscription_plans():
        return jsonify(subscriptions.list_plans())

    @app.get("/api/subscriptions")
    @require_user
    def subscriptions_list():
        return jsonify(subscriptions.list_user_subscriptions(g.user["id"]))

    @app.post("/api/subscriptions/purchase")
    @require_user
    def subscription_purchase():
        rate_limit("subpurchase:{}".format(g.user["id"]), 8, 3600)
        return jsonify(subscriptions.purchase(g.user["id"], _json().get("plan_code")))

    return app
