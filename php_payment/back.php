<?php
// مرحله ۵ و ۷: بازگشت از زرین‌پال (یا دکمه‌ی «بررسی پرداخت» با chk=1) → verify.json → کد امضاشده.
declare(strict_types=1);
require __DIR__ . '/common.php';

$p = vira_read_link();
$cfg = vira_config();
$qs = (string)($_SERVER['QUERY_STRING'] ?? '');

$authorityMatches = [];
$statusMatches = [];
preg_match_all('/(?:^|&)Authority=([A-Za-z0-9]{10,60})(?=&|$)/', $qs, $authorityMatches);
preg_match_all('/(?:^|&)Status=([A-Za-z]{2,10})(?=&|$)/', $qs, $statusMatches);
if (count($authorityMatches[1] ?? []) > 1 || count($statusMatches[1] ?? []) > 1) {
    vira_fail(400, 'اطلاعات بازگشت از درگاه تکراری یا نامعتبر است.');
}
$authority = $authorityMatches[1][0] ?? '';
$status = $statusMatches[1][0] ?? '';
$isCheck = preg_match('/(?:^|&)chk=1(?=&|$)/', $qs) === 1;

if ($authority === '') {
    if (!$isCheck) {
        vira_fail(400, 'اطلاعات بازگشت از درگاه ناقص است.');
    }
    $authority = vira_latest_authority($p);
    if ($authority === '') {
        vira_page(200, 'پرداختی پیدا نشد', '<h3>هنوز پرداختی ثبت نشده است.</h3><p>اگر پرداخت کرده‌ای چند دقیقه بعد دوباره «بررسی پرداخت» را بزن.</p>');
    }
} elseif ($status !== 'OK') {
    vira_page(200, 'پرداخت ناموفق', '<h3>پرداخت انجام نشد یا لغو شد.</h3><p>هزینه‌ای از حساب تو کسر نشده است؛ به برنامه برگرد.</p>');
}

// از تأیید Authority مربوط به فاکتور/کاربر دیگری جلوگیری می‌شود.
if (!vira_known_authority($p, $authority)) {
    vira_fail(403, 'این تراکنش برای این فاکتور ثبت نشده است.');
}

$res = vira_zp_call(vira_zp_base($p) . '/pg/v4/payment/verify.json', [
    'merchant_id' => $p['m'],
    'amount' => (int)$p['t'] * 10,
    'authority' => $authority,
]);

$code = $res['data']['code'] ?? null;
$ref = $res['data']['ref_id'] ?? null;
if (($code !== 100 && $code !== 101) || !is_numeric($ref)) {
    vira_page(200, 'تأیید نشد', '<h3>پرداخت هنوز تأیید نشد.</h3><p>اگر پول کم شده، چند دقیقه بعد «بررسی پرداخت» را بزن.</p>');
}

$ref = (string)(int)$ref;
$sig2 = vira_return_sig($p['d'], $ref, $p['t']);
$token = 'zpd_' . $p['d'] . '_' . $ref . '_' . $sig2;

$tpl = (string)($cfg['RETURN_URL'] ?? 'vira://payment?code={code}');
$back = str_replace('{code}', rawurlencode($token), $tpl);
vira_page(200, 'پرداخت موفق',
    '<h3>✅ پرداخت با موفقیت تأیید شد</h3><p>برای شارژ حساب، به برنامه برگرد.</p>'
    . '<a class="b" href="' . vira_h($back) . '">بازگشت به برنامه VIRA</a>'
    . '<p style="font-size:13px;color:#aaa">اگر برنامه باز نشد، این کد را در بخش «ثبت پرداخت» برنامه بچسبان:</p>'
    . '<code>' . vira_h($token) . '</code>');
