"""یک‌بار اجرا کن تا وبهوک ربات ورود روی تلگرام ثبت شود."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import tg  # noqa: E402
from app.config import env, public_base_url  # noqa: E402

if __name__ == "__main__":
    token, secret = env("AUTH_BOT_TOKEN"), env("TELEGRAM_WEBHOOK_SECRET")
    if not token or len(secret) < 16:
        raise SystemExit("AUTH_BOT_TOKEN و TELEGRAM_WEBHOOK_SECRET (حداقل ۱۶ نویسه) را در .env بگذار.")
    url = public_base_url() + "/api/telegram/webhook"
    tg.call(token, "setWebhook", {"url": url, "secret_token": secret,
                                  "allowed_updates": ["message", "callback_query"],
                                  "drop_pending_updates": True})
    print("webhook set:", url)
    print(json.dumps(tg.call(token, "getWebhookInfo"), ensure_ascii=False)[:300])
