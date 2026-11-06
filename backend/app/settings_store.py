"""تنظیمات عمومی برنامه (پنل مدیریت). فقط کلیدهای فهرست سفید پذیرفته می‌شوند."""
from __future__ import annotations

import json
import re

from .config import channel_bot_ready, env
from .db import get_db, utc_now
from .security import ApiError

DEFAULTS = {
    "app_name": "VIRA", "tagline": "بساز، رشد بده، مدیریت کن",
    "home_title": "VIRA", "home_subtitle": "دستیار مدیریت کانال و محتوا",
    "onboarding_question": "امروز می‌خواهی چه کاری انجام بدهی؟",
    "subscription_title": "اشتراک ویژه", "subscription_description": "",
    "subscription_monthly_price": "", "subscription_quarterly_price": "",
    "subscription_yearly_price": "", "subscription_cta_text": "خرید اشتراک",
    "subscription_bot_url": "", "services_bot_url": "",
    "auth_bot_username": "", "support_url": "", "community_url": "",
    "telegram_channel_id": "", "telegram_channel_username": "", "instagram_profile_url": "",
    "announcement_text": "", "ai_status_text": "",
    "free_ai_daily_limit": "5", "premium_ai_daily_limit": "50",
    "security_notice": "توکن و رمز خود را با هیچ‌کس به اشتراک نگذار.",
    "footer_text": "", "ai_enabled": True,
}
URL_KEYS = {"subscription_bot_url", "services_bot_url", "support_url", "community_url",
            "instagram_profile_url"}
LIMIT_KEYS = {"free_ai_daily_limit", "premium_ai_daily_limit"}
PRICE_KEYS = {"subscription_monthly_price", "subscription_quarterly_price", "subscription_yearly_price"}
LONG_KEYS = {"announcement_text", "subscription_description", "security_notice", "footer_text",
             "ai_status_text", "home_subtitle"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_USER = re.compile(r"^[A-Za-z0-9_]{5,32}$")
_CHAT = re.compile(r"^(@[A-Za-z0-9_]{5,32}|-?\d{5,20})$")


def normalize(key: str, value):
    if key == "ai_enabled":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        value = ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ApiError(400, "مقدار «{}» معتبر نیست.".format(key))
    text = _CONTROL.sub("", str(value)).strip()
    limit = 600 if key in LONG_KEYS else 200
    if len(text) > limit:
        raise ApiError(400, "مقدار «{}» بیش از حد طولانی است.".format(key))
    if key in URL_KEYS and text:
        if not re.fullmatch(r"https://[^\s<>\"']{4,280}", text, re.IGNORECASE):
            raise ApiError(400, "«{}» باید یک لینک کامل با https:// باشد.".format(key))
    if key in ("auth_bot_username", "telegram_channel_username"):
        text = text.lstrip("@")
        if text and not _USER.fullmatch(text):
            raise ApiError(400, "یوزرنیم «{}» معتبر نیست (بدون @ و فقط حروف انگلیسی/عدد/_).".format(key))
    if key == "telegram_channel_id" and text and not _CHAT.fullmatch(text):
        raise ApiError(400, "شناسه کانال معتبر نیست.")
    if key in LIMIT_KEYS:
        if not text.isdigit() or int(text) > 100000:
            raise ApiError(400, "سهمیه باید عدد بین ۰ تا ۱۰۰۰۰۰ باشد.")
        text = str(int(text))
    if key in PRICE_KEYS and text:
        if not text.isdigit() or not 1000 <= int(text) <= 500000000:
            raise ApiError(400, "قیمت اشتراک باید عدد صحیح بین ۱٬۰۰۰ تا ۵۰۰٬۰۰۰٬۰۰۰ تومان باشد.")
        text = str(int(text))
    return text


def get_all() -> dict:
    values = dict(DEFAULTS)
    with get_db() as conn:
        for row in conn.execute("SELECT key,value FROM settings"):
            if row["key"] in DEFAULTS:
                try:
                    values[row["key"]] = json.loads(row["value"])
                except ValueError:
                    pass
    return values


def save(updates: dict) -> list:
    clean = {}
    for key, value in updates.items():
        if key in DEFAULTS:
            clean[key] = normalize(key, value)
    with get_db() as conn:
        for key, value in clean.items():
            conn.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), utc_now()))
    return sorted(clean)


def login_bot_username() -> str:
    return (env("AUTH_BOT_USERNAME") or str(get_all().get("auth_bot_username") or "")).lstrip("@")


def telegram_login_ready() -> bool:
    return bool(env("AUTH_BOT_TOKEN") and env("TELEGRAM_WEBHOOK_SECRET") and login_bot_username())


def runtime_flags() -> dict:
    from .config import ai_ready, public_base_url
    return {
        "ai_ready": ai_ready(),
        "channel_bot_ready": channel_bot_ready(),
        "telegram_login_ready": telegram_login_ready(),
        "https_api_configured": public_base_url().startswith("https://"),
    }
