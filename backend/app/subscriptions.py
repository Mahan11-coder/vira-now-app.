"""خرید اشتراک با کسر اتمیک موجودی کیف پول."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .db import audit, get_db, utc_now
from .security import ApiError
from . import settings_store

PLANS = {
    "monthly": {"title": "ماهانه", "days": 30, "setting": "subscription_monthly_price"},
    "quarterly": {"title": "سه‌ماهه", "days": 90, "setting": "subscription_quarterly_price"},
    "yearly": {"title": "سالانه", "days": 365, "setting": "subscription_yearly_price"},
}


def _price(value) -> int:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,9}", text):
        return 0
    amount = int(text)
    return amount if 1000 <= amount <= 500_000_000 else 0


def list_plans() -> list[dict]:
    values = settings_store.get_all()
    result = []
    for code, plan in PLANS.items():
        price = _price(values.get(plan["setting"]))
        result.append({
            "plan_code": code,
            "title": plan["title"],
            "duration_days": plan["days"],
            "price_toman": price or None,
            "enabled": price > 0,
        })
    return result


def list_user_subscriptions(user_id: int) -> dict:
    now_dt = datetime.now(timezone.utc)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id,plan_code,status,expires_at,created_at FROM subscriptions "
            "WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,)).fetchall()
    items = [dict(row) for row in rows]
    active = None
    for item in items:
        if item["status"] != "active":
            continue
        try:
            end = datetime.fromisoformat(str(item["expires_at"]).replace("Z", "+00:00")) if item["expires_at"] else None
            if end is None or (end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end) > now_dt:
                active = item
                break
        except ValueError:
            continue
    return {"active": active, "items": items}


def purchase(user_id: int, plan_code: str) -> dict:
    plan = PLANS.get(str(plan_code or "").strip())
    if not plan:
        raise ApiError(400, "نوع اشتراک انتخاب‌شده معتبر نیست.")

    values = settings_store.get_all()
    price = _price(values.get(plan["setting"]))
    if not price:
        raise ApiError(503, "قیمت این اشتراک هنوز در پنل مدیریت تنظیم نشده است.")

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT balance_toman FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            raise ApiError(404, "حساب کاربری پیدا نشد.")
        balance_before = int(user["balance_toman"])
        if balance_before < price:
            raise ApiError(402, "موجودی کیف پول برای خرید این اشتراک کافی نیست.")

        active_rows = conn.execute(
            "SELECT id,expires_at FROM subscriptions WHERE user_id=? AND status='active'",
            (user_id,)).fetchall()
        future_ends = []
        for row in active_rows:
            if not row["expires_at"]:
                raise ApiError(409, "برای این حساب اشتراک دائمی فعال ثبت شده است.")
            try:
                end = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if end > now_dt:
                    future_ends.append(end.astimezone(timezone.utc))
            except ValueError:
                raise ApiError(409, "تاریخ پایان اشتراک فعلی معتبر نیست؛ پشتیبانی باید آن را بررسی کند.") from None

        starts_dt = max(future_ends) if future_ends else now_dt
        expires_dt = starts_dt + timedelta(days=int(plan["days"]))
        starts_at = starts_dt.isoformat(timespec="seconds")
        expires_at = expires_dt.isoformat(timespec="seconds")

        # کسر موجودی، ثبت اشتراک و سابقه خرید در یک تراکنش SQLite انجام می‌شود.
        changed = conn.execute(
            "UPDATE users SET balance_toman=balance_toman-? WHERE id=? AND balance_toman>=?",
            (price, user_id, price)).rowcount
        if changed != 1:
            raise ApiError(402, "موجودی کیف پول برای خرید این اشتراک کافی نیست.")

        conn.execute(
            "UPDATE subscriptions SET status='superseded' WHERE user_id=? AND status='active'",
            (user_id,))
        cur = conn.execute(
            "INSERT INTO subscriptions(user_id,plan_code,status,expires_at,created_at) "
            "VALUES(?,?,'active',?,?)", (user_id, plan_code, expires_at, now))
        subscription_id = int(cur.lastrowid)
        balance_after = balance_before - price
        conn.execute(
            "INSERT INTO subscription_purchases(user_id,subscription_id,plan_code,amount_toman,"
            "balance_before,balance_after,starts_at,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (user_id, subscription_id, plan_code, price, balance_before, balance_after,
             starts_at, expires_at, now))
        audit(conn, "user:{}".format(user_id), "subscription_purchased",
              "{}:{}:{}".format(plan_code, price, subscription_id))

    return {
        "status": "active",
        "subscription_id": subscription_id,
        "plan_code": plan_code,
        "title": plan["title"],
        "amount_toman": price,
        "balance_toman": balance_after,
        "starts_at": starts_at,
        "expires_at": expires_at,
    }
