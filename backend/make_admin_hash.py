"""ساخت هش رمز مدیر: python backend/make_admin_hash.py  (رمز را مخفیانه می‌پرسد)."""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.security import hash_password  # noqa: E402

if __name__ == "__main__":
    password = getpass.getpass("رمز مدیر (حداقل ۱۲ نویسه): ")
    if len(password) < 12 or password != getpass.getpass("تکرار رمز: "):
        raise SystemExit("رمز کوتاه است یا تکرار آن یکسان نیست.")
    print("ADMIN_PASSWORD_HASH='" + hash_password(password) + "'")
