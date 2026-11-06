"""ساخت خودکار .env سرور + config.php هاست ایرانی (بدون ویرایش دستی).

اجرا (روی سرور آی‌تی‌میزبان، در ترمینال cPanel):  python backend/make_env.py
توکن‌ها هنگام تایپ دیده نمی‌شوند. چیزی در APK یا مخزن قرار نمی‌گیرد.
"""
import getpass
import os
import re
import secrets
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.security import hash_password  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ask(label, secret=False, default=""):
    suffix = " [{}]".format(default) if default else ""
    fn = getpass.getpass if secret else input
    value = fn("{}{}: ".format(label, suffix)).strip()
    return value or default


def main():
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        raise SystemExit(".env از قبل وجود دارد؛ برای جلوگیری از پاک‌شدن اطلاعات، ساخته نشد.")
    print("— رمز مدیر پنل (حداقل ۱۲ نویسه) —")
    password = getpass.getpass("رمز مدیر: ")
    if len(password) < 12 or password != getpass.getpass("تکرار رمز: "):
        raise SystemExit("رمز کوتاه است یا تکرار آن یکسان نیست.")
    print("\n— ربات ورود به برنامه (از BotFather؛ ربات جدید برای VIRA، نه ربات سیگنال) —")
    auth_token = ask("توکن ربات ورود", secret=True)
    if not re.fullmatch(r"[0-9]{5,12}:[A-Za-z0-9_-]{20,}", auth_token):
        raise SystemExit("توکن ربات ورود معتبر نیست.")
    auth_user = ask("یوزرنیم ربات ورود (بدون @)").lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", auth_user):
        raise SystemExit("یوزرنیم ربات ورود معتبر نیست.")
    print("\n— ربات انتشار در کانال (می‌تواند همان ربات ورود نباشد؛ خالی = بعداً) —")
    chan_token = ask("توکن ربات کانال", secret=True)
    if chan_token and not re.fullmatch(r"[0-9]{5,12}:[A-Za-z0-9_-]{20,}", chan_token):
        raise SystemExit("توکن ربات کانال معتبر نیست.")
    print("\n— درگاه پرداخت (خالی = بعداً) —")
    merchant = ask("مرچنت‌کد زرین‌پال")
    if merchant and not re.fullmatch(r"[A-Za-z0-9-]{20,64}", merchant):
        raise SystemExit("فرمت مرچنت‌کد معتبر نیست.")
    pay_base = ask("آدرس پوشه‌ی pay روی هاست ایرانی", default="https://blackcreator.ir/pay").rstrip("/")
    parsed_pay_base = urlsplit(pay_base)
    if (not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9_./-]*)?", pay_base)
            or parsed_pay_base.scheme != "https" or not parsed_pay_base.netloc
            or parsed_pay_base.username or parsed_pay_base.password or parsed_pay_base.query or parsed_pay_base.fragment):
        raise SystemExit("آدرس درگاه باید یک URL معتبر و ساده با https:// باشد.")
    sandbox = ask("حالت پرداخت زرین‌پال (1 آزمایشی / 0 واقعی)", default="1")
    if sandbox not in {"0", "1"}:
        raise SystemExit("حالت پرداخت فقط باید 1 یا 0 باشد.")
    print("\n— هوش مصنوعی (خالی = بعداً) —")
    ai_key = ask("AI_API_KEY", secret=True)
    ai_base = ask("AI_API_BASE_URL", default="https://api.openai.com/v1") if ai_key else ""
    ai_model = ask("AI_MODEL", default="gpt-4o-mini") if ai_key else ""

    shared = secrets.token_urlsafe(36)
    lines = [
        "PUBLIC_BASE_URL=https://api.blackcreator.ir",
        "TRUST_PROXY_HOPS=1",
        "ADMIN_PASSWORD_HASH='{}'".format(hash_password(password)),
        "AUTH_BOT_TOKEN={}".format(auth_token),
        "AUTH_BOT_USERNAME={}".format(auth_user),
        "TELEGRAM_WEBHOOK_SECRET={}".format(secrets.token_urlsafe(32)),
        "CHANNEL_BOT_TOKEN={}".format(chan_token),
        "PAY_SHARED_SECRET={}".format(shared),
        "PAY_BASE_URL={}".format(pay_base.rstrip("/")),
        "PAY_APP_ID=vira",
        "ZARINPAL_MERCHANT_ID={}".format(merchant),
        "ZARINPAL_SANDBOX={}".format(sandbox),
        "AI_API_KEY={}".format(ai_key),
        "AI_API_BASE_URL={}".format(ai_base),
        "AI_MODEL={}".format(ai_model),
    ]
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    php_path = os.path.join(ROOT, "php_payment", "config.php")
    php = ("<?php\nreturn [\n    'SECRET' => '{}',\n    'BASE_URL' => '{}',\n    'MERCHANT_ID' => '{}',\n"
           "    'RETURN_URL' => 'vira://payment?code={{code}}',\n];\n").format(shared, pay_base, merchant)
    fd = os.open(php_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(php)
    print("\n✅ ساخته شد:\n  {}\n  {}  ← همین را به هاست ایرانی (پوشه pay) آپلود کن، سپس از این سرور پاک کن."
          .format(env_path, php_path))


if __name__ == "__main__":
    main()
