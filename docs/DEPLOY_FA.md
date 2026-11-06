# راه‌اندازی VIRA Backend روی cPanel (نسخه V3)

این بسته، **Backend + پنل API + فایل‌های PHP درگاه + Helper** است. سورس React/Capacitor و APK در این بسته نیست؛ بنابراین این بسته به‌تنهایی APK جدید یا صفحه شارژ داخل اپ تولید نمی‌کند.

## ۰) پیش‌نیازهای هاست

- Python 3.11 یا 3.12 در cPanel Setup Python App
- افزونه‌های Python قابل نصب با pip: `Flask` و `python-dotenv`
- PHP 7.4 یا بالاتر روی هاست درگاه، به‌همراه افزونه `cURL` و `JSON`
- HTTPS معتبر برای `api.blackcreator.ir` و `blackcreator.ir`
- پوشه API خارج از `public_html` و خارج از Document Root

## ۱) بارگذاری Backend

1. ZIP نسخه V3 را در File Manager مستقیماً در مسیر `/home/novacom/vira_api` آپلود و Extract کن. محتویات ZIP از ریشه شروع می‌شوند؛ نباید یک پوشه اضافی `VIRA_work` ایجاد شود.
2. مسیر نهایی باید این فایل را داشته باشد: `/home/novacom/vira_api/passenger_wsgi.py`.
3. در cPanel → **Setup Python App** یک برنامه بساز:
   - Python: 3.11 یا 3.12
   - Application root: `vira_api`
   - Application URL: `api.blackcreator.ir`
   - Startup file: `passenger_wsgi.py`
   - Entry point: `application`
4. دستور فعال‌سازی Virtualenv که cPanel نشان می‌دهد را در Terminal اجرا کن؛ سپس:

   ```bash
   cd ~/vira_api
   pip install -r backend/requirements.txt
   python backend/make_env.py
   ```

5. در `make_env.py` رمز پنل، ربات ورود، ربات انتشار و تنظیمات درگاه را وارد کن. حالت پرداخت را فعلاً روی **۱ (Sandbox)** نگه دار. برای کلید AI می‌توانی فعلاً Enter بزنی.
6. فایل `.env` و `php_payment/config.php` با دسترسی محدود ساخته می‌شوند. هیچ‌کدام را در GitHub، APK یا پوشه عمومی وب قرار نده.
7. در Setup Python App روی **Restart** بزن.
8. این آدرس را باز کن:

   `https://api.blackcreator.ir/health`

   پاسخ مورد انتظار: JSON دارای `"ok": true`.

## ۲) ثبت Webhook ربات ورود

پس از تنظیم `.env` و فعال شدن HTTPS، فقط یک بار اجرا کن:

```bash
cd ~/vira_api
python backend/set_webhook.py
```

اگر قبلاً همین ربات روی webhook دیگری بوده، ثبت مجدد این دستور، webhook آن ربات را به VIRA منتقل می‌کند. از توکن ربات سیگنال یا ربات Helper استفاده نکن.

## ۳) نصب فایل‌های درگاه روی blackcreator.ir

1. روی هاست ایرانی، پوشه `pay` را زیر Document Root دامنه `blackcreator.ir` بساز.
2. این فایل‌ها را در `public_html/pay/` کپی کن:
   - `php_payment/pay.php`
   - `php_payment/back.php`
   - `php_payment/common.php`
   - `php_payment/.htaccess`
   - `php_payment/store/.htaccess` داخل پوشه `store`
   - فایل `php_payment/config.php` که `make_env.py` روی هاست API ساخته است.
3. پوشه `store` باید قابل نوشتن توسط PHP باشد؛ ترجیحاً سطح دسترسی `700` یا `750` با مالک صحیح هاست. فایل‌های داخل آن نباید از وب قابل دریافت باشند.
4. افزونه PHP `cURL` باید فعال باشد. اگر فعال نباشد، کد به‌جای Fatal Error پیام خطای کنترل‌شده می‌دهد، اما پرداخت انجام نمی‌شود.
5. مرچنت زرین‌پال باید برای دامنه `blackcreator.ir` ثبت شده باشد.
6. پس از انتقال امن `config.php` به هاست ایرانی، نسخه آن را از `/home/novacom/vira_api/php_payment/config.php` حذف کن. فایل `.env` روی هاست API را حذف نکن.
7. آدرس `PAY_BASE_URL` باید دقیقاً به پوشه درگاه اشاره کند؛ مقدار پیش‌فرض `https://blackcreator.ir/pay` است.

## ۴) آزمایش پرداخت

- ابتدا `ZARINPAL_SANDBOX=1` باشد.
- با یک حساب تست، فاکتور بساز، وارد درگاه آزمایشی شو، پرداخت را کامل کن و بازگشت به اپ/کد دستی را بررسی کن.
- سپس در API این مسیرها را بررسی کن:
  - `POST /api/payments/invoices`
  - `GET /api/payments/invoices/<id>`
  - `POST /api/payments/redeem`
- پس از موفقیت کامل Sandbox، برای پرداخت واقعی مقدار `ZARINPAL_SANDBOX=0` را در `.env` تغییر بده و Python App را Restart کن.
- پرداخت واقعی یا Sandbox هنوز از این محیط قابل تأیید نیست؛ پیش از دریافت پول واقعی، حتماً یک تراکنش واقعی کم‌مبلغ و قابل استرداد را با صاحب مرچنت هماهنگ کن.

## ۵) تنظیم قیمت اشتراک

در پنل مدیریت، کلیدهای زیر را با عدد صحیح تومان تنظیم کن:

- `subscription_monthly_price`
- `subscription_quarterly_price`
- `subscription_yearly_price`

محدوده مجاز هر قیمت: ۱٬۰۰۰ تا ۵۰۰٬۰۰۰٬۰۰۰ تومان. مقدار خالی یعنی آن طرح غیرفعال است.

APIهای جدید:

- `GET /api/public/subscription-plans` — فهرست طرح‌ها و قیمت‌ها
- `GET /api/subscriptions` — اشتراک‌های حساب واردشده
- `POST /api/subscriptions/purchase` با JSON مانند `{"plan_code":"monthly"}` — خرید با کسر اتمیک موجودی

خرید اشتراک، کسر موجودی و ثبت سابقه خرید در یک تراکنش SQLite انجام می‌شوند. در صورت کافی نبودن موجودی، پاسخ `402` با قالب `{"detail":"..."}` است.

## ۶) زمان‌بندی انتشار پست‌ها

cPanel → Cron Jobs، هر دقیقه:

```cron
* * * * * /home/novacom/virtualenv/vira_api/3.11/bin/python /home/novacom/vira_api/backend/run_scheduler.py
```

مسیر Python را با مسیر واقعی Virtualenv نمایش‌داده‌شده در cPanel جایگزین کن.

## ۷) اجرای VIRA Helper

Helper از همان دیتابیس استفاده می‌کند، اما باید پردازش مستقل و همیشه‌روشن داشته باشد. روی VPS یا سروری که systemd و سرویس دائمی در اختیار می‌گذارد:

1. `httpx` و `python-dotenv` را در همان Virtualenv نصب کن:

   ```bash
   pip install -r helper/requirements.txt
   ```

2. توکن اختصاصی Helper را فقط در `.env` سرور با نام `VIRA_HELPER_BOT_TOKEN` اضافه کن.
3. در `helper/vira_helper_bot.py` مقدار `ADMIN_IDS` را به شناسه عددی مدیران واقعی تغییر بده.
4. نمونه سرویس در `helper/vira-helper.service.example` است؛ مسیر کاربر و Virtualenv را با سرور واقعی هماهنگ کن.

روی هاست اشتراکی cPanel معمولاً systemd یا پردازش دائمی تضمین نمی‌شود؛ در این حالت Helper را نمی‌توان با Cron هر دقیقه به‌صورت مطمئن اجرا کرد. برای سرویس پایدار، VPS یا قابلیت رسمی Background Worker لازم است.

در اتصال کانال از طریق Helper، هم ادمین/مالک بودن ربات و هم ادمین/مالک بودن کاربر بررسی می‌شود.

## ۸) تست و عیب‌یابی

قبل از هر تغییر روی سرور، بکاپ بگیر. اجرای تست‌ها:

```bash
cd ~/vira_api
pip install -r backend/requirements-dev.txt
cd backend
python -m pytest tests -q
```

در محیطی که PHP CLI و افزونه cURL دارد، تست‌های پرداخت انتهابه‌انتها هم اجرا می‌شوند؛ بدون افزونه cURL این بخش‌ها Skip می‌شوند، اما برای درگاه واقعی cURL روی هاست PHP الزامی است. بعد از هر استقرار، دست‌کم این موارد را بررسی کن:

- `https://api.blackcreator.ir/health`
- ورود تلگرام و callback ربات
- اتصال کانال با اکانت ادمین کانال
- ساخت و انتشار یک پست آزمایشی
- فاکتور Sandbox و شارژ کیف پول فقط یک بار
- ردشدن Authority متعلق به فاکتور دیگر
- خرید اشتراک با موجودی کافی و ناکافی

تمام کلیدها و توکن‌ها فقط روی سرور می‌مانند. قرارداد خطای API همیشه `{"detail":"..."}` است.

## ۹) راه‌انداز نیمه‌خودکار OneClick

برای کاهش ورود دستی دستورها، فایل `VIRA_OneClick_Setup.sh` در ریشه بسته قرار گرفته است. این اسکریپت جایگزین ساخت Python App، تنظیم DNS/SSL یا ساخت APK نیست؛ ابتدا باید Python App را از طریق cPanel بسازی و ZIP را مستقیماً در Application root استخراج کنی.

پس از آن در Terminal:

```bash
cd ~/vira_api
bash VIRA_OneClick_Setup.sh
```

اسکریپت Python 3.11/3.12 مربوط به Virtualenv همین برنامه را پیدا می‌کند، وابستگی‌های Backend و Helper و تست را نصب می‌کند، `pytest` را اجرا می‌کند و در صورت شکست تست‌ها برای مراحل پیکربندی و دیتابیس جلو نمی‌رود. اگر `.env` موجود نباشد، راه‌انداز `backend/make_env.py` را برای ساخت امن آن اجرا می‌کند؛ اگر `.env` موجود باشد، آن را بازنویسی نمی‌کند. سپس ورودی Helper را برای دریافت توکن اختصاصی و شناسه مدیران اجرا می‌کند، دیتابیس را initialize/migrate می‌کند، بسته انتقال PHP را در `outbound/VIRA_pay_upload.zip` می‌سازد (این فایل حاوی secret است و باید محرمانه منتقل شود)، فایل restart Passenger را لمس می‌کند و خط Cron را در `outbound/vira_cron_line.txt` می‌نویسد.

ثبت Webhook ربات ورود و اجرای Helper اختیاری و با تأیید صریح انجام می‌شود. اسکریپت از توکن ربات سیگنال استفاده نمی‌کند و نباید توکن ربات سیگنال یا Helper را به‌عنوان توکن ورود وارد کنی. اجرای Helper با `nohup` روی هاست اشتراکی پایدار تضمین نمی‌شود.

**پیش از اجرا:** مطمئن شو ریشه ZIP دقیقاً `/home/novacom/vira_api` است و در cPanel یک Python App با Application root برابر `vira_api`، Startup file برابر `passenger_wsgi.py` و Entry point برابر `application` ساخته‌ای. قبل از اجرای مجدد، از `.env` و `backend/data/` نسخه پشتیبان بگیر. اسکریپت عمداً از ساخت/تغییر تنظیمات در صورت شکست تست‌ها خودداری می‌کند.
