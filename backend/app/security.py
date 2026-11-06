"""توکن، رمز مدیر، محدودیت نرخ، تشخیص IP و دکوراتورهای دسترسی."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import g, request

from .config import env_int
from .db import audit, get_db, utc_now

USER_TOKEN_DAYS = 30
ADMIN_TOKEN_HOURS = 8


class ApiError(Exception):
    def __init__(self, status: int, detail: str, headers: dict | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.headers = headers or {}


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


# ── رمز مدیر (scrypt) ────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        n, r, p, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r),
                                p=int(p), dklen=len(expected))
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


# ── IP کلاینت ────────────────────────────────────────────────────────────────
def client_ip() -> str:
    """TRUST_PROXY_HOPS = تعداد پراکسی مطمئن جلوی برنامه (Nginx/Apache). پیش‌فرض ۱."""
    hops = env_int("TRUST_PROXY_HOPS", 1)
    forwarded = request.headers.get("X-Forwarded-For", "")
    if hops > 0 and forwarded:
        parts = [x.strip() for x in forwarded.split(",") if x.strip()]
        if len(parts) >= hops:
            return parts[-hops][:64]
    return (request.remote_addr or "unknown")[:64]


# ── محدودیت نرخ (SQLite؛ بین چند پروسس Passenger مشترک) ───────────────────────
def rate_limit(bucket: str, limit: int, window_sec: int, *, count_only_failures: bool = False) -> None:
    """هر فراخوانی یک رویداد ثبت می‌کند. اگر بیش از limit بود ۴۲۹ می‌دهد."""
    now = int(time.time())
    with get_db() as conn:
        if now % 50 == 0:
            conn.execute("DELETE FROM rate_limits WHERE ts < ?", (now - 86400,))
        row = conn.execute("SELECT COUNT(*), MIN(ts) FROM rate_limits WHERE bucket=? AND ts>=?",
                           (bucket, now - window_sec)).fetchone()
        if int(row[0]) >= limit:
            retry = max(1, window_sec - (now - int(row[1] or now)))
            raise ApiError(429, "تعداد درخواست‌ها زیاد است؛ کمی بعد دوباره تلاش کن.",
                           {"Retry-After": str(retry)})
        if not count_only_failures:
            conn.execute("INSERT INTO rate_limits(bucket,ts) VALUES(?,?)", (bucket, now))


def record_failure(bucket: str) -> None:
    with get_db() as conn:
        conn.execute("INSERT INTO rate_limits(bucket,ts) VALUES(?,?)", (bucket, int(time.time())))


# ── نشست‌ها ──────────────────────────────────────────────────────────────────
def _iso_in(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat(timespec="seconds")


def create_session(conn, kind: str, user_id: int | None) -> str:
    token = ("va_" if kind == "admin" else "vu_") + secrets.token_urlsafe(32)
    ttl = timedelta(hours=ADMIN_TOKEN_HOURS) if kind == "admin" else timedelta(days=USER_TOKEN_DAYS)
    conn.execute(
        "INSERT INTO sessions(token_hash,kind,user_id,created_at,expires_at) VALUES(?,?,?,?,?)",
        (sha256_hex(token), kind, user_id, utc_now(), _iso_in(ttl)))
    return token


def _bearer() -> str:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()[:200]


def _load_session(kind: str):
    token = _bearer()
    if not token:
        raise ApiError(401, "ابتدا وارد شو.")
    with get_db() as conn:
        row = conn.execute(
            "SELECT s.id AS sid, s.user_id, s.expires_at, s.revoked_at FROM sessions s "
            "WHERE s.token_hash=? AND s.kind=?", (sha256_hex(token), kind)).fetchone()
        if not row or row["revoked_at"] or row["expires_at"] <= utc_now():
            raise ApiError(401, "نشست معتبر نیست یا منقضی شده است.")
        user = None
        if kind == "user":
            user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
            if not user:
                raise ApiError(401, "حساب پیدا نشد.")
            user = dict(user)
    g.session_id = row["sid"]
    g.user = user


def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _load_session("user")
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _load_session("admin")
        return fn(*args, **kwargs)
    return wrapper


def revoke_current_session() -> None:
    with get_db() as conn:
        conn.execute("UPDATE sessions SET revoked_at=? WHERE id=?", (utc_now(), g.session_id))


def public_user(row: dict) -> dict:
    return {
        "id": row["id"], "telegram_id": row["telegram_id"], "first_name": row["first_name"],
        "last_name": row["last_name"], "username": row["username"],
        "created_at": row["created_at"], "last_login_at": row["last_login_at"],
        "balance_toman": row.get("balance_toman", 0),
    }


def audit_event(actor: str, action: str, detail: str = "") -> None:
    with get_db() as conn:
        audit(conn, actor, action, detail)
