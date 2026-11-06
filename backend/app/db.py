"""SQLite + مهاجرت‌ها. با vira_helper_bot.py سازگار است (init_db, get_db, audit, utc_now)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import BACKEND_DIR, env


def utc_now() -> str:
    # قالب ثابت؛ ربات کمکی رشته‌ها را مستقیم مقایسه می‌کند.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    value = env("VIRA_DB_PATH") or env("DATABASE_PATH")
    path = Path(value).expanduser() if value else BACKEND_DIR / "data" / "vira.sqlite3"
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, actor: str, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log(actor,action,detail,created_at) VALUES(?,?,?,?)",
        (str(actor)[:80], str(action)[:80], str(detail)[:300], utc_now()),
    )


MIGRATIONS = [
    # 1 — هسته
    """
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      telegram_id TEXT NOT NULL UNIQUE,
      first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '',
      username TEXT NOT NULL DEFAULT '',
      balance_toman INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, last_login_at TEXT);
    CREATE TABLE IF NOT EXISTS sessions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      token_hash TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL CHECK(kind IN ('user','admin')),
      user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT);
    CREATE TABLE IF NOT EXISTS login_sessions(
      id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, code_hash TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL DEFAULT 'pending', tg_user_id TEXT, user_id INTEGER,
      created_at TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT);
    CREATE TABLE IF NOT EXISTS settings(
      key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS services(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
      icon TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
      sort_order INTEGER NOT NULL DEFAULT 100, is_active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS rate_limits(bucket TEXT NOT NULL, ts INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_rate_bucket ON rate_limits(bucket, ts);
    """,
    # 2 — کانال، پست، اشتراک
    """
    CREATE TABLE IF NOT EXISTS channels(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      chat_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(user_id, chat_id));
    CREATE TABLE IF NOT EXISTS channel_posts(
      id TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
      title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'draft',
      scheduled_at TEXT, published_at TEXT, message_id INTEGER, last_error TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_posts_channel ON channel_posts(channel_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_posts_due ON channel_posts(status, scheduled_at);
    CREATE TABLE IF NOT EXISTS subscriptions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      plan_code TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
      expires_at TEXT, created_at TEXT NOT NULL DEFAULT '');
    """,
    # 3 — جدول‌های ربات کمکی (طبق SQLهای vira_helper_bot.py)
    """
    CREATE TABLE IF NOT EXISTS helper_bot_links(
      user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
      telegram_id TEXT NOT NULL, linked_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS helper_user_state(
      user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
      state TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS channel_routes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      source_chat_id TEXT NOT NULL, source_title TEXT NOT NULL DEFAULT '',
      destination_channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
      mode TEXT NOT NULL DEFAULT 'copy', filter_ads INTEGER NOT NULL DEFAULT 1,
      enabled INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(user_id, source_chat_id, destination_channel_id));
    CREATE TABLE IF NOT EXISTS helper_route_events(
      route_id INTEGER NOT NULL REFERENCES channel_routes(id) ON DELETE CASCADE,
      source_message_id INTEGER NOT NULL, destination_channel_id INTEGER,
      status TEXT NOT NULL DEFAULT 'pending', destination_message_ids TEXT NOT NULL DEFAULT '[]',
      last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      PRIMARY KEY(route_id, source_message_id));
    CREATE TABLE IF NOT EXISTS helper_post_history(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, source TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL, delete_status TEXT NOT NULL DEFAULT 'active',
      UNIQUE(channel_id, message_id));
    CREATE TABLE IF NOT EXISTS helper_scheduled_posts(
      id TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      destination_channel_id INTEGER NOT NULL, source_chat_id TEXT NOT NULL,
      source_message_id INTEGER NOT NULL, scheduled_at TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'scheduled', published_message_id INTEGER,
      last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS helper_archive_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      private_chat_id TEXT NOT NULL, private_message_id INTEGER NOT NULL,
      media_group_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending');
    CREATE TABLE IF NOT EXISTS helper_filter_rules(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      rule_type TEXT NOT NULL, value TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(user_id, rule_type, value));
    CREATE TABLE IF NOT EXISTS helper_bot_meta(
      key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
    """,
    # 4 — پرداخت و کیف پول
    """
    CREATE TABLE IF NOT EXISTS payment_invoices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      amount_toman INTEGER NOT NULL CHECK(amount_toman > 0),
      status TEXT NOT NULL DEFAULT 'pending',
      authority TEXT NOT NULL DEFAULT 'L', ref_id TEXT,
      created_at TEXT NOT NULL, paid_at TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_ref ON payment_invoices(ref_id) WHERE ref_id IS NOT NULL;
    CREATE TABLE IF NOT EXISTS wallet_ledger(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      invoice_id INTEGER NOT NULL UNIQUE, amount_toman INTEGER NOT NULL,
      kind TEXT NOT NULL DEFAULT 'topup', created_at TEXT NOT NULL);
    """,
    # 5 — سهمیه هوش مصنوعی و ایندکس‌ها
    """
    CREATE TABLE IF NOT EXISTS ai_usage(
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(user_id, day));
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_audit_id ON audit_log(id);
    """,
    # 6 — سوابق خرید اشتراک و کسر موجودی (بدون تغییر جدول‌های قدیمی)
    """
    CREATE TABLE IF NOT EXISTS subscription_purchases(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
      plan_code TEXT NOT NULL,
      amount_toman INTEGER NOT NULL CHECK(amount_toman > 0),
      balance_before INTEGER NOT NULL,
      balance_after INTEGER NOT NULL,
      starts_at TEXT NOT NULL,
      expires_at TEXT NOT NULL,
      created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_subscription_purchases_user
      ON subscription_purchases(user_id, created_at DESC);
    """,
]


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = int(row[0] or 0)
        for number, script in enumerate(MIGRATIONS, start=1):
            if number <= current:
                continue
            for statement in [s.strip() for s in script.split(";") if s.strip()]:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_version(version) VALUES(?)", (number,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
