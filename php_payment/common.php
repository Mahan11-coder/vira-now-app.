<?php
// VIRA — توابع مشترک pay.php و back.php (PHP >= 7.4، فقط curl).
declare(strict_types=1);

function vira_config(): array
{
    static $cfg = null;
    if ($cfg === null) {
        $file = __DIR__ . '/config.php';
        if (!is_file($file)) {
            vira_fail(500, 'پیکربندی درگاه کامل نیست.');
        }
        $cfg = require $file;
        if (!is_array($cfg) || strlen((string)($cfg['SECRET'] ?? '')) < 24) {
            vira_fail(500, 'کلید مشترک (SECRET) باید حداقل ۲۴ نویسه باشد.');
        }
    }
    return $cfg;
}

function vira_h(string $s): string
{
    return htmlspecialchars($s, ENT_QUOTES, 'UTF-8');
}

function vira_page(int $status, string $title, string $bodyHtml, string $extraHead = ''): void
{
    http_response_code($status);
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store');
    // عمداً Referrer-Policy نمی‌گذاریم: زرین‌پال باید دامنه‌ی هاست را در Referer ببیند.
    echo '<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
        . '<meta name="viewport" content="width=device-width,initial-scale=1">'
        . $extraHead . '<title>' . vira_h($title) . '</title>'
        . '<style>body{font-family:tahoma,sans-serif;background:#0f1115;color:#eee;margin:0;padding:24px;line-height:1.9}'
        . '.c{max-width:460px;margin:8vh auto;background:#1a1d24;border-radius:16px;padding:24px;text-align:center}'
        . 'a.b{display:inline-block;background:#7c5cff;color:#fff;padding:12px 22px;border-radius:12px;text-decoration:none;margin:8px 0}'
        . 'code{display:block;word-break:break-all;direction:ltr;background:#0f1115;padding:10px;border-radius:8px;margin:10px 0}'
        . '</style></head><body><div class="c">' . $bodyHtml . '</div></body></html>';
    exit;
}

function vira_fail(int $status, string $message): void
{
    vira_page($status, 'خطا', '<h3>⚠️ ' . vira_h($message) . '</h3><p>به برنامه برگرد و دوباره تلاش کن.</p>');
}

function vira_hmac(string $text): string
{
    return hash_hmac('sha256', $text, (string)vira_config()['SECRET']);
}

function vira_link_sig(string $d, string $u, string $t, string $m, string $b, string $sb): string
{
    return substr(vira_hmac("$d|$u|$t|$m|$b|$sb"), 0, 32);
}

function vira_return_sig(string $id, string $ref, string $amount): string
{
    return substr(vira_hmac("ok|$id|$ref|$amount"), 0, 16);
}

/** پارامترهای لینک را با regex از QUERY_STRING می‌خواند و امضا را بررسی می‌کند. */
function vira_read_link(): array
{
    $qs = (string)($_SERVER['QUERY_STRING'] ?? '');
    $get = static function (string $key, string $pattern) use ($qs): string {
        return preg_match('/(?:^|&)' . $key . '=(' . $pattern . ')(?=&|$)/', $qs, $m) ? $m[1] : '';
    };
    $p = [
        'd' => $get('d', '\d{1,12}'),
        'u' => $get('u', '\d{1,12}'),
        't' => $get('t', '\d{1,9}'),
        'm' => $get('m', '[A-Za-z0-9-]{20,64}'),
        'b' => $get('b', '[A-Za-z0-9_-]{1,32}'),
        'sb' => $get('sb', '[01]'),
        's' => $get('s', '[0-9a-f]{32}'),
    ];
    foreach ($p as $v) {
        if ($v === '') {
            vira_fail(400, 'لینک ناقص یا نامعتبر است.');
        }
    }
    $expected = vira_link_sig($p['d'], $p['u'], $p['t'], $p['m'], $p['b'], $p['sb']);
    if (!hash_equals($expected, $p['s'])) {
        vira_fail(403, 'امضای لینک معتبر نیست.');
    }
    $cfg = vira_config();
    if (!empty($cfg['MERCHANT_ID']) && !hash_equals((string)$cfg['MERCHANT_ID'], $p['m'])) {
        vira_fail(403, 'مرچنت نامعتبر است.');
    }
    $amount = (int)$p['t'];
    if ($amount < 1000 || $amount > 500000000) {
        vira_fail(400, 'مبلغ نامعتبر است.');
    }
    return $p;
}

function vira_zp_base(array $p): string
{
    $cfg = vira_config();
    if (!empty($cfg['ZP_BASE'])) {           // برای تست با mock
        return rtrim((string)$cfg['ZP_BASE'], '/');
    }
    return $p['sb'] === '1' ? 'https://sandbox.zarinpal.com' : 'https://payment.zarinpal.com';
}

/** درخواست JSON به زرین‌پال. در خطا null برمی‌گرداند. */
function vira_zp_call(string $url, array $payload): ?array
{
    // روی هاستی که cURL فعال نیست، به‌جای Fatal Error پاسخ کنترل‌شده بده.
    if (!function_exists('curl_init')) {
        return null;
    }
    $ch = curl_init($url);
    if ($ch === false) {
        return null;
    }
    $body = json_encode($payload, JSON_UNESCAPED_UNICODE);
    if (!is_string($body)) {
        curl_close($ch);
        return null;
    }
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $body,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Accept: application/json'],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_TIMEOUT => 25,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ]);
    $raw = curl_exec($ch);
    $http = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if (!is_string($raw) || $raw === '' || $http < 200 || $http >= 300) {
        return null;
    }
    $json = json_decode($raw, true);
    return is_array($json) ? $json + ['_http' => $http] : null;
}

function vira_store_dir(): string
{
    $dir = __DIR__ . '/store';
    if (!is_dir($dir) && !@mkdir($dir, 0700, true) && !is_dir($dir)) {
        vira_fail(500, 'فضای ثبت امن تراکنش‌ها در دسترس نیست.');
    }
    return $dir;
}

function vira_authority_file(string $sig, string $authority): string
{
    if (!preg_match('/^[0-9a-f]{32}$/', $sig) || !preg_match('/^[A-Za-z0-9]{10,60}$/', $authority)) {
        vira_fail(400, 'شناسه تراکنش نامعتبر است.');
    }
    return vira_store_dir() . '/a_' . $sig . '_' . $authority . '.json';
}

/** Authority برای همان لینک امضاشده ذخیره می‌شود؛ به فاکتور دیگری قابل انتقال نیست. */
function vira_save_authority(array $p, string $authority): void
{
    $record = [
        'd' => $p['d'], 'u' => $p['u'], 't' => $p['t'], 'm' => $p['m'],
        'b' => $p['b'], 'sb' => $p['sb'], 's' => $p['s'],
        'authority' => $authority, 'created_at' => time(),
    ];
    $path = vira_authority_file($p['s'], $authority);
    $encoded = json_encode($record, JSON_UNESCAPED_UNICODE);
    if (!is_string($encoded) || file_put_contents($path, $encoded, LOCK_EX) === false) {
        vira_fail(500, 'ثبت امن تراکنش انجام نشد؛ دوباره تلاش کن.');
    }
    @chmod($path, 0600);
    $latest = vira_store_dir() . '/l_' . $p['s'] . '.txt';
    if (file_put_contents($latest, $authority, LOCK_EX) === false) {
        vira_fail(500, 'ثبت وضعیت تراکنش انجام نشد؛ دوباره تلاش کن.');
    }
    @chmod($latest, 0600);
}

function vira_known_authority(array $p, string $authority): bool
{
    $path = vira_authority_file($p['s'], $authority);
    if (!is_file($path)) {
        return false;
    }
    $record = json_decode((string)file_get_contents($path), true);
    if (!is_array($record)) {
        return false;
    }
    foreach (['d', 'u', 't', 'm', 'b', 'sb', 's', 'authority'] as $key) {
        if (!isset($record[$key]) || !hash_equals((string)$record[$key], (string)($key === 'authority' ? $authority : $p[$key]))) {
            return false;
        }
    }
    return true;
}

function vira_latest_authority(array $p): string
{
    $latest = vira_store_dir() . '/l_' . $p['s'] . '.txt';
    $authority = is_file($latest) ? trim((string)file_get_contents($latest)) : '';
    if (!preg_match('/^[A-Za-z0-9]{10,60}$/', $authority) || !vira_known_authority($p, $authority)) {
        return '';
    }
    return $authority;
}
