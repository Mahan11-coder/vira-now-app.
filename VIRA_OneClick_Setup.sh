#!/usr/bin/env bash
# VIRA cPanel one-click setup assistant (safe / rerunnable)
# Run from /home/USERNAME/vira_api after creating the cPanel Python App.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_URL="https://api.blackcreator.ir"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; RESET='\033[0m'
info(){ printf "${BLUE}ℹ %s${RESET}\n" "$*"; }
ok(){ printf "${GREEN}✓ %s${RESET}\n" "$*"; }
warn(){ printf "${YELLOW}! %s${RESET}\n" "$*"; }
fail(){ printf "${RED}✗ %s${RESET}\n" "$*" >&2; exit 1; }

clear 2>/dev/null || true
printf '\n=====================================================\n'
printf '          راه‌انداز خودکار سرور VIRA\n'
printf '=====================================================\n\n'
printf 'این ابزار نصب وابستگی‌ها، تست، تنظیم امن، دیتابیس و آماده‌سازی Helper را انجام می‌دهد.\n'
printf 'فایل‌های موجود .env و دیتابیس را بازنویسی یا حذف نمی‌کند.\n\n'

# 1) Check package structure.
[[ -f "$ROOT/passenger_wsgi.py" ]] || fail "passenger_wsgi.py پیدا نشد. این فایل را کنار پوشه‌های backend و helper قرار بده."
[[ -f "$ROOT/backend/requirements.txt" ]] || fail "backend/requirements.txt پیدا نشد. ZIP نسخه V3 را کامل Extract کن."
[[ -f "$ROOT/backend/make_env.py" ]] || fail "backend/make_env.py پیدا نشد."
[[ -f "$ROOT/helper/vira_helper_bot.py" ]] || fail "فایل ربات Helper پیدا نشد."
cd "$ROOT"
ok "ساختار پروژه در $ROOT شناسایی شد."

# 2) Find the cPanel Python virtualenv.
PYTHON_BIN="${VIRA_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]] && command -v python >/dev/null 2>&1; then
  CANDIDATE="$(command -v python)"
  if "$CANDIDATE" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1; then
    PYTHON_BIN="$CANDIDATE"
  fi
fi
if [[ -z "$PYTHON_BIN" ]]; then
  for CANDIDATE in "$HOME"/virtualenv/vira_api/*/bin/python "$HOME"/virtualenv/vira_api/*/bin/python3; do
    if [[ -x "$CANDIDATE" ]]; then PYTHON_BIN="$CANDIDATE"; break; fi
  done
fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || fail "Virtualenv پیدا نشد. یک‌بار در cPanel → Setup Python App برنامه‌ای با Application root برابر vira_api و Python 3.11 یا 3.12 بساز؛ سپس دوباره این فایل را اجرا کن."
PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[[ "$PYTHON_VERSION" == "3.11" || "$PYTHON_VERSION" == "3.12" ]] || fail "نسخه Python این برنامه $PYTHON_VERSION است؛ برای این بسته Python 3.11 یا 3.12 انتخاب کن."
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' || fail "Python پیدا شده متعلق به Virtualenv نیست. مسیر Python App در cPanel را بررسی کن."
ok "Python $PYTHON_VERSION در Virtualenv پیدا شد: $PYTHON_BIN"

# 3) Install runtime + test dependencies. These are prerequisites for the required tests.
info "نصب کتابخانه‌های Backend و ابزار تست..."
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$ROOT/backend/requirements.txt"
"$PYTHON_BIN" -m pip install -r "$ROOT/backend/requirements-dev.txt"
"$PYTHON_BIN" -m pip install -r "$ROOT/helper/requirements.txt"
ok "وابستگی‌های Python نصب شدند."

# 4) Required tests BEFORE config/database/helper changes.
info "اجرای تست‌های Backend پیش از تغییر تنظیمات یا دیتابیس..."
(
  cd "$ROOT/backend"
  "$PYTHON_BIN" -m pytest tests -q
) || fail "تست‌ها موفق نبودند؛ هیچ تغییری در تنظیمات، دیتابیس یا Helper انجام نشد. خروجی خطا را بررسی کن."
ok "تست‌های Backend با موفقیت گذشتند."

# 5) Create .env only when absent (make_env refuses overwrite).
if [[ ! -f "$ROOT/.env" ]]; then
  info "ساخت امن فایل .env و تنظیمات پرداخت؛ اطلاعات محرمانه هنگام تایپ نمایش داده نمی‌شوند."
  "$PYTHON_BIN" "$ROOT/backend/make_env.py"
else
  warn "فایل .env از قبل وجود دارد؛ برای حفظ اطلاعات قبلی، بازنویسی نمی‌شود."
fi
[[ -f "$ROOT/.env" ]] || fail ".env ساخته نشد؛ بررسی کن اجرای make_env.py کامل شده باشد."
chmod 600 "$ROOT/.env"
ok "فایل .env موجود است و دسترسی آن محدود شد."

# 6) Safely add Helper token/admin IDs to .env and make the code read admin IDs from env.
info "تنظیم ربات Helper (توکن اختصاصی ربات Helper؛ نه ربات ورود/انتشار/سیگنال)."
ROOT="$ROOT" PYTHON_BIN="$PYTHON_BIN" "$PYTHON_BIN" - <<'PY'
import getpass, os, re, stat, tempfile
from pathlib import Path
root = Path(os.environ['ROOT'])
env_path = root / '.env'
text = env_path.read_text(encoding='utf-8')
lines = text.splitlines()
values = {}
for line in lines:
    s = line.strip()
    if s and not s.startswith('#') and '=' in s:
        k, v = s.split('=', 1)
        values[k.strip()] = v.strip().strip('"').strip("'")

existing_token = values.get('VIRA_HELPER_BOT_TOKEN', '')
if existing_token:
    token = getpass.getpass('توکن Helper قبلاً تنظیم شده؛ برای حفظ آن Enter، یا توکن جدید را وارد کن: ').strip()
else:
    token = getpass.getpass('توکن اختصاصی ربات Helper (برای رد شدن Enter): ').strip()
if token and not re.fullmatch(r'[0-9]{5,12}:[A-Za-z0-9_-]{20,}', token):
    raise SystemExit('فرمت توکن Helper معتبر نیست؛ تنظیمات ذخیره نشد.')

existing_ids = values.get('VIRA_HELPER_ADMIN_IDS', '')
ids = input(f'شناسه عددی تلگرام مدیران، با کاما جدا کن [{existing_ids or "فعلاً خالی"}]: ').strip() or existing_ids
if ids:
    parts = [x.strip() for x in ids.split(',') if x.strip()]
    if not parts or any(not x.isdigit() or int(x) <= 0 for x in parts):
        raise SystemExit('شناسه مدیر باید فقط عدد مثبت باشد؛ تنظیمات ذخیره نشد.')
    ids = ','.join(dict.fromkeys(parts))

updates = {}
if token: updates['VIRA_HELPER_BOT_TOKEN'] = token
if ids: updates['VIRA_HELPER_ADMIN_IDS'] = ids
if updates:
    out = []
    seen = set()
    for line in lines:
        stripped = line.strip()
        key = stripped.split('=',1)[0].strip() if stripped and not stripped.startswith('#') and '=' in stripped else None
        if key in updates:
            if key not in seen: out.append(f'{key}={updates[key]}'); seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen: out.append(f'{key}={value}')
    fd, tmp = tempfile.mkstemp(prefix='.env.', dir=str(root), text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out).rstrip() + '\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, env_path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    os.chmod(env_path, 0o600)

# Convert the V3 placeholder ADMIN_IDS declaration to environment-based config once.
helper = root / 'helper' / 'vira_helper_bot.py'
src = helper.read_text(encoding='utf-8')
old = 'ADMIN_IDS = {123456789}  # Replace with your numeric Telegram user ID(s).'
new = 'ADMIN_IDS = {int(x.strip()) for x in os.getenv("VIRA_HELPER_ADMIN_IDS", "123456789").split(",") if x.strip().isdigit()}  # configured in .env'
if old in src:
    backup = helper.with_suffix('.py.pre_oneclick.bak')
    if not backup.exists(): backup.write_text(src, encoding='utf-8')
    helper.write_text(src.replace(old, new, 1), encoding='utf-8')
elif 'VIRA_HELPER_ADMIN_IDS' not in src:
    raise SystemExit('ساختار خط ADMIN_IDS در Helper با نسخه مورد انتظار فرق دارد؛ فایل دست‌کاری نشد. لطفاً فایل V3 اصلی را بررسی کن.')
print('تنظیمات Helper بررسی شد؛ مقدار توکن چاپ نمی‌شود.')
PY
chmod 600 "$ROOT/.env"

# 7) Initialize/migrate DB without deleting existing data.
info "آماده‌سازی دیتابیس و اجرای migrationهای لازم (بدون حذف داده‌های موجود)..."
(
  cd "$ROOT"
  PYTHONPATH="$ROOT/backend" "$PYTHON_BIN" -c 'from app.db import init_db, db_path; init_db(); print("DATABASE:", db_path())'
) || fail "آماده‌سازی دیتابیس ناموفق بود."
ok "دیتابیس آماده است."

# 8) Package PHP payment files for the separate blackcreator.ir hosting account.
if [[ ! -f "$ROOT/php_payment/config.php" ]]; then
  ROOT="$ROOT" "$PYTHON_BIN" - <<'PYREBUILD'
import os
from pathlib import Path
root = Path(os.environ['ROOT'])
values = {}
for line in (root / '.env').read_text(encoding='utf-8').splitlines():
    s = line.strip()
    if s and not s.startswith('#') and '=' in s:
        k, v = s.split('=', 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
secret = values.get('PAY_SHARED_SECRET', '')
base = values.get('PAY_BASE_URL', '')
merchant = values.get('ZARINPAL_MERCHANT_ID', '')
if secret and base.startswith('https://'):
    target = root / 'php_payment' / 'config.php'
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "<?php\nreturn [\n    'SECRET' => %s,\n    'BASE_URL' => %s,\n    'MERCHANT_ID' => %s,\n    'RETURN_URL' => 'vira://payment?code={code}',\n];\n" % (repr(secret), repr(base), repr(merchant))
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write(content)
PYREBUILD
fi
if [[ -f "$ROOT/php_payment/config.php" ]]; then
  OUT="$ROOT/outbound"
  mkdir -p "$OUT"
  ROOT="$ROOT" OUT="$OUT" "$PYTHON_BIN" - <<'PY'
import os, zipfile
from pathlib import Path
root, out = Path(os.environ['ROOT']), Path(os.environ['OUT'])
src = root / 'php_payment'
files = ['pay.php','back.php','common.php','.htaccess','config.php','store/.htaccess']
missing = [x for x in files if not (src/x).is_file()]
if missing: raise SystemExit('فایل‌های بسته پرداخت ناقص است: ' + ', '.join(missing))
zip_path = out / 'VIRA_pay_upload.zip'
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
    for name in files: z.write(src/name, arcname=name)
os.chmod(zip_path, 0o600)
print(zip_path)
PY
  ok "بسته درگاه PHP آماده شد؛ این ZIP حاوی config محرمانه است و باید فقط امن به هاست blackcreator.ir منتقل شود."
else
  warn "config.php پرداخت پیدا نشد؛ بسته انتقال درگاه ساخته نشد."
fi

# 9) Passenger restart trigger.
mkdir -p "$ROOT/tmp"
touch "$ROOT/tmp/restart.txt"
ok "درخواست Restart برای Passenger ثبت شد."

# 10) Write Cron command file (do not add duplicate cron jobs automatically).
mkdir -p "$ROOT/outbound"
CRON_LINE="* * * * * $PYTHON_BIN $ROOT/backend/run_scheduler.py"
printf '%s\n' "$CRON_LINE" > "$ROOT/outbound/vira_cron_line.txt"
chmod 600 "$ROOT/outbound/vira_cron_line.txt"

# 11) Optional webhook registration (only the dedicated VIRA login bot).
printf '\n'
warn "اسکریپت ثبت Webhook، webhook قبلی ربات ورود را جایگزین می‌کند و طبق کد فعلی، آپدیت‌های معلق را هم حذف می‌کند. فقط برای ربات اختصاصی VIRA تأیید کن؛ از ربات سیگنال یا Helper استفاده نکن."
read -r -p "آیا ربات ورود اختصاصی VIRA است و اجازه ثبت webhook را می‌دهی؟ (y/N): " DO_WEBHOOK
if [[ "$DO_WEBHOOK" =~ ^[Yy]$ ]]; then
  "$PYTHON_BIN" "$ROOT/backend/set_webhook.py" || warn "ثبت webhook شکست خورد؛ لاگ خطا را بررسی کن."
else
  info "ثبت webhook رد شد؛ بعداً می‌توانی جداگانه انجامش بدهی."
fi

# 12) Optional Helper start. Uses a dedicated bot token; may not persist on shared hosting.
printf '\n'
if grep -q '^VIRA_HELPER_BOT_TOKEN=.' "$ROOT/.env"; then
  read -r -p "Helper token در .env وجود دارد. آیا این توکن فقط مخصوص VIRA Helper است و می‌خواهی همین حالا اجرا شود؟ (y/N): " START_HELPER
  if [[ "$START_HELPER" =~ ^[Yy]$ ]]; then
    if [[ -f "$ROOT/helper.pid" ]] && kill -0 "$(cat "$ROOT/helper.pid" 2>/dev/null || echo 0)" 2>/dev/null; then
      warn "Helper ظاهراً از قبل اجراست؛ اجرای تکراری انجام نشد."
    else
      nohup "$PYTHON_BIN" -u "$ROOT/helper/vira_helper_bot.py" >> "$ROOT/helper.log" 2>&1 < /dev/null &
      echo $! > "$ROOT/helper.pid"
      sleep 4
      if kill -0 "$(cat "$ROOT/helper.pid")" 2>/dev/null; then
        ok "فرمان اجرای Helper ارسال شد. لاگ: $ROOT/helper.log"
      else
        warn "Helper متوقف شد؛ آخرین لاگ:"
        tail -n 35 "$ROOT/helper.log" 2>/dev/null || true
      fi
    fi
  else
    info "Helper اجرا نشد؛ هر زمان آماده بودی دوباره همین فایل را اجرا کن."
  fi
else
  warn "توکن Helper در .env نیست؛ برای اجرای Helper ابتدا توکن اختصاصی بساز و دوباره این فایل را اجرا کن."
fi

# 13) Health check.
printf '\n'
info "بررسی HTTPS و سلامت API در $APP_URL/health ..."
if command -v curl >/dev/null 2>&1 && curl --connect-timeout 5 --max-time 15 -fsS "$APP_URL/health"; then
  printf '\n'
  ok "API از بیرون پاسخ داد."
else
  warn "فعلاً پاسخ سالم از API دریافت نشد. از Setup Python App مطمئن شو App ساخته و Restart شده و SSL/DNS فعال است. این موضوع لزوماً به معنی خرابی نصب نیست."
fi

printf '\n=====================================================\n'
printf '               خلاصه و قدم بعدی\n'
printf '=====================================================\n'
printf '۱) فایل outbound/VIRA_pay_upload.zip را فقط روی هاست blackcreator.ir در public_html/pay استخراج کن.\n'
printf '   این ZIP دارای config محرمانه پرداخت است؛ عمومی‌اش نکن.\n'
printf '۲) محتوای outbound/vira_cron_line.txt را در cPanel → Cron Jobs اضافه کن (هر دقیقه).\n'
printf '۳) سلامت API: %s/health\n' "$APP_URL"
printf '۴) گزارش Helper: tail -n 40 %s/helper.log\n' "$ROOT"
printf '۵) برای توقف Helper: kill "$(cat %s/helper.pid)"  (در صورت وجود PID)\n' "$ROOT"
printf '\n'
warn "cPanel Setup Python App، DNS/SSL، انتقال فایل پرداخت به هاست دوم و ساخت APK خودکار از این اسکریپت قابل انجام نیستند."
warn "روی هاست اشتراکی، nohup تضمین اجرای دائمی Helper نیست؛ برای پایداری باید Background Worker رسمی یا VPS داشته باشی."
ok "فرایند راه‌اندازی خودکار به پایان رسید."
