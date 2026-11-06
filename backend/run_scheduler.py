"""برای Cron Job هر دقیقه: انتشار پست‌های زمان‌بندی‌شده."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import channels  # noqa: E402
from app.db import init_db  # noqa: E402

if __name__ == "__main__":
    init_db()
    print("published:", channels.run_due_posts())
