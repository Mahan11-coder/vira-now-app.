<?php
// مرحله ۳: لینک امضاشده → request.json → انتقال به StartPay (فقط meta refresh + لینک ساده).
declare(strict_types=1);
require __DIR__ . '/common.php';

$p = vira_read_link();
$cfg = vira_config();
$callbackBase = rtrim((string)($cfg['BASE_URL'] ?? ''), '/');
if (strpos($callbackBase, 'https://') !== 0 && empty($cfg['ALLOW_HTTP'])) {
    vira_fail(500, 'BASE_URL باید با https:// شروع شود.');
}

$qs = 'd=' . $p['d'] . '&u=' . $p['u'] . '&t=' . $p['t'] . '&m=' . $p['m']
    . '&b=' . $p['b'] . '&sb=' . $p['sb'] . '&s=' . $p['s'];

$res = vira_zp_call(vira_zp_base($p) . '/pg/v4/payment/request.json', [
    'merchant_id' => $p['m'],
    'amount' => (int)$p['t'] * 10,                 // ریال = تومان × ۱۰
    'callback_url' => $callbackBase . '/back.php?' . $qs,
    'description' => 'شارژ حساب VIRA #' . $p['d'],
]);

$code = $res['data']['code'] ?? null;
$authority = $res['data']['authority'] ?? '';
if ($code !== 100 || !is_string($authority) || !preg_match('/^[A-Za-z0-9]{10,60}$/', $authority)) {
    vira_fail(502, 'ساخت تراکنش در زرین‌پال انجام نشد. کمی بعد دوباره تلاش کن.');
}

vira_save_authority($p, $authority);

$startBase = !empty($cfg['ZP_BASE']) ? rtrim((string)$cfg['ZP_BASE'], '/')
    : ($p['sb'] === '1' ? 'https://sandbox.zarinpal.com' : 'https://payment.zarinpal.com');
$url = $startBase . '/pg/StartPay/' . $authority;

vira_page(200, 'انتقال به درگاه',
    '<h3>در حال انتقال به درگاه پرداخت…</h3><p>اگر منتقل نشدی، دکمه‌ی زیر را بزن.</p>'
    . '<a class="b" href="' . vira_h($url) . '">ادامه پرداخت</a>',
    '<meta http-equiv="refresh" content="1;url=' . vira_h($url) . '">');
