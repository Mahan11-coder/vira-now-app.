# راهنمای اسکریپت نصب VIRA OneClick

این ابزار نصب Backend روی هاست cPanel را ساده می‌کند؛ اما نمی‌تواند وارد پنل وب cPanel شود، DNS/SSL را تغییر دهد، سرویس دائمی تضمین‌شده بسازد یا APK تولید کند.

## قبل از اجرا، فقط یک بار در cPanel

1. در File Manager فایل `VIRA_Server_Deploy_v3.zip` را در `/home/novacom/vira_api` استخراج کن. فایل `passenger_wsgi.py` باید مستقیماً داخل همین پوشه باشد.
2. در Software → Setup Python App یک برنامه با این مشخصات بساز:
   - Python: 3.11 یا 3.12
   - Application root: `vira_api`
   - Application URL: `api.blackcreator.ir`
   - Startup file: `passenger_wsgi.py`
   - Entry point: `application`
3. فایل `VIRA_OneClick_Setup.sh` را هم در ریشه `/home/novacom/vira_api` قرار بده.

## اجرای راه‌انداز

در cPanel → Terminal اجرا کن:

```bash
cd ~/vira_api
bash VIRA_OneClick_Setup.sh
```

اگر `Virtualenv پیدا نشد` دیدی، به Setup Python App برگرد و بررسی کن برنامه با Application root دقیقاً `vira_api` ایجاد شده باشد. مسیر Python/Virtualenv را از همان صفحه بگیر؛ در صورت لزوم می‌توانی آن را به شکل زیر مشخص کنی:

```bash
VIRA_PYTHON=/home/novacom/virtualenv/vira_api/3.11/bin/python bash VIRA_OneClick_Setup.sh
```

مسیر نمونه است؛ مسیر دقیق نمایش‌داده‌شده در cPanel خودت را قرار بده.

## اطلاعاتی که در طول اجرا خواسته می‌شود

- رمز قوی مدیر پنل
- توکن و username ربات اختصاصی ورود VIRA
- توکن ربات انتشار کانال (اختیاری، در صورت نیاز)
- مرچنت زرین‌پال (اختیاری تا زمان آماده‌شدن درگاه)
- آدرس پوشه پرداخت؛ پیش‌فرض `https://blackcreator.ir/pay`
- حالت پرداخت؛ فعلاً `1` برای Sandbox
- کلید هوش مصنوعی (اختیاری)
- توکن اختصاصی ربات Helper (در prompt جداگانه)
- شناسه عددی تلگرام مدیران Helper

هیچ توکن یا کلیدی را در APK، GitHub، این چت یا فایل ZIP عمومی قرار نده. اطلاعات محرمانه فقط در فایل `.env` با دسترسی محدود روی سرور قرار می‌گیرند.

## کارهای اسکریپت

1. بررسی ساختار ZIP و Virtualenv.
2. نصب نیازمندی‌های Backend، تست و Helper.
3. اجرای `cd backend && python -m pytest tests -q` با همان Python محیط مجازی.
4. اگر تست‌ها شکست بخورند، اسکریپت متوقف می‌شود و مراحل تنظیمات، تغییر Helper و initialize دیتابیس را انجام نمی‌دهد.
5. اگر `.env` وجود نداشته باشد، `backend/make_env.py` را اجرا می‌کند؛ فایل موجود بازنویسی نمی‌شود.
6. دریافت توکن اختصاصی و شناسه مدیران Helper و تنظیم آن‌ها در `.env`.
7. آماده‌سازی migrationهای دیتابیس بدون حذف داده‌های موجود.
8. تولید `outbound/VIRA_pay_upload.zip` برای انتقال امن فایل‌های PHP به هاست `blackcreator.ir`.
9. ایجاد Passenger restart marker و نوشتن خط Cron در `outbound/vira_cron_line.txt`.
10. پرسیدن تأیید پیش از ثبت Webhook ربات ورود یا اجرای Helper.
11. بررسی `https://api.blackcreator.ir/health` در انتهای اجرا.

## فایل‌های خروجی مهم

- `outbound/VIRA_pay_upload.zip`: شامل فایل تنظیمات محرمانه درگاه است؛ فقط از مسیر امن به هاست مقصد منتقل شود و عمومی نشود.
- `outbound/vira_cron_line.txt`: خط Cron پیشنهادی برای زمان‌بندی انتشارها؛ آن را فقط یک بار در Cron Jobs ثبت کن.
- `helper.log`: لاگ اجرای Helper.
- `helper.pid`: شناسه پردازش Helper.

## مواردی که خودکار انجام نمی‌شود

- ساخت Python App در پنل cPanel
- تنظیم DNS و صدور/تمدید SSL
- انتقال فایل‌های PHP به هاست دوم `blackcreator.ir`
- ساخت یا انتشار APK؛ این بسته سورس React/Capacitor ندارد
- تضمین اجرای ۲۴/۷ Helper با `nohup` روی هاست اشتراکی
- تست واقعی ربات تلگرام یا تراکنش واقعی زرین‌پال

## اجرای مجدد و حفظ داده‌ها

اسکریپت طوری نوشته شده که `.env` و دیتابیس موجود را حذف یا بازنویسی نکند. با این حال پیش از هر اجرای مجدد، از این موارد نسخه پشتیبان بگیر:

```bash
cd ~/vira_api
cp -a .env .env.backup_$(date +%F_%H%M)
cp -a backend/data backend_data_backup_$(date +%F_%H%M)
```

اگر از قبل یک Helper با همین توکن فعال است، اجرای دوباره آن می‌تواند باعث خطای `409 Conflict` شود؛ قبل از اجرای مجدد، پردازش قبلی را بررسی و از اجرای دو polling هم‌زمان با یک توکن خودداری کن.
