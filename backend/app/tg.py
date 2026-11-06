"""کلاینت ساده Bot API تلگرام (فقط stdlib). توکن هرگز لاگ نمی‌شود."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import telegram_api_base


class TgError(RuntimeError):
    pass


def call(token: str, method: str, payload: dict | None = None, timeout: int = 12):
    if not token:
        raise TgError("توکن ربات تنظیم نشده است.")
    url = "{}/bot{}/{}".format(telegram_api_base(), token, method)
    request = urllib.request.Request(
        url, data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:
            raise TgError("خطای تلگرام ({})".format(exc.code)) from None
    except Exception as exc:
        raise TgError("ارتباط با تلگرام برقرار نشد ({})".format(type(exc).__name__)) from None
    if not isinstance(data, dict) or not data.get("ok"):
        description = data.get("description", "خطای نامشخص") if isinstance(data, dict) else "پاسخ نامعتبر"
        raise TgError(str(description)[:200])
    return data.get("result")
