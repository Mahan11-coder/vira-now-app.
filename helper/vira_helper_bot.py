#!/usr/bin/env python3
"""VIRA Helper — asynchronous Telegram long-polling bot.

Run this beside the VIRA backend and point it at the SAME SQLite database.
No MTProto user session, password, login code, or user-account token is used.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
load_dotenv(ROOT / ".env")

# ─────────────────────────────────────────────────────────────────────────────
# SET THESE TWO VALUES BEFORE STARTING THE BOT.
# Use a dedicated VIRA Helper bot token, NOT the login bot or channel-publisher token.
BOT_TOKEN = "PASTE_VIRA_HELPER_BOT_TOKEN_HERE"
ADMIN_IDS = {int(x.strip()) for x in __import__("os").getenv("VIRA_HELPER_ADMIN_IDS", "123456789").split(",") if x.strip().isdigit()}  # from .env
# ─────────────────────────────────────────────────────────────────────────────

TOKEN = os.getenv("VIRA_HELPER_BOT_TOKEN", "").strip() or BOT_TOKEN.strip()
_db_value = os.getenv("VIRA_DB_PATH", "").strip() or os.getenv("DATABASE_PATH", "").strip()
_db_candidate = Path(_db_value).expanduser() if _db_value else (BACKEND_DIR / "data" / "vira.sqlite3")
# Project-relative DATABASE_PATH values are resolved from backend/, matching the
# backend's documented working directory and default database location.
if _db_value and not _db_candidate.is_absolute():
    _db_candidate = BACKEND_DIR / _db_candidate
DB_PATH = _db_candidate.resolve()
os.environ["DATABASE_PATH"] = str(DB_PATH)
TIMEZONE_NAME = os.getenv("VIRA_TIMEZONE", "Asia/Tehran").strip() or "Asia/Tehran"
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    # Tehran has used a fixed UTC+03:30 offset since daylight-saving changes were discontinued.
    if TIMEZONE_NAME in {"Asia/Tehran", "Iran"}:
        LOCAL_TZ = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")
    else:
        LOCAL_TZ = timezone.utc
        TIMEZONE_NAME = "UTC"

sys.path.insert(0, str(BACKEND_DIR))
try:
    from app.db import audit, get_db, init_db, utc_now
except Exception as exc:  # fail early with a useful installation hint
    raise SystemExit(
        "VIRA backend package could not be imported. Run this file from the extracted VIRA_work "
        "folder and install backend/requirements.txt. Error: " + type(exc).__name__
    ) from exc

LOG_LEVEL = os.getenv("VIRA_HELPER_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("vira.helper")

MAX_DESTINATIONS_PER_SOURCE = 20
MAX_ARCHIVE_ITEMS_PER_TRANSFER = 500
BUILTIN_AD_TERMS = (
    "تبلیغ", "تبليغ", "تبلیغات", "خرید ممبر", "فروش ممبر", "افزایش ممبر",
    "ثبت سفارش", "سفارش تبلیغ", "تبلیغ رایگان", "کسب درآمد", "درآمد میلیونی",
    "ویژه تبلیغ", "پذیرش تبلیغ", "اسپانسر", "sponsored", "advertisement",
    "buy members", "promo code", "تبلیغاتی",
)
URL_RE = re.compile(r"(?:https?://|t\.me/|telegram\.me/|www\.)\S+", re.IGNORECASE)
CHAT_REF_RE = re.compile(r"^(?:@[A-Za-z0-9_]{5,32}|-100\d{6,20})$")


class BotAPIError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int = 0, parameters: dict | None = None):
        super().__init__(f"{method}: {description[:240]}")
        self.method = method
        self.description = description
        self.error_code = error_code
        self.parameters = parameters or {}


class TelegramAPI:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=8.0, read=42.0, write=15.0, pool=8.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=10),
        )

    async def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = await self.http.post(f"{self.base}/{method}", json=payload or {})
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BotAPIError(method, f"ارتباط تلگرام ناموفق بود ({type(exc).__name__})") from exc
        if not response.is_success or not isinstance(data, dict) or not data.get("ok"):
            description = data.get("description", "Telegram API error") if isinstance(data, dict) else "Invalid response"
            parameters = data.get("parameters") if isinstance(data, dict) else None
            raise BotAPIError(method, str(description), int(data.get("error_code", 0) or 0), parameters)
        return data.get("result")

    async def close(self) -> None:
        await self.http.aclose()


class ViraDB:
    """Short-lived SQLite connections; all calls are executed with asyncio.to_thread."""

    @staticmethod
    def _connect():
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH), timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def run(self, fn, *args):
        conn = self._connect()
        try:
            value = fn(conn, *args)
            conn.commit()
            return value
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _user(conn, telegram_id: int):
        return conn.execute("SELECT id, telegram_id, first_name FROM users WHERE telegram_id=?", (str(telegram_id),)).fetchone()

    def link_user(self, telegram_id: int):
        def op(c, tid):
            user = self._user(c, tid)
            if not user:
                return None
            c.execute(
                "INSERT INTO helper_bot_links(user_id,telegram_id,linked_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET telegram_id=excluded.telegram_id,linked_at=excluded.linked_at",
                (user["id"], str(tid), utc_now()),
            )
            return dict(user)
        return self.run(op, telegram_id)

    def user_for_telegram(self, telegram_id: int):
        return self.run(lambda c, tid: (dict(r) if (r := self._user(c, tid)) else None), telegram_id)

    def set_state(self, user_id: int, state: str = "", data: dict | None = None):
        self.run(lambda c, uid, st, d: c.execute(
            "INSERT INTO helper_user_state(user_id,state,data_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state,data_json=excluded.data_json,updated_at=excluded.updated_at",
            (uid, st, json.dumps(d or {}, ensure_ascii=False), utc_now()),
        ), user_id, state, data or {})

    def get_state(self, user_id: int):
        def op(c, uid):
            row = c.execute("SELECT state,data_json FROM helper_user_state WHERE user_id=?", (uid,)).fetchone()
            if not row:
                return {"state": "", "data": {}}
            try:
                data = json.loads(row["data_json"] or "{}")
            except json.JSONDecodeError:
                data = {}
            return {"state": row["state"], "data": data if isinstance(data, dict) else {}}
        return self.run(op, user_id)

    def channels(self, user_id: int):
        return self.run(lambda c, uid: [dict(r) for r in c.execute(
            "SELECT id,chat_id,title,username FROM channels WHERE user_id=? ORDER BY id DESC", (uid,)
        ).fetchall()], user_id)

    def add_channel(self, user_id: int, chat: dict):
        def op(c, uid, item):
            now = utc_now()
            c.execute(
                "INSERT INTO channels(user_id,chat_id,title,username,created_at,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(user_id,chat_id) DO UPDATE SET title=excluded.title,username=excluded.username,updated_at=excluded.updated_at",
                (uid, str(item["id"]), str(item.get("title") or item.get("username") or item["id"])[:120], str(item.get("username") or "")[:32], now, now),
            )
            row = c.execute("SELECT id,chat_id,title,username FROM channels WHERE user_id=? AND chat_id=?", (uid, str(item["id"]))).fetchone()
            audit(c, f"user:{uid}", "helper_channel_connected", str(item["id"]))
            return dict(row)
        return self.run(op, user_id, chat)

    def remove_channel(self, user_id: int, channel_id: int):
        return self.run(lambda c, uid, cid: c.execute("DELETE FROM channels WHERE id=? AND user_id=?", (cid, uid)).rowcount, user_id, channel_id)

    def routes(self, user_id: int):
        return self.run(lambda c, uid: [dict(r) for r in c.execute(
            "SELECT r.id,r.source_chat_id,r.source_title,r.mode,r.filter_ads,r.enabled,c.title AS destination_title,c.chat_id AS destination_chat_id,c.id AS destination_channel_id "
            "FROM channel_routes r JOIN channels c ON c.id=r.destination_channel_id WHERE r.user_id=? ORDER BY r.id DESC LIMIT 200", (uid,)
        ).fetchall()], user_id)

    def create_routes(self, user_id: int, source_id: str, source_title: str, destination_ids: list[int], mode: str, filter_ads: bool):
        def op(c, uid, sid, title, dids, md, filt):
            existing = c.execute("SELECT COUNT(*) FROM channel_routes WHERE user_id=? AND source_chat_id=? AND enabled=1", (uid, sid)).fetchone()[0]
            allowed = max(0, MAX_DESTINATIONS_PER_SOURCE - int(existing))
            picked = list(dict.fromkeys(int(x) for x in dids))[:allowed]
            now = utc_now()
            inserted = 0
            for dest in picked:
                # destination must belong to the same VIRA user
                owns = c.execute("SELECT 1 FROM channels WHERE id=? AND user_id=?", (dest, uid)).fetchone()
                if not owns:
                    continue
                cur = c.execute(
                    "INSERT OR IGNORE INTO channel_routes(user_id,source_chat_id,source_title,destination_channel_id,mode,filter_ads,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
                    (uid, sid, title[:120], dest, md, 1 if filt else 0, now, now),
                )
                inserted += cur.rowcount
            audit(c, f"user:{uid}", "helper_routes_created", f"{sid}:{inserted}")
            return {"created": inserted, "limit": MAX_DESTINATIONS_PER_SOURCE, "existing": int(existing)}
        return self.run(op, user_id, source_id, source_title, destination_ids, mode, filter_ads)

    def route_toggle(self, user_id: int, route_id: int, column: str):
        if column not in {"enabled", "filter_ads"}:
            return False
        return self.run(lambda c, uid, rid, col: c.execute(
            f"UPDATE channel_routes SET {col}=CASE WHEN {col}=1 THEN 0 ELSE 1 END,updated_at=? WHERE id=? AND user_id=?",
            (utc_now(), rid, uid),
        ).rowcount, user_id, route_id, column) > 0

    def route_delete(self, user_id: int, route_id: int):
        return self.run(lambda c, uid, rid: c.execute("DELETE FROM channel_routes WHERE id=? AND user_id=?", (rid, uid)).rowcount, user_id, route_id) > 0

    def get_routes_for_source(self, source_chat_id: str):
        return self.run(lambda c, sid: [dict(r) for r in c.execute(
            "SELECT r.*,c.chat_id AS destination_chat_id,c.id AS destination_id,c.title AS destination_title "
            "FROM channel_routes r JOIN channels c ON c.id=r.destination_channel_id WHERE r.source_chat_id=? AND r.enabled=1",
            (sid,),
        ).fetchall()], source_chat_id)

    def reserve_route_event(self, route_id: int, source_message_id: int, destination_channel_id: int):
        """Reserve an idempotency row; failed/incomplete items may be retried."""
        def op(c, rid, mid, dest):
            row = c.execute(
                "SELECT status FROM helper_route_events WHERE route_id=? AND source_message_id=?",
                (rid, mid),
            ).fetchone()
            if row and row["status"] == "sent":
                return False
            now = utc_now()
            if row:
                c.execute(
                    "UPDATE helper_route_events SET destination_channel_id=?,status='pending',last_error='',updated_at=? WHERE route_id=? AND source_message_id=?",
                    (dest, now, rid, mid),
                )
                return True
            c.execute(
                "INSERT INTO helper_route_events(route_id,source_message_id,destination_channel_id,status,created_at,updated_at) VALUES(?,?,?,'pending',?,?)",
                (rid, mid, dest, now, now),
            )
            return True
        return self.run(op, route_id, source_message_id, destination_channel_id)

    def record_route_result(self, user_id: int, route_id: int, source_message_id: int, destination_channel_id: int, dest_message_id: int | None, error: str = ""):
        def op(c, uid, rid, src_mid, channel_id, dest_mid, err):
            c.execute(
                "UPDATE helper_route_events SET status=?,destination_message_ids=?,last_error=?,updated_at=? WHERE route_id=? AND source_message_id=?",
                ("failed" if err else "sent", json.dumps([dest_mid] if dest_mid else []), err[:300], utc_now(), rid, src_mid),
            )
            if dest_mid:
                c.execute(
                    "INSERT OR IGNORE INTO helper_post_history(user_id,channel_id,message_id,source,created_at) VALUES(?,?,?,?,?)",
                    (uid, channel_id, dest_mid, "auto_copy", utc_now()),
                )
        self.run(op, user_id, route_id, source_message_id, destination_channel_id, dest_message_id, error)

    def subscription(self, user_id: int):
        return self.run(lambda c, uid: (dict(r) if (r := c.execute(
            "SELECT plan_code,status,expires_at FROM subscriptions WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
        ).fetchone()) else None), user_id)

    def schedule_post(self, user_id: int, destination_channel_id: int, source_chat_id: str, source_message_id: int, scheduled_at: str):
        post_id = uuid.uuid4().hex
        self.run(lambda c, uid, cid, chat, mid, when, pid: c.execute(
            "INSERT INTO helper_scheduled_posts(id,user_id,destination_channel_id,source_chat_id,source_message_id,scheduled_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'scheduled',?,?)",
            (pid, uid, cid, chat, mid, when, utc_now(), utc_now()),
        ), user_id, destination_channel_id, source_chat_id, source_message_id, scheduled_at, post_id)
        return post_id

    def recover_stale_schedules(self, stale_minutes: int = 10):
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat(timespec="seconds")
        return self.run(lambda c, before: c.execute(
            "UPDATE helper_scheduled_posts SET status='scheduled',updated_at=? WHERE status='publishing' AND updated_at<?",
            (utc_now(), before),
        ).rowcount, cutoff)

    def claim_due_schedules(self, limit: int = 10):
        def op(c, maximum):
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            rows = c.execute("SELECT * FROM helper_scheduled_posts WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at LIMIT ?", (now, maximum)).fetchall()
            claimed = []
            for r in rows:
                cur = c.execute("UPDATE helper_scheduled_posts SET status='publishing',updated_at=? WHERE id=? AND status='scheduled'", (utc_now(), r["id"]))
                if cur.rowcount:
                    claimed.append(dict(r))
            return claimed
        return self.run(op, limit)

    def finish_schedule(self, post_id: str, ok: bool, message_id: int | None = None, error: str = ""):
        def op(c, pid, success, mid, err):
            row = c.execute("SELECT user_id,destination_channel_id FROM helper_scheduled_posts WHERE id=?", (pid,)).fetchone()
            c.execute("UPDATE helper_scheduled_posts SET status=?,published_message_id=?,last_error=?,updated_at=? WHERE id=?",
                      ("published" if success else "failed", mid, err[:300], utc_now(), pid))
            if success and row and mid:
                c.execute("INSERT OR IGNORE INTO helper_post_history(user_id,channel_id,message_id,source,created_at) VALUES(?,?,?,?,?)",
                          (row["user_id"], row["destination_channel_id"], mid, "scheduled", utc_now()))
        self.run(op, post_id, ok, message_id, error)

    def add_archive_item(self, user_id: int, chat_id: int, message_id: int, media_group_id: str = ""):
        self.run(lambda c, uid, chat, mid, group: c.execute(
            "INSERT INTO helper_archive_items(user_id,private_chat_id,private_message_id,media_group_id,created_at,status) VALUES(?,?,?,?,?,'pending')",
            (uid, str(chat), mid, group, utc_now()),
        ), user_id, chat_id, message_id, media_group_id)

    def archive_items(self, user_id: int, limit: int = MAX_ARCHIVE_ITEMS_PER_TRANSFER):
        return self.run(lambda c, uid, maximum: [dict(r) for r in c.execute(
            "SELECT * FROM helper_archive_items WHERE user_id=? AND status='pending' ORDER BY id LIMIT ?", (uid, maximum)
        ).fetchall()], user_id, limit)

    def finish_archive_item(self, item_id: int, status: str = "sent"):
        self.run(lambda c, iid, st: c.execute("UPDATE helper_archive_items SET status=? WHERE id=?", (st, iid)), item_id, status)

    def clear_archive(self, user_id: int):
        return self.run(lambda c, uid: c.execute("DELETE FROM helper_archive_items WHERE user_id=? AND status='pending'", (uid,)).rowcount, user_id)

    def add_filter_rule(self, user_id: int, rule_type: str, value: str):
        self.run(lambda c, uid, kind, val: c.execute(
            "INSERT OR IGNORE INTO helper_filter_rules(user_id,rule_type,value,created_at) VALUES(?,?,?,?)",
            (uid, kind, val[:120], utc_now()),
        ), user_id, rule_type, value)

    def filter_rules(self, user_id: int):
        return self.run(lambda c, uid: [dict(r) for r in c.execute("SELECT rule_type,value FROM helper_filter_rules WHERE user_id=?", (uid,)).fetchall()], user_id)

    def delete_candidates(self, user_id: int, channel_id: int):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")
        return self.run(lambda c, uid, cid, cut: [dict(r) for r in c.execute(
            "SELECT message_id FROM helper_post_history WHERE user_id=? AND channel_id=? AND delete_status='active' AND created_at>=? ORDER BY message_id DESC LIMIT 10000",
            (uid, cid, cut),
        ).fetchall()], user_id, channel_id, cutoff)

    def mark_deleted(self, channel_id: int, message_ids: list[int]):
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        self.run(lambda c, cid, ids, sql: c.execute(sql, [cid, *ids]), channel_id, message_ids,
                 f"UPDATE helper_post_history SET delete_status='deleted' WHERE channel_id=? AND message_id IN ({placeholders})")

    def admin_stats(self):
        def op(c):
            return {
                "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "active_subscriptions": c.execute("SELECT COUNT(*) FROM subscriptions WHERE status='active'").fetchone()[0],
                "channels": c.execute("SELECT COUNT(*) FROM channels").fetchone()[0],
                "routes": c.execute("SELECT COUNT(*) FROM channel_routes WHERE enabled=1").fetchone()[0],
                "scheduled": c.execute("SELECT COUNT(*) FROM helper_scheduled_posts WHERE status='scheduled'").fetchone()[0],
                "pending_archive": c.execute("SELECT COUNT(*) FROM helper_archive_items WHERE status='pending'").fetchone()[0],
                "helper_users": c.execute("SELECT COUNT(*) FROM helper_bot_links").fetchone()[0],
            }
        return self.run(op)

    def admin_users(self, limit: int = 20):
        return self.run(lambda c, n: [dict(r) for r in c.execute(
            "SELECT u.telegram_id,u.first_name,u.username,u.created_at FROM users u JOIN helper_bot_links h ON h.user_id=u.id ORDER BY u.id DESC LIMIT ?", (n,)
        ).fetchall()], limit)

    def helper_recipients(self):
        return self.run(lambda c: [int(r[0]) for r in c.execute("SELECT telegram_id FROM helper_bot_links").fetchall() if str(r[0]).isdigit()])

    def save_offset(self, offset: int):
        self.run(lambda c, value: c.execute(
            "INSERT INTO helper_bot_meta(key,value,updated_at) VALUES('polling_offset',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (str(value), utc_now()),
        ), offset)

    def get_offset(self):
        return self.run(lambda c: int((r[0] if (r := c.execute("SELECT value FROM helper_bot_meta WHERE key='polling_offset'").fetchone()) else 0)))


DB = ViraDB()


def is_advertisement(text: str, extra_rules: list[str] | None = None) -> bool:
    value = (text or "").casefold()
    if not value:
        return False
    if any(term.casefold() in value for term in BUILTIN_AD_TERMS):
        return True
    # A link alone is not enough; treat obvious mass-promotion patterns as ads.
    if len(URL_RE.findall(value)) >= 2 and any(k in value for k in ("join", "subscribe", "عضو", "ثبت نام", "خرید", "لینک")):
        return True
    return any(rule.casefold() in value for rule in (extra_rules or []) if rule)


def parse_local_schedule(value: str) -> str:
    dt = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
    if dt <= datetime.now(LOCAL_TZ):
        raise ValueError("زمان باید در آینده باشد.")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def inline(rows: list[list[dict[str, str]]]) -> dict:
    return {"inline_keyboard": rows}


class ViraHelperBot:
    def __init__(self, api: TelegramAPI):
        self.api = api
        self.me: dict[str, Any] = {}
        self.album_buffer: dict[tuple[str, str], dict[str, Any]] = {}
        self.album_lock = asyncio.Lock()
        self.stop_event = asyncio.Event()

    async def db(self, method: str, *args):
        return await asyncio.to_thread(getattr(DB, method), *args)

    async def send(self, chat_id: int, text: str, keyboard: list[list[dict[str, str]]] | None = None):
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True}
        if keyboard:
            payload["reply_markup"] = inline(keyboard)
        return await self.api.call("sendMessage", payload)

    async def edit_or_send(self, chat_id: int, message_id: int, text: str, keyboard=None):
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text[:4096], "disable_web_page_preview": True}
        if keyboard:
            payload["reply_markup"] = inline(keyboard)
        try:
            return await self.api.call("editMessageText", payload)
        except BotAPIError:
            return await self.send(chat_id, text, keyboard)

    async def answer_callback(self, callback_id: str, text: str = ""):
        try:
            await self.api.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180], "show_alert": bool(text)})
        except BotAPIError:
            pass

    def home_keyboard(self, telegram_id: int):
        rows = [
            [{"text": "📡 کانال‌های من", "callback_data": "channels"}, {"text": "🔁 کپی خودکار", "callback_data": "routes"}],
            [{"text": "🗓 زمان‌بندی پست", "callback_data": "schedule"}, {"text": "🧊 دکمه‌ساز", "callback_data": "buttons"}],
            [{"text": "📦 انتقال آرشیو", "callback_data": "archive"}, {"text": "🧹 پاکسازی پست‌های VIRA", "callback_data": "cleanup"}],
            [{"text": "🛡 فیلتر تبلیغات", "callback_data": "filters"}, {"text": "🤖 دستیار هوشمند", "callback_data": "ai"}],
            [{"text": "💎 اشتراک من", "callback_data": "subscription"}, {"text": "💳 افزایش موجودی", "callback_data": "balance"}],
        ]
        if telegram_id in ADMIN_IDS:
            rows.append([{"text": "⚙️ پنل مدیریت", "callback_data": "admin"}])
        return rows

    async def require_user(self, chat_id: int, telegram_id: int):
        user = await self.db("link_user", telegram_id)
        if not user:
            await self.send(chat_id,
                "برای اتصال VIRA Helper، ابتدا با همین حساب تلگرام وارد اپلیکیشن VIRA شو.\n\n"
                "بعد دوباره /start را بزن. هیچ رمز، کد ورود یا Session تلگرام از تو دریافت نمی‌شود.")
            return None
        return user

    async def show_home(self, chat_id: int, telegram_id: int, user: dict):
        await self.db("set_state", user["id"], "", {})
        name = user.get("first_name") or "دوست من"
        await self.send(chat_id, f"سلام {name} 👋\nبه VIRA Helper خوش آمدی.\n\nمدیریت کانال، انتشار، انتقال محتوا و اشتراک تو به همان حساب VIRA متصل است.", self.home_keyboard(telegram_id))

    async def handle_message(self, message: dict):
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("type") != "private" or not isinstance(sender.get("id"), int):
            return
        chat_id, telegram_id = int(chat["id"]), int(sender["id"])
        text = str(message.get("text") or message.get("caption") or "").strip()
        if text in {"/start", "/menu", "منو"}:
            user = await self.require_user(chat_id, telegram_id)
            if user:
                await self.show_home(chat_id, telegram_id, user)
            return
        if text in {"/cancel", "لغو"}:
            user = await self.require_user(chat_id, telegram_id)
            if user:
                await self.db("set_state", user["id"], "", {})
                await self.send(chat_id, "عملیات لغو شد.", self.home_keyboard(telegram_id))
            return

        user = await self.require_user(chat_id, telegram_id)
        if not user:
            return
        uid = int(user["id"])
        session = await self.db("get_state", uid)
        state, data = session["state"], session["data"]

        if state == "add_channel":
            await self.add_channel_from_text(chat_id, uid, telegram_id, text)
            return
        if state == "route_source":
            await self.start_route_from_text(chat_id, uid, text)
            return
        if state == "schedule_content":
            await self.db("set_state", uid, "schedule_datetime", {
                "channel_id": data.get("channel_id"), "source_chat_id": str(chat_id), "source_message_id": int(message["message_id"])
            })
            await self.send(chat_id, f"🗓 زمان انتشار را به شکل زیر بفرست:\n`YYYY-MM-DD HH:MM`\n\nمنطقه زمانی: {TIMEZONE_NAME}\nمثال: `2026-11-05 18:30`\nبرای لغو: /cancel")
            return
        if state == "schedule_datetime":
            try:
                when = parse_local_schedule(text)
                post_id = await self.db("schedule_post", uid, int(data["channel_id"]), data["source_chat_id"], int(data["source_message_id"]), when)
            except (ValueError, KeyError) as exc:
                await self.send(chat_id, f"زمان معتبر نیست: {exc}\nفرمت درست: `YYYY-MM-DD HH:MM`")
                return
            await self.db("set_state", uid, "", {})
            await self.send(chat_id, f"✅ پست زمان‌بندی شد.\nشناسه: `{post_id[:10]}`\nزمان: {datetime.fromisoformat(when).astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')} ({TIMEZONE_NAME})", self.home_keyboard(telegram_id))
            return
        if state == "button_post":
            await self.db("set_state", uid, "button_label", {"source_chat_id": str(chat_id), "source_message_id": int(message["message_id"])})
            await self.send(chat_id, "متن دکمه را بفرست؛ مثلاً «ورود به سایت».")
            return
        if state == "button_label":
            if not text or len(text) > 50:
                await self.send(chat_id, "متن دکمه باید بین ۱ تا ۵۰ نویسه باشد.")
                return
            data["button_text"] = text
            await self.db("set_state", uid, "button_url", data)
            await self.send(chat_id, "حالا لینک کامل HTTPS را بفرست.")
            return
        if state == "button_url":
            if not re.fullmatch(r"https://[^\s]{5,500}", text, re.IGNORECASE):
                await self.send(chat_id, "لینک باید با https:// شروع شود و معتبر باشد.")
                return
            data["url"] = text
            channels = await self.db("channels", uid)
            if not channels:
                await self.db("set_state", uid, "", {})
                await self.send(chat_id, "ابتدا کانال مقصد را از بخش «کانال‌های من» اضافه کن.", self.home_keyboard(telegram_id))
                return
            await self.db("set_state", uid, "button_destination", data)
            await self.send(chat_id, "کانال مقصد را انتخاب کن:", self.channel_picker(channels, "button:dest:"))
            return
        if state == "archive_collect":
            await self.db("add_archive_item", uid, chat_id, int(message["message_id"]), str(message.get("media_group_id") or ""))
            items = await self.db("archive_items", uid)
            if len(items) >= MAX_ARCHIVE_ITEMS_PER_TRANSFER:
                await self.db("set_state", uid, "", {})
                await self.send(chat_id, f"به سقف هر نوبت ({MAX_ARCHIVE_ITEMS_PER_TRANSFER}) رسیدی. از منوی آرشیو مقصد را انتخاب کن.", self.home_keyboard(telegram_id))
            elif len(items) % 10 == 0:
                await self.send(chat_id, f"📦 {len(items)} پیام در آرشیو موقت ذخیره شد. پیام‌های بعدی را هم بفرست یا از منوی آرشیو «پایان دریافت» را بزن.")
            return
        if state == "filter_rule":
            if len(text) < 2 or len(text) > 120:
                await self.send(chat_id, "عبارت باید بین ۲ تا ۱۲۰ نویسه باشد.")
                return
            await self.db("add_filter_rule", uid, "keyword", text)
            await self.db("set_state", uid, "", {})
            await self.send(chat_id, "✅ عبارت به فهرست فیلتر شخصی اضافه شد.", self.home_keyboard(telegram_id))
            return
        if state == "ai_prompt":
            await self.db("set_state", uid, "", {})
            answer = await self.generate_ai(text)
            await self.send(chat_id, answer, self.home_keyboard(telegram_id))
            return
        if state == "delete_phrase":
            expected = str(data.get("phrase") or "")
            if text != expected:
                await self.send(chat_id, f"عبارت دقیق مطابقت ندارد. برای لغو /cancel\nعبارت موردنیاز: `{expected}`")
                return
            channel_id = int(data.get("channel_id") or 0)
            channel = next((x for x in await self.db("channels", uid) if int(x["id"]) == channel_id), None)
            if not channel:
                await self.db("set_state", uid, "", {})
                await self.send(chat_id, "کانال پیدا نشد؛ عملیات لغو شد.", self.home_keyboard(telegram_id))
                return
            candidates = await self.db("delete_candidates", uid, channel_id)
            ids = [int(x["message_id"]) for x in candidates]
            deleted = 0
            errors = 0
            for start in range(0, len(ids), 100):
                batch = ids[start:start + 100]
                try:
                    await self.api.call("deleteMessages", {"chat_id": channel["chat_id"], "message_ids": batch})
                    await self.db("mark_deleted", channel_id, batch)
                    deleted += len(batch)
                except BotAPIError as exc:
                    errors += len(batch)
                    log.warning("deleteMessages failed for channel %s: %s", channel["chat_id"], exc.description[:100])
                await asyncio.sleep(0.15)
            await self.db("set_state", uid, "", {})
            await self.send(chat_id, f"🧹 پایان پاکسازی پیام‌های ثبت‌شده توسط VIRA.\nدرخواست حذف: {len(ids)}\nحذف‌شده/تأییدشده: {deleted}\nناموفق یا نیازمند بررسی: {errors}\n\nپیام‌های قدیمی‌تر از ۴۸ ساعت و پیام‌هایی که توسط VIRA ثبت نشده‌اند قابل شناسایی/حذف در این عملیات نیستند.", self.home_keyboard(telegram_id))
            return
        if state == "admin_broadcast":
            if telegram_id not in ADMIN_IDS:
                await self.db("set_state", uid, "", {})
                return
            if not text:
                await self.send(chat_id, "ارسال همگانی فعلاً متن ساده می‌پذیرد.")
                return
            await self.db("set_state", uid, "admin_broadcast_confirm", {"text": text[:3500]})
            await self.send(chat_id, f"پیش‌نمایش پیام همگانی:\n\n{text[:3000]}\n\nارسال برای کاربران متصل انجام شود؟", [[{"text": "✅ تأیید ارسال", "callback_data": "admin:broadcast:confirm"}, {"text": "لغو", "callback_data": "admin:broadcast:cancel"}]])
            return
        if message.get("forward_origin") and state != "archive_collect":
            await self.send(chat_id, "برای واردکردن آرشیو، ابتدا از منوی «انتقال آرشیو» حالت دریافت را فعال کن؛ سپس پیام‌های موردنظر را به ربات فوروارد کن.")
        elif message.get("document") or message.get("photo") or message.get("video") or message.get("text"):
            await self.send(chat_id, "از منوی اصلی یک ابزار انتخاب کن یا /menu را بزن.", self.home_keyboard(telegram_id))

    def channel_picker(self, channels: list[dict], prefix: str):
        rows = [[{"text": (c["title"][:28] or str(c["chat_id"])), "callback_data": f"{prefix}{c['id']}"}] for c in channels[:20]]
        rows.append([{"text": "بازگشت", "callback_data": "home"}])
        return rows

    async def add_channel_from_text(self, chat_id: int, user_id: int, telegram_user_id: int, chat_ref: str):
        if not CHAT_REF_RE.fullmatch(chat_ref):
            await self.send(chat_id, "یوزرنیم عمومی مثل `@mychannel` یا شناسه عددی `-100...` را بفرست.\nربات باید در کانال ادمین و دارای مجوز انتشار باشد.")
            return
        try:
            chat = await self.api.call("getChat", {"chat_id": chat_ref})
            bot_member = await self.api.call("getChatMember", {"chat_id": chat["id"], "user_id": self.me["id"]})
            if bot_member.get("status") not in {"administrator", "creator"}:
                raise ValueError("ربات Helper هنوز ادمین کانال نیست.")
            if bot_member.get("status") != "creator" and bot_member.get("can_post_messages") is not True:
                raise ValueError("مجوز Post Messages برای ربات فعال نیست.")

            # فقط کاربری که همین حالا در تلگرام ادمین/مالک کانال است، می‌تواند کانال را به حسابش وصل کند.
            user_member = await self.api.call("getChatMember", {
                "chat_id": chat["id"], "user_id": int(telegram_user_id)
            })
            if user_member.get("status") not in {"administrator", "creator"}:
                raise ValueError("فقط مالک یا ادمین همان کانال می‌تواند آن را به حساب VIRA متصل کند.")

            row = await self.db("add_channel", user_id, chat)
            await self.db("set_state", user_id, "", {})
            await self.send(chat_id, f"✅ کانال «{row['title']}» به حساب VIRA متصل شد.\nاین اتصال در اپلیکیشن هم قابل مشاهده است.", [[{"text": "منوی اصلی", "callback_data": "home"}]])
        except (BotAPIError, ValueError) as exc:
            await self.send(chat_id, f"اتصال کانال انجام نشد: {str(exc)[:300]}")

    async def start_route_from_text(self, chat_id: int, user_id: int, chat_ref: str):
        if not CHAT_REF_RE.fullmatch(chat_ref):
            await self.send(chat_id, "شناسه کانال مبدا را به شکل `@username` یا `-100...` بفرست.")
            return
        try:
            chat = await self.api.call("getChat", {"chat_id": chat_ref})
            member = await self.api.call("getChatMember", {"chat_id": chat["id"], "user_id": self.me["id"]})
            if member.get("status") not in {"administrator", "creator", "member"}:
                raise ValueError("ابتدا ربات Helper را به کانال مبدا اضافه کن (عضویت کافی است؛ ادمین‌کردن فقط در مقصد لازم است).")
            channels = await self.db("channels", user_id)
            if not channels:
                raise ValueError("ابتدا حداقل یک کانال مقصد را از بخش کانال‌های من اضافه کن.")
            await self.db("set_state", user_id, "route_dest_select", {
                "source_chat_id": str(chat["id"]), "source_title": str(chat.get("title") or chat.get("username") or chat["id"]),
                "destination_ids": [], "mode": "copy", "filter_ads": True,
            })
            await self.send(chat_id, f"مبدا: {chat.get('title') or chat_ref}\nتا ۲۰ کانال مقصد انتخاب کن. ربات باید در همه مقصدها ادمین با مجوز انتشار باشد.", self.route_destination_keyboard(channels, []))
        except (BotAPIError, ValueError) as exc:
            await self.send(chat_id, f"ساخت مسیر انجام نشد: {str(exc)[:300]}")

    def route_destination_keyboard(self, channels, selected):
        rows = []
        for c in channels[:20]:
            tick = "✅ " if int(c["id"]) in selected else "▫️ "
            rows.append([{"text": tick + str(c["title"])[:30], "callback_data": f"route:dest:{c['id']}"}])
        rows.append([{"text": "حالت: تغییر کپی/فوروارد", "callback_data": "route:mode"}, {"text": "تغییر فیلتر", "callback_data": "route:filter_setting"}])
        rows.append([{"text": "✅ ذخیره مسیرها", "callback_data": "route:save"}, {"text": "لغو", "callback_data": "home"}])
        return rows

    async def handle_callback(self, query: dict):
        sender = query.get("from") or {}
        msg = query.get("message") or {}
        chat = msg.get("chat") or {}
        callback_id = str(query.get("id") or "")
        if chat.get("type") != "private" or not isinstance(sender.get("id"), int):
            await self.answer_callback(callback_id, "این عملیات فقط در گفت‌وگوی خصوصی قابل انجام است.")
            return
        telegram_id, chat_id = int(sender["id"]), int(chat["id"])
        user = await self.require_user(chat_id, telegram_id)
        if not user:
            await self.answer_callback(callback_id)
            return
        uid = int(user["id"])
        action = str(query.get("data") or "home")
        await self.answer_callback(callback_id)

        if action == "home":
            await self.show_home(chat_id, telegram_id, user); return
        if action == "channels":
            channels = await self.db("channels", uid)
            lines = [f"• {x['title']} — `{x['chat_id']}`" for x in channels]
            await self.send(chat_id, "📡 کانال‌های متصل به همان حساب اپلیکیشن:\n\n" + ("\n".join(lines) if lines else "هنوز کانالی ثبت نشده."), [[{"text": "➕ افزودن کانال", "callback_data": "channel:add"}], [{"text": "منوی اصلی", "callback_data": "home"}]])
            return
        if action == "channel:add":
            await self.db("set_state", uid, "add_channel", {})
            await self.send(chat_id, "یوزرنیم عمومی کانال یا شناسه عددی `-100...` را بفرست.\nربات Helper باید در کانال ادمین و دارای مجوز ارسال پست باشد.\nلغو: /cancel")
            return
        if action == "routes":
            routes = await self.db("routes", uid)
            if not routes:
                text = "هنوز مسیر کپی فعالی نداری.\nبرای دریافت پست‌های جدید، ربات Helper باید عضو کانال مبدا و ادمین کانال مقصد باشد."
            else:
                text = "🔁 مسیرهای کپی تو:\n\n" + "\n".join(
                    f"#{r['id']} | {r['source_title']} ➜ {r['destination_title']} | {'فعال' if r['enabled'] else 'متوقف'} | {'فیلتر روشن' if r['filter_ads'] else 'فیلتر خاموش'} | {r['mode']}"
                    for r in routes[:25]
                )
            rows = [[{"text": "➕ ساخت مسیر جدید", "callback_data": "route:add"}]]
            for r in routes[:10]:
                rows.append([{"text": f"#{r['id']} {'⏸ توقف' if r['enabled'] else '▶️ فعال'}", "callback_data": f"route:toggle:{r['id']}"}, {"text": "فیلتر", "callback_data": f"route:filter:{r['id']}"}, {"text": "حذف", "callback_data": f"route:delete:{r['id']}"}])
            rows.append([{"text": "منوی اصلی", "callback_data": "home"}])
            await self.send(chat_id, text, rows); return
        if action == "route:add":
            await self.db("set_state", uid, "route_source", {})
            await self.send(chat_id, "شناسه یا یوزرنیم کانال مبدا را بفرست.\nربات باید عضو کانال مبدا باشد. برای کانال خصوصی، ربات را با لینک دعوت اضافه کن.\nلغو: /cancel")
            return
        if action.startswith("route:dest:"):
            state = await self.db("get_state", uid)
            if state["state"] != "route_dest_select":
                await self.send(chat_id, "انتخاب مقصد منقضی شده؛ دوباره مسیر بساز."); return
            channel_id = int(action.rsplit(":", 1)[1])
            selected = [int(x) for x in state["data"].get("destination_ids", [])]
            if channel_id in selected:
                selected.remove(channel_id)
            elif len(selected) < MAX_DESTINATIONS_PER_SOURCE:
                selected.append(channel_id)
            state["data"]["destination_ids"] = selected
            await self.db("set_state", uid, state["state"], state["data"])
            await self.edit_or_send(chat_id, int(msg["message_id"]), f"مقصدهای انتخاب‌شده: {len(selected)}/20", self.route_destination_keyboard(await self.db("channels", uid), selected))
            return
        if action in {"route:mode", "route:filter_setting"}:
            state = await self.db("get_state", uid)
            if state["state"] != "route_dest_select":
                await self.send(chat_id, "تنظیم مسیر منقضی شده است؛ دوباره مسیر بساز."); return
            d = state["data"]
            if action == "route:mode":
                d["mode"] = "forward" if d.get("mode") == "copy" else "copy"
            else:
                d["filter_ads"] = not bool(d.get("filter_ads", True))
            await self.db("set_state", uid, state["state"], d)
            description = f"حالت: {'کپی بدون نمایش مبدا' if d['mode']=='copy' else 'فوروارد با نمایش مبدا'}\nفیلتر تبلیغات: {'روشن' if d['filter_ads'] else 'خاموش'}\nمقصد انتخاب‌شده: {len(d.get('destination_ids', []))}/20"
            await self.edit_or_send(chat_id, int(msg["message_id"]), description, self.route_destination_keyboard(await self.db("channels", uid), d.get("destination_ids", [])))
            return
        if action == "route:save":
            state = await self.db("get_state", uid)
            if state["state"] != "route_dest_select" or not state["data"].get("destination_ids"):
                await self.send(chat_id, "حداقل یک مقصد انتخاب کن."); return
            data = state["data"]
            # Verify Helper can publish to every selected target before persisting.
            channels = await self.db("channels", uid)
            good = []
            for cid in data["destination_ids"]:
                ch = next((x for x in channels if int(x["id"]) == int(cid)), None)
                if not ch: continue
                try:
                    member = await self.api.call("getChatMember", {"chat_id": ch["chat_id"], "user_id": self.me["id"]})
                    if member.get("status") in {"administrator", "creator"} and (member.get("status") == "creator" or member.get("can_post_messages") is True):
                        good.append(int(cid))
                except BotAPIError:
                    continue
            if len(good) != len(data["destination_ids"]):
                await self.send(chat_id, "همه مقصدها مجوز انتشار برای Helper ندارند. ربات را در هر مقصد ادمین کن و دوباره ذخیره را بزن.")
                return
            result = await self.db("create_routes", uid, data["source_chat_id"], data["source_title"], good, data.get("mode", "copy"), bool(data.get("filter_ads", True)))
            await self.db("set_state", uid, "", {})
            await self.send(chat_id, f"✅ {result['created']} مسیر ذخیره شد.\nهر پست جدیدی که ربات در مبدا دریافت کند، طبق تنظیمات به مقصدها می‌فرستد.\nمحدودیت: حداکثر ۲۰ مقصد برای هر مبدا.", self.home_keyboard(telegram_id))
            return
        if action.startswith("route:toggle:") or action.startswith("route:filter:") or action.startswith("route:delete:"):
            parts = action.split(":")
            rid = int(parts[-1])
            if parts[1] == "toggle": ok = await self.db("route_toggle", uid, rid, "enabled")
            elif parts[1] == "filter": ok = await self.db("route_toggle", uid, rid, "filter_ads")
            else: ok = await self.db("route_delete", uid, rid)
            await self.send(chat_id, "✅ تنظیم مسیر به‌روزرسانی شد." if ok else "مسیر پیدا نشد.", [[{"text": "مشاهده مسیرها", "callback_data": "routes"}], [{"text": "منوی اصلی", "callback_data": "home"}]])
            return
        if action == "schedule":
            channels = await self.db("channels", uid)
            if not channels:
                await self.send(chat_id, "ابتدا کانال مقصد را اضافه کن.", self.home_keyboard(telegram_id)); return
            await self.send(chat_id, "کانال مقصد را برای زمان‌بندی انتخاب کن:", self.channel_picker(channels, "schedule:channel:")); return
        if action.startswith("schedule:channel:"):
            cid = int(action.rsplit(":", 1)[1])
            if not any(int(x["id"]) == cid for x in await self.db("channels", uid)):
                await self.send(chat_id, "کانال معتبر نیست."); return
            await self.db("set_state", uid, "schedule_content", {"channel_id": cid})
            await self.send(chat_id, "حالا پست را بفرست یا فوروارد کن. متن، عکس، ویدیو و فایل پشتیبانی می‌شود؛ برای آلبوم، هر عضو آلبوم را جداگانه بفرست.")
            return
        if action == "buttons":
            await self.db("set_state", uid, "button_post", {})
            await self.send(chat_id, "پستی را که می‌خواهی زیر آن دکمه Inline قرار بگیرد بفرست (متن، عکس، ویدیو یا فایل). سپس متن دکمه و لینک را می‌گیریم.")
            return
        if action.startswith("button:dest:"):
            state = await self.db("get_state", uid)
            cid = int(action.rsplit(":", 1)[1])
            channel = next((x for x in await self.db("channels", uid) if int(x["id"]) == cid), None)
            if state["state"] != "button_destination" or not channel:
                await self.send(chat_id, "اطلاعات پست یا مقصد معتبر نیست."); return
            markup = {"inline_keyboard": [[{"text": state["data"]["button_text"], "url": state["data"]["url"]}]]}
            try:
                result = await self.api.call("copyMessage", {"chat_id": channel["chat_id"], "from_chat_id": state["data"]["source_chat_id"], "message_id": int(state["data"]["source_message_id"]), "reply_markup": markup})
                await self.db("record_route_result", uid, -1, 0, cid, int(result["message_id"])) if False else asyncio.sleep(0)
                # Record an app-visible deletion handle for VIRA-managed posts.
                await self.track_history(uid, cid, int(result["message_id"]), "button_builder")
                await self.db("set_state", uid, "", {})
                await self.send(chat_id, "✅ پست همراه دکمه در کانال منتشر شد. دکمه به شکل Inline تلگرام است؛ ظاهر دقیق آن را خود تلگرام تعیین می‌کند.", self.home_keyboard(telegram_id))
            except BotAPIError as exc:
                await self.send(chat_id, f"انتشار ناموفق بود: {exc.description[:250]}")
            return
        if action == "archive":
            await self.db("set_state", uid, "archive_collect", {})
            await self.send(chat_id, f"📦 حالت دریافت آرشیو فعال شد.\nپست‌های قدیمی را در همین گفت‌وگو به ربات فوروارد کن. تا {MAX_ARCHIVE_ITEMS_PER_TRANSFER} پیام در هر نوبت نگه می‌داریم.\nوقتی تمام شد، «پایان دریافت» را بزن.\n\nنکته: این روش نیازمند این است که خودت پیام‌های آرشیو را به ربات بفرستی؛ Bot API امکان واکشی کامل تاریخچه کانال را نمی‌دهد.", [[{"text": "پایان دریافت و انتخاب مقصد", "callback_data": "archive:finish"}, {"text": "پاک‌کردن آرشیو موقت", "callback_data": "archive:clear"}], [{"text": "لغو", "callback_data": "home"}]])
            return
        if action == "archive:finish":
            items = await self.db("archive_items", uid)
            await self.db("set_state", uid, "", {})
            if not items:
                await self.send(chat_id, "هنوز پیامی برای انتقال دریافت نشده.", self.home_keyboard(telegram_id)); return
            channels = await self.db("channels", uid)
            if not channels:
                await self.send(chat_id, "برای انتقال آرشیو ابتدا یک کانال مقصد در بخش کانال‌های من متصل کن.", self.home_keyboard(telegram_id)); return
            await self.send(chat_id, f"📦 {len(items)} پیام در صف آرشیو است. مقصد را انتخاب کن:", self.channel_picker(channels, "archive:dest:")); return
        if action == "archive:clear":
            count = await self.db("clear_archive", uid)
            await self.db("set_state", uid, "", {})
            await self.send(chat_id, f"{count} پیام موقت پاک شد.", self.home_keyboard(telegram_id)); return
        if action.startswith("archive:dest:"):
            cid = int(action.rsplit(":", 1)[1])
            channel = next((x for x in await self.db("channels", uid) if int(x["id"]) == cid), None)
            items = await self.db("archive_items", uid)
            if not channel or not items:
                await self.send(chat_id, "مقصد یا آرشیو پیدا نشد."); return
            await self.send(chat_id, f"انتقال {len(items)} پیام به «{channel['title']}» شروع شد. ممکن است چند دقیقه طول بکشد.")
            await self.transfer_archive(chat_id, telegram_id, uid, channel, items)
            return
        if action == "cleanup":
            channels = await self.db("channels", uid)
            if not channels:
                await self.send(chat_id, "کانالی ثبت نشده."); return
            await self.send(chat_id, "⚠️ این بخش فقط پیام‌هایی را حذف می‌کند که Helper/VIRA قبلاً شناسه آن‌ها را ثبت کرده و کمتر از ۴۸ ساعت از ارسالشان گذشته باشد. کل تاریخچه کانال قابل پاک‌کردن نیست. کانال را انتخاب کن:", self.channel_picker(channels, "delete:start:")); return
        if action.startswith("delete:start:"):
            cid = int(action.rsplit(":", 1)[1])
            channel = next((x for x in await self.db("channels", uid) if int(x["id"]) == cid), None)
            if not channel: return
            items = await self.db("delete_candidates", uid, cid)
            phrase = f"پاکسازی ویرا {cid}"
            await self.db("set_state", uid, "delete_step1", {"channel_id": cid, "phrase": phrase, "count": len(items)})
            await self.send(chat_id, f"تأیید اول از ۳\nکانال: {channel['title']}\nپیام‌های ثبت‌شده و کمتر از ۴۸ ساعت: {len(items)}\n\nحذف غیرقابل‌بازگشت است.", [[{"text": "ادامه مرحله ۲", "callback_data": "delete:confirm:1"}, {"text": "لغو", "callback_data": "home"}]])
            return
        if action.startswith("delete:confirm:"):
            state = await self.db("get_state", uid)
            step = int(action.rsplit(":", 1)[1])
            expected_state = f"delete_step{step}"
            if state["state"] != expected_state:
                await self.send(chat_id, "مرحله تأیید معتبر نیست یا منقضی شده."); return
            data = state["data"]
            if step == 1:
                await self.db("set_state", uid, "delete_step2", data)
                await self.send(chat_id, "تأیید دوم از ۳\nآیا مطمئنی می‌خواهی پست‌های ثبت‌شده توسط VIRA در این کانال حذف شوند؟", [[{"text": "بله، مرحله ۳", "callback_data": "delete:confirm:2"}, {"text": "لغو", "callback_data": "home"}]])
            elif step == 2:
                await self.db("set_state", uid, "delete_step3", data)
                await self.send(chat_id, "تأیید سوم از ۳\nاین کار قابل بازگشت نیست. بعد از تأیید نهایی باید عبارت مشخص‌شده را دستی تایپ کنی.", [[{"text": "تأیید نهایی", "callback_data": "delete:confirm:3"}, {"text": "لغو", "callback_data": "home"}]])
            elif step == 3:
                await self.db("set_state", uid, "delete_phrase", data)
                await self.send(chat_id, f"برای اجرای حذف، دقیقاً این عبارت را تایپ کن:\n`{data['phrase']}`\n\nلغو: /cancel")
            return
        if action == "filters":
            rules = await self.db("filter_rules", uid)
            routes = await self.db("routes", uid)
            text = "🛡 فیلتر تبلیغات\n\nفیلتر شامل کلمات تبلیغاتی رایج، لینک‌های متعدد همراه با عبارات دعوت/خرید، و کلمات شخصی توست. تنظیم روشن/خاموش هر مسیر از بخش کپی خودکار انجام می‌شود.\n\nعبارات اختصاصی:\n" + ("\n".join("• " + r["value"] for r in rules[:30]) or "هنوز عبارتی اضافه نکردی.")
            await self.send(chat_id, text, [[{"text": "➕ افزودن کلمه/عبارت", "callback_data": "filter:add"}], [{"text": "مدیریت مسیرها", "callback_data": "routes"}, {"text": "منوی اصلی", "callback_data": "home"}]])
            return
        if action == "filter:add":
            await self.db("set_state", uid, "filter_rule", {})
            await self.send(chat_id, "کلمه یا عبارت تبلیغاتی موردنظر را بفرست.\nلغو: /cancel")
            return
        if action == "ai":
            await self.db("set_state", uid, "ai_prompt", {})
            await self.send(chat_id, "🤖 متن یا درخواستت را بفرست؛ مثلاً تولید کپشن، بازنویسی یا ایده محتوا.\nفقط متنی که خودت در گفت‌وگوی خصوصی می‌فرستی پردازش می‌شود؛ محتوای کانال‌های مبدا به AI خارجی ارسال نمی‌شود.\nلغو: /cancel")
            return
        if action == "subscription":
            sub = await self.db("subscription", uid)
            if sub:
                await self.send(chat_id, f"💎 اشتراک مشترک VIRA\nپلن: {sub['plan_code']}\nوضعیت: {sub['status']}\nانقضا: {sub['expires_at'] or 'ثبت نشده'}\n\nاطلاعات از همان دیتابیس اپلیکیشن خوانده می‌شود.", self.home_keyboard(telegram_id))
            else:
                await self.send(chat_id, "هنوز اشتراکی برای حساب VIRA ثبت نشده است.", self.home_keyboard(telegram_id))
            return
        if action == "balance":
            await self.send(chat_id, "💳 بخش افزایش موجودی در نقشه محصول آماده شده است، اما مطابق تصمیم پروژه، منطق پرداخت و شارژ موجودی در این نسخه پیاده‌سازی نشده.", self.home_keyboard(telegram_id)); return
        if action == "admin":
            if telegram_id not in ADMIN_IDS:
                await self.send(chat_id, "دسترسی مدیر ندارید."); return
            stats = await self.db("admin_stats")
            text = ("⚙️ پنل مدیریت VIRA Helper\n\n" + "\n".join(f"• {k}: {v}" for k, v in [
                ("کل کاربران", stats["users"]), ("کاربران متصل به Helper", stats["helper_users"]),
                ("اشتراک فعال", stats["active_subscriptions"]), ("کانال‌ها", stats["channels"]),
                ("مسیرهای فعال کپی", stats["routes"]), ("انتشارهای زمان‌بندی‌شده", stats["scheduled"]),
                ("آرشیوهای در انتظار", stats["pending_archive"]),
            ]))
            await self.send(chat_id, text, [[{"text": "👥 کاربران متصل", "callback_data": "admin:users"}], [{"text": "📣 پیام همگانی", "callback_data": "admin:broadcast"}], [{"text": "منوی اصلی", "callback_data": "home"}]])
            return
        if action == "admin:users":
            if telegram_id not in ADMIN_IDS: return
            users = await self.db("admin_users", 25)
            lines = [f"{u['first_name']} | @{u['username'] or '-'} | {u['telegram_id']}" for u in users]
            await self.send(chat_id, "👥 آخرین کاربران متصل:\n\n" + ("\n".join(lines) or "موردی نیست."), [[{"text": "بازگشت به پنل", "callback_data": "admin"}]])
            return
        if action == "admin:broadcast":
            if telegram_id not in ADMIN_IDS: return
            await self.db("set_state", uid, "admin_broadcast", {})
            await self.send(chat_id, "متن پیام همگانی را بفرست. قبل از ارسال، پیش‌نمایش و تأیید می‌گیریم.\nلغو: /cancel")
            return
        if action == "admin:broadcast:cancel":
            await self.db("set_state", uid, "", {})
            await self.send(chat_id, "ارسال همگانی لغو شد.", self.home_keyboard(telegram_id)); return
        if action == "admin:broadcast:confirm":
            if telegram_id not in ADMIN_IDS: return
            state = await self.db("get_state", uid)
            if state["state"] != "admin_broadcast_confirm":
                await self.send(chat_id, "پیش‌نمایش منقضی شده است."); return
            text = state["data"].get("text", "")
            recipients = await self.db("helper_recipients")
            sent = failed = 0
            for recipient in recipients:
                try:
                    await self.send(recipient, text)
                    sent += 1
                except BotAPIError:
                    failed += 1
                await asyncio.sleep(0.06)
            await self.db("set_state", uid, "", {})
            await self.send(chat_id, f"گزارش ارسال همگانی\nموفق: {sent}\nناموفق: {failed}", self.home_keyboard(telegram_id)); return
        await self.send(chat_id, "این دکمه شناخته نشد. /menu را بزن.", self.home_keyboard(telegram_id))

    async def track_history(self, user_id: int, channel_id: int, message_id: int, source: str):
        def op(c, uid, cid, mid, src):
            c.execute("INSERT OR IGNORE INTO helper_post_history(user_id,channel_id,message_id,source,created_at) VALUES(?,?,?,?,?)", (uid, cid, mid, src, utc_now()))
        await asyncio.to_thread(DB.run, op, user_id, channel_id, message_id, source)

    async def transfer_archive(self, chat_id: int, telegram_id: int, user_id: int, channel: dict, items: list[dict]):
        sent = failed = 0
        # Group messages are copied together so Telegram can retain album grouping.
        groups: list[list[dict]] = []
        index = 0
        while index < len(items):
            item = items[index]
            group_id = item.get("media_group_id") or ""
            if group_id:
                group = [item]
                j = index + 1
                while j < len(items) and items[j].get("media_group_id") == group_id and len(group) < 10:
                    group.append(items[j]); j += 1
                groups.append(group); index = j
            else:
                groups.append([item]); index += 1
        for group in groups:
            try:
                if len(group) > 1:
                    result = await self.api.call("copyMessages", {
                        "chat_id": channel["chat_id"], "from_chat_id": group[0]["private_chat_id"],
                        "message_ids": [int(x["private_message_id"]) for x in group],
                    })
                else:
                    one = group[0]
                    result = [await self.api.call("copyMessage", {
                        "chat_id": channel["chat_id"], "from_chat_id": one["private_chat_id"], "message_id": int(one["private_message_id"])
                    })]
                dest_ids = [int(x["message_id"]) for x in result]
                for item, dest_id in zip(group, dest_ids):
                    await self.track_history(user_id, int(channel["id"]), dest_id, "archive_import")
                    await self.db("finish_archive_item", int(item["id"]), "sent")
                    sent += 1
                if len(dest_ids) != len(group):
                    for item in group[len(dest_ids):]:
                        await self.db("finish_archive_item", int(item["id"]), "failed")
                        failed += 1
            except BotAPIError as exc:
                for item in group:
                    await self.db("finish_archive_item", int(item["id"]), "failed")
                    failed += 1
                log.warning("archive transfer failed: %s", exc.description[:160])
            await asyncio.sleep(0.12)
        await self.send(chat_id, f"📦 انتقال آرشیو پایان یافت.\nمنتقل‌شده: {sent}\nناموفق: {failed}\n\nترتیب پیام‌ها تا حد دریافت‌شده حفظ شد؛ آیتم‌های ناموفق در دیتابیس با وضعیت failed ثبت شدند.", self.home_keyboard(telegram_id))

    async def generate_ai(self, prompt: str) -> str:
        api_key = os.getenv("AI_API_KEY", "").strip()
        base = os.getenv("AI_API_BASE_URL", "").strip().rstrip("/")
        model = os.getenv("AI_MODEL", "").strip()
        if not (api_key and base and model):
            return "🤖 سرویس AI هنوز روی سرور VIRA تنظیم نشده است. متغیرهای AI_API_KEY، AI_API_BASE_URL و AI_MODEL باید توسط مدیر روی سرور تنظیم شوند."
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=8.0)) as client:
                response = await client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "تو دستیار فارسی VIRA هستی. پاسخ‌ها کاربردی، دقیق، حرفه‌ای و بدون ادعای بی‌پایه باشند."},
                        {"role": "user", "content": prompt[:3500]},
                    ],
                    "temperature": 0.6,
                })
                response.raise_for_status()
                result = response.json()
            answer = result["choices"][0]["message"]["content"]
            return str(answer).strip()[:3900] or "پاسخ خالی دریافت شد."
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            log.warning("AI request failed: %s", type(exc).__name__)
            return "ارتباط با سرویس هوش مصنوعی ناموفق بود. تنظیمات و اعتبار API را بررسی کن و دوباره تلاش کن."

    async def handle_channel_post(self, message: dict):
        chat = message.get("chat") or {}
        if chat.get("type") != "channel":
            return
        source_id = str(chat.get("id"))
        album_id = str(message.get("media_group_id") or "")
        if album_id:
            key = (source_id, album_id)
            async with self.album_lock:
                bucket = self.album_buffer.setdefault(key, {"messages": [], "last_seen": asyncio.get_running_loop().time()})
                if not any(int(x["message_id"]) == int(message["message_id"]) for x in bucket["messages"]):
                    bucket["messages"].append(message)
                bucket["last_seen"] = asyncio.get_running_loop().time()
            return
        await self.distribute_channel_messages(source_id, [message])

    async def distribute_channel_messages(self, source_id: str, messages: list[dict]):
        if not messages:
            return
        routes = await self.db("get_routes_for_source", source_id)
        if not routes:
            return
        sample_text = "\n".join(str(m.get("text") or m.get("caption") or "") for m in messages)
        for route in routes:
            user_rules = await self.db("filter_rules", int(route["user_id"])) if route["filter_ads"] else []
            extra = [r["value"] for r in user_rules]
            if route["filter_ads"] and is_advertisement(sample_text, extra):
                log.info("ad filtered source=%s route=%s", source_id, route["id"])
                continue
            pending = []
            for msg in messages:
                source_mid = int(msg["message_id"])
                if await self.db("reserve_route_event", int(route["id"]), source_mid, int(route["destination_id"])):
                    pending.append(msg)
            if not pending:
                continue
            try:
                if route["mode"] == "forward":
                    dest_ids = []
                    for msg in pending:
                        result = await self.api.call("forwardMessage", {
                            "chat_id": route["destination_chat_id"], "from_chat_id": source_id, "message_id": int(msg["message_id"])
                        })
                        dest_ids.append(int(result["message_id"]))
                        await asyncio.sleep(0.08)
                elif len(pending) > 1:
                    result = await self.api.call("copyMessages", {
                        "chat_id": route["destination_chat_id"], "from_chat_id": source_id,
                        "message_ids": [int(x["message_id"]) for x in pending],
                    })
                    dest_ids = [int(x["message_id"]) for x in result]
                else:
                    result = await self.api.call("copyMessage", {
                        "chat_id": route["destination_chat_id"], "from_chat_id": source_id,
                        "message_id": int(pending[0]["message_id"]),
                    })
                    dest_ids = [int(result["message_id"])]
                for i, msg in enumerate(pending):
                    dest_id = dest_ids[i] if i < len(dest_ids) else None
                    await self.db("record_route_result", int(route["user_id"]), int(route["id"]), int(msg["message_id"]), int(route["destination_id"]), dest_id, "" if dest_id else "Telegram returned no message id")
                if len(dest_ids) < len(pending):
                    log.warning("partial copy route=%s expected=%s got=%s", route["id"], len(pending), len(dest_ids))
            except BotAPIError as exc:
                for msg in pending:
                    await self.db("record_route_result", int(route["user_id"]), int(route["id"]), int(msg["message_id"]), int(route["destination_id"]), None, exc.description)
                log.warning("route delivery failed route=%s error=%s", route["id"], exc.description[:160])
            await asyncio.sleep(0.12)

    async def album_worker(self):
        while not self.stop_event.is_set():
            await asyncio.sleep(0.7)
            now = asyncio.get_running_loop().time()
            ready = []
            async with self.album_lock:
                for key, bucket in list(self.album_buffer.items()):
                    if now - bucket["last_seen"] >= 1.25:
                        ready.append((key, sorted(bucket["messages"], key=lambda m: int(m["message_id"]))))
                        self.album_buffer.pop(key, None)
            for (source_id, _album), messages in ready:
                try:
                    await self.distribute_channel_messages(source_id, messages)
                except Exception as exc:
                    log.exception("album delivery failed: %s", type(exc).__name__)

    async def scheduler_worker(self):
        try:
            recovered = await self.db("recover_stale_schedules", 10)
            if recovered:
                log.warning("recovered %s stale scheduled posts", recovered)
        except Exception as exc:
            log.exception("schedule recovery failed: %s", type(exc).__name__)
        while not self.stop_event.is_set():
            try:
                posts = await self.db("claim_due_schedules", 10)
                for post in posts:
                    try:
                        result = await self.api.call("copyMessage", {
                            "chat_id": post["destination_channel_id"], "from_chat_id": post["source_chat_id"],
                            "message_id": int(post["source_message_id"]),
                        })
                        await self.db("finish_schedule", post["id"], True, int(result["message_id"]), "")
                    except BotAPIError as exc:
                        await self.db("finish_schedule", post["id"], False, None, exc.description)
                    await asyncio.sleep(0.1)
            except Exception as exc:
                log.exception("scheduler loop error: %s", type(exc).__name__)
            await asyncio.sleep(3.0)

    async def process_update(self, update: dict):
        if "message" in update:
            await self.handle_message(update["message"])
        elif "callback_query" in update:
            await self.handle_callback(update["callback_query"])
        elif "channel_post" in update:
            await self.handle_channel_post(update["channel_post"])
        elif "my_chat_member" in update:
            item = update["my_chat_member"]
            chat = item.get("chat") or {}
            log.info("bot membership changed chat=%s new_status=%s", chat.get("id"), (item.get("new_chat_member") or {}).get("status"))

    async def run(self):
        # Bot API does not allow polling and a webhook simultaneously; this standalone
        # worker intentionally switches the dedicated Helper bot to long polling.
        await self.api.call("deleteWebhook", {"drop_pending_updates": False})
        self.me = await self.api.call("getMe", {})
        log.info("VIRA Helper started as @%s; DB=%s", self.me.get("username"), DB_PATH)
        offset = await self.db("get_offset")
        workers = [asyncio.create_task(self.album_worker()), asyncio.create_task(self.scheduler_worker())]
        try:
            while not self.stop_event.is_set():
                try:
                    updates = await self.api.call("getUpdates", {
                        "offset": offset, "timeout": 30,
                        "allowed_updates": ["message", "callback_query", "channel_post", "my_chat_member"],
                    })
                    for update in updates or []:
                        update_id = int(update.get("update_id", 0))
                        try:
                            await self.process_update(update)
                        except Exception as exc:
                            log.exception("update %s failed: %s", update_id, type(exc).__name__)
                        offset = max(offset, update_id + 1)
                        await self.db("save_offset", offset)
                except BotAPIError as exc:
                    log.error("polling API error: %s", exc.description[:180])
                    retry_after = int(exc.parameters.get("retry_after", 0) or 0)
                    wait = max(3, min(60, retry_after or (15 if exc.error_code == 409 else 5)))
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception("polling loop error: %s", type(exc).__name__)
                    await asyncio.sleep(3)
        finally:
            self.stop_event.set()
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)


async def main():
    if not TOKEN or TOKEN == "PASTE_VIRA_HELPER_BOT_TOKEN_HERE":
        raise SystemExit("ابتدا مقدار BOT_TOKEN را در ابتدای فایل با توکن اختصاصی VIRA Helper جایگزین کن.")
    if not DB_PATH.exists():
        raise SystemExit(f"پایگاه داده VIRA پیدا نشد: {DB_PATH}\nVIRA_DB_PATH را روی مسیر دیتابیس اصلی اپ تنظیم کن.")
    if not any(isinstance(x, int) and x > 0 and x != 123456789 for x in ADMIN_IDS):
        log.warning("ADMIN_IDS still contains the example ID; the admin panel will be unavailable.")
    # Ensure migrations 1..5 exist without changing existing user/channel/subscription data.
    await asyncio.to_thread(init_db)
    api = TelegramAPI(TOKEN)
    bot = ViraHelperBot(api)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.stop_event.set)
        except NotImplementedError:  # Windows / some Android Python builds
            pass
    try:
        await bot.run()
    finally:
        await api.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("VIRA Helper stopped.")
