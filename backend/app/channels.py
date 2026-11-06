"""کانال‌ها و پست‌ها. انتشار با CHANNEL_BOT_TOKEN؛ مالکیت کانال از تلگرام بررسی می‌شود."""
from __future__ import annotations

import html
import re
import secrets
from datetime import datetime, timezone

from .config import env
from .db import audit, get_db, utc_now
from .security import ApiError
from . import tg

_REF = re.compile(r"^(?:https?://t\.me/)?(@?[A-Za-z0-9_]{5,32}|-100\d{5,15})/?$")


def _token() -> str:
    token = env("CHANNEL_BOT_TOKEN")
    if not token:
        raise ApiError(503, "ربات مدیریت کانال هنوز روی سرور پیکربندی نشده است.")
    return token


def _channel_dict(row) -> dict:
    return {"id": row["id"], "chat_id": row["chat_id"], "title": row["title"], "username": row["username"]}


def list_channels(user_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM channels WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
    return [_channel_dict(r) for r in rows]


def verify_channel(user: dict, chat_ref: str) -> dict:
    match = _REF.fullmatch(str(chat_ref or "").strip())
    if not match:
        raise ApiError(400, "یوزرنیم کانال را مثل @mychannel وارد کن.")
    ref = match.group(1)
    if not ref.startswith("-") and not ref.startswith("@"):
        ref = "@" + ref
    token = _token()
    try:
        chat = tg.call(token, "getChat", {"chat_id": ref})
        if chat.get("type") != "channel":
            raise ApiError(400, "این آدرس یک کانال نیست.")
        me = tg.call(token, "getMe")
        bot_member = tg.call(token, "getChatMember", {"chat_id": chat["id"], "user_id": me["id"]})
        if bot_member.get("status") != "administrator" or not bot_member.get("can_post_messages", False):
            raise ApiError(400, "ربات باید در کانال ادمین باشد و اجازه ارسال پیام داشته باشد.")
        # فقط ادمین/مالک واقعی کانال می‌تواند آن را به حساب خودش وصل کند.
        member = tg.call(token, "getChatMember", {"chat_id": chat["id"], "user_id": int(user["telegram_id"])})
        if member.get("status") not in ("creator", "administrator"):
            raise ApiError(403, "تو ادمین این کانال نیستی.")
    except tg.TgError as exc:
        raise ApiError(400, "بررسی کانال انجام نشد: {}".format(exc)) from None
    now = utc_now()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO channels(user_id,chat_id,title,username,created_at,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(user_id,chat_id) DO UPDATE SET title=excluded.title,username=excluded.username,"
            "updated_at=excluded.updated_at",
            (user["id"], str(chat["id"]), str(chat.get("title", ""))[:200], str(chat.get("username", ""))[:64], now, now))
        row = conn.execute("SELECT * FROM channels WHERE user_id=? AND chat_id=?", (user["id"], str(chat["id"]))).fetchone()
        audit(conn, "user:{}".format(user["id"]), "channel_connected", str(row["id"]))
    return _channel_dict(row)


def remove_channel(user_id: int, channel_id: int) -> None:
    with get_db() as conn:
        if conn.execute("DELETE FROM channels WHERE id=? AND user_id=?", (channel_id, user_id)).rowcount != 1:
            raise ApiError(404, "کانال پیدا نشد.")
        audit(conn, "user:{}".format(user_id), "channel_removed", str(channel_id))


def _own_channel(conn, user_id: int, channel_id: int):
    row = conn.execute("SELECT * FROM channels WHERE id=? AND user_id=?", (channel_id, user_id)).fetchone()
    if not row:
        raise ApiError(404, "کانال پیدا نشد.")
    return row


def _post_dict(row) -> dict:
    keys = ("id", "channel_id", "title", "body", "status", "scheduled_at", "published_at", "last_error", "created_at")
    return {k: row[k] for k in keys}


def list_posts(user_id: int, channel_id: int) -> list:
    with get_db() as conn:
        _own_channel(conn, user_id, channel_id)
        rows = conn.execute("SELECT * FROM channel_posts WHERE channel_id=? AND user_id=? "
                            "ORDER BY created_at DESC LIMIT 100", (channel_id, user_id)).fetchall()
    return [_post_dict(r) for r in rows]


def _parse_schedule(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "زمان‌بندی معتبر نیست.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        raise ApiError(400, "زمان انتشار باید در آینده باشد.")
    return parsed.isoformat(timespec="seconds")


def save_post(user_id: int, channel_id: int, data: dict) -> dict:
    title = " ".join(str(data.get("title") or "").split())[:120]
    body = str(data.get("body") or "").replace("\x00", "").strip()
    if not 1 <= len(body) <= 3800:
        raise ApiError(400, "متن پست باید بین ۱ تا ۳۸۰۰ نویسه باشد.")
    scheduled_at = _parse_schedule(data.get("scheduled_at"))
    status = "scheduled" if scheduled_at else "draft"
    post_id = secrets.token_hex(12)
    now = utc_now()
    with get_db() as conn:
        _own_channel(conn, user_id, channel_id)
        conn.execute(
            "INSERT INTO channel_posts(id,user_id,channel_id,title,body,status,scheduled_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)", (post_id, user_id, channel_id, title, body, status, scheduled_at, now, now))
        audit(conn, "user:{}".format(user_id), "post_saved", post_id)
        row = conn.execute("SELECT * FROM channel_posts WHERE id=?", (post_id,)).fetchone()
    return _post_dict(row)


def _send(post, chat_id: str) -> int:
    text = ("<b>{}</b>\n\n{}".format(html.escape(post["title"]), html.escape(post["body"]))
            if post["title"] else html.escape(post["body"]))
    result = tg.call(_token(), "sendMessage", {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
                                              "disable_web_page_preview": True})
    return int(result["message_id"])


def publish_post(user_id: int, post_id: str) -> dict:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        post = conn.execute("SELECT * FROM channel_posts WHERE id=? AND user_id=?", (post_id, user_id)).fetchone()
        if not post:
            raise ApiError(404, "پست پیدا نشد.")
        if post["status"] in ("published", "publishing"):
            raise ApiError(409, "این پست قبلاً منتشر شده یا در حال انتشار است.")
        channel = conn.execute("SELECT * FROM channels WHERE id=?", (post["channel_id"],)).fetchone()
        conn.execute("UPDATE channel_posts SET status='publishing',updated_at=? WHERE id=?", (utc_now(), post_id))
    try:
        message_id = _send(post, channel["chat_id"])
    except (tg.TgError, ApiError) as exc:
        message = exc.detail if isinstance(exc, ApiError) else str(exc)
        with get_db() as conn:
            conn.execute("UPDATE channel_posts SET status='failed',last_error=?,updated_at=? WHERE id=?",
                         (message[:300], utc_now(), post_id))
        raise ApiError(502, "انتشار انجام نشد: {}".format(message)) from None
    with get_db() as conn:
        conn.execute("UPDATE channel_posts SET status='published',published_at=?,message_id=?,last_error='',"
                     "updated_at=? WHERE id=?", (utc_now(), message_id, utc_now(), post_id))
        audit(conn, "user:{}".format(user_id), "post_published", post_id)
        row = conn.execute("SELECT * FROM channel_posts WHERE id=?", (post_id,)).fetchone()
    return _post_dict(row)


def delete_post(user_id: int, post_id: str) -> None:
    with get_db() as conn:
        row = conn.execute("SELECT status FROM channel_posts WHERE id=? AND user_id=?", (post_id, user_id)).fetchone()
        if not row:
            raise ApiError(404, "پست پیدا نشد.")
        if row["status"] == "published":
            raise ApiError(409, "پست منتشرشده از این‌جا حذف نمی‌شود.")
        conn.execute("DELETE FROM channel_posts WHERE id=?", (post_id,))


def run_due_posts(limit: int = 20) -> int:
    """برای cron هر دقیقه: پست‌های زمان‌بندی‌شده‌ی رسیده را منتشر می‌کند."""
    now = utc_now()
    with get_db() as conn:
        conn.execute("UPDATE channel_posts SET status='scheduled' WHERE status='publishing' AND updated_at<?",
                     (datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - 600, timezone.utc)
                      .isoformat(timespec="seconds"),))
        rows = conn.execute("SELECT id,user_id FROM channel_posts WHERE status='scheduled' AND scheduled_at<=? "
                            "ORDER BY scheduled_at LIMIT ?", (now, limit)).fetchall()
    done = 0
    for row in rows:
        try:
            publish_post(row["user_id"], row["id"])
            done += 1
        except ApiError:
            pass
    return done
