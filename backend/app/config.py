"""تنظیمات از متغیرهای محیطی (و فایل .env بیرون از پوشه backend)."""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent


def _load_env_file() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name) or default)
    except ValueError:
        return default


def public_base_url() -> str:
    return env("PUBLIC_BASE_URL", "https://api.blackcreator.ir").rstrip("/")


def telegram_api_base() -> str:
    return env("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


def auth_bot_username() -> str:
    return env("AUTH_BOT_USERNAME").lstrip("@")


def telegram_login_ready() -> bool:
    return bool(env("AUTH_BOT_TOKEN") and auth_bot_username() and env("TELEGRAM_WEBHOOK_SECRET"))


def channel_bot_ready() -> bool:
    return bool(env("CHANNEL_BOT_TOKEN"))


def ai_ready() -> bool:
    return bool(env("AI_API_KEY"))


def cors_origins() -> set:
    base = {"https://localhost", "capacitor://localhost", "http://localhost"}
    extra = {x.strip() for x in env("CORS_ORIGINS").split(",") if x.strip()}
    return base | extra
