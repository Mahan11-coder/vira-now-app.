# VIRA Complete — بسته یکپارچه

نسخه کامل شامل:
- **Frontend** با تم کریستالی Phase E + اتصال واقعی API
- **Backend** Flask V3 (ورود تلگرام، کانال، پرداخت، اشتراک، پنل مدیر، AI)
- **Helper Bot** (خواندن توکن و ادمین از `.env`)
- **PHP Payment** برای زرین‌پال
- **GitHub Actions** برای ساخت APK دیباگ

API پیش‌فرض اپ: `https://api.blackcreator.ir`

---

## ۱) نصب Backend روی cPanel

1. کل این پوشه را در مسیر مثلاً `/home/USER/vira_api` آپلود و extract کن طوری که `passenger_wsgi.py` در ریشه باشد.
2. Setup Python App:
   - Python 3.11 یا 3.12
   - Application root: `vira_api`
   - URL: `api.blackcreator.ir`
   - Startup: `passenger_wsgi.py` / entry: `application`
3. Terminal:
   ```bash
   cd ~/vira_api
   bash VIRA_OneClick_Setup.sh
   ```
4. Cron از فایل `outbound/vira_cron_line.txt`
5. ZIP پرداخت را فقط روی هاست `blackcreator.ir` در `public_html/pay` بگذار.

چک سلامت:
```bash
curl -sS https://api.blackcreator.ir/health
```

### متغیرهای حیاتی `.env`
- `AUTH_BOT_TOKEN` + `AUTH_BOT_USERNAME` + `TELEGRAM_WEBHOOK_SECRET`
- `ADMIN_PASSWORD_HASH` (با `backend/make_admin_hash.py`)
- `PAY_SHARED_SECRET` + `PAY_BASE_URL` + `ZARINPAL_MERCHANT_ID`
- `ZARINPAL_SANDBOX=1` تا پایان تست
- `VIRA_HELPER_BOT_TOKEN` + `VIRA_HELPER_ADMIN_IDS` (اختیاری)

---

## ۲) ساخت APK

### روش GitHub Actions (پیشنهادی)
1. این مخزن را روی GitHub بگذار (Private بهتر است).
2. Actions → **Build VIRA Android APK** → Run workflow
3. `api_base_url` را `https://api.blackcreator.ir` بگذار
4. Artifact را دانلود کن

### روش محلی
```bash
cd frontend
npm install
VITE_API_BASE_URL=https://api.blackcreator.ir npm run build
npx cap add android   # فقط بار اول
npx cap sync android
cd android && ./gradlew assembleDebug
# خروجی: app/build/outputs/apk/debug/app-debug.apk
```

---

## ۳) قابلیت‌های اپ

| بخش | وضعیت |
|------|--------|
| UI تم تیره کریستالی | فعال |
| ورود تلگرام (deep-link + poll) | فعال — نیاز ربات و webhook |
| خدمات از API | فعال |
| کانال‌ها / پیش‌نویس پست | فعال پس از ورود |
| کیف پول / اشتراک / redeem | فعال پس از ورود |
| AI generate | فعال اگر کلید سرور ست شود |
| پنل مدیر (۷ بار لمس لوگو) | فعال |

---

## ۴) امنیت
- هیچ توکنی داخل Frontend/APK نیست
- فقط `VITE_API_BASE_URL` عمومی است
- `.env` در Git نگذار

---

## ۵) ترتیب راه‌اندازی پیشنهادی
1. Backend + `/health` سبز
2. Webhook ربات ورود
3. ساخت APK با همین API
4. تست ورود روی گوشی
5. تنظیم قیمت اشتراک در پنل مدیر
6. تست Sandbox پرداخت
