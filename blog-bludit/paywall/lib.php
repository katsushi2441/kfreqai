<?php
/**
 * Kurageブログ 有料記事(ペイウォール)の共通ライブラリ。
 *
 * 記事本文中の <!--paywall--> より後ろを「有料部分」とし、購入者だけに表示する。
 * 解錠手段は2つ(2026-07-28 ユーザー確定仕様):
 *   1) PayPal 200円 (Smart Buttons, info@exbridge.jp 受け取り)
 *   2) URLAIトークン 20,000 (Base上のERC20を受け取りウォレットへ送金→オンチェーン検証)
 * 購入者の識別は匿名でよい: メールアドレス(PayPal決済のメール) or ウォレットアドレス。
 * 記録はJSONファイル(data/purchases.json, flock保護)。閲覧はHMAC署名Cookieで維持する。
 */

define('PW_DIR', __DIR__);
define('PW_DATA_DIR', PW_DIR . '/data');
define('PW_STORE', PW_DATA_DIR . '/purchases.json');
define('PW_SECRET_FILE', PW_DATA_DIR . '/secret.txt');
define('PW_COOKIE', 'KURAGEPAY');
define('PW_PRICE_JPY', 200);
define('PW_PRICE_URLAI', 20000);
// PayPal Smart Buttons 用 Client ID(公開値)。受け取りは info@exbridge.jp のアカウント。
define('PW_PAYPAL_CLIENT_ID', 'AbbwjyEYdGXqSqptChYFw7vxdOzBSZXiNslHASN1bHfxJZnV_borxUJdMzR1gs8njHQxqn69APqn5-MG');
// URLAI (Base mainnet)
define('PW_URLAI_CONTRACT', '0xdaecdda6ad112f0e1e4097fb735dd01d9c33cba3');
define('PW_URLAI_RECEIVER', '0x444fadbd6e1fed0cfbf7613b6c9f91b9021eecbd');
define('PW_BASE_RPC', 'https://mainnet.base.org');

function pw_secret() {
    if (!is_dir(PW_DATA_DIR)) { @mkdir(PW_DATA_DIR, 0705, true); }
    if (!file_exists(PW_SECRET_FILE)) {
        @file_put_contents(PW_SECRET_FILE, bin2hex(random_bytes(32)), LOCK_EX);
        @chmod(PW_SECRET_FILE, 0600);
    }
    return trim((string)@file_get_contents(PW_SECRET_FILE));
}

function pw_load() {
    if (!file_exists(PW_STORE)) { return array('purchases' => array()); }
    $j = json_decode((string)@file_get_contents(PW_STORE), true);
    return is_array($j) ? $j : array('purchases' => array());
}

function pw_save($data) {
    if (!is_dir(PW_DATA_DIR)) { @mkdir(PW_DATA_DIR, 0705, true); }
    $fp = fopen(PW_STORE, 'c+');
    if (!$fp) { return false; }
    flock($fp, LOCK_EX);
    ftruncate($fp, 0);
    fwrite($fp, json_encode($data, JSON_UNESCAPED_UNICODE));
    fflush($fp);
    flock($fp, LOCK_UN);
    fclose($fp);
    return true;
}

function pw_norm_identifier($s) {
    $s = strtolower(trim((string)$s));
    return $s;
}

/** 購入を記録する(冪等: 同一identifier+pageは1件)。 */
function pw_add_purchase($method, $identifier, $page_key, $ref) {
    $identifier = pw_norm_identifier($identifier);
    if ($identifier === '' || $page_key === '') { return false; }
    $d = pw_load();
    foreach ($d['purchases'] as $p) {
        if ($p['identifier'] === $identifier && $p['page'] === $page_key) { return true; }
    }
    $d['purchases'][] = array(
        'method' => $method, 'identifier' => $identifier, 'page' => $page_key,
        'ref' => (string)$ref, 'ts' => time(),
    );
    return pw_save($d);
}

function pw_has_purchase($identifier, $page_key) {
    $identifier = pw_norm_identifier($identifier);
    foreach (pw_load()['purchases'] as $p) {
        if ($p['identifier'] === $identifier && $p['page'] === $page_key) { return true; }
    }
    return false;
}

/** 閲覧Cookie: identifierをHMAC署名して持たせる(サーバ側でDB照合するのでCookieは目印)。 */
function pw_issue_cookie($identifier) {
    $identifier = pw_norm_identifier($identifier);
    $sig = hash_hmac('sha256', $identifier, pw_secret());
    setcookie(PW_COOKIE, $identifier . '|' . $sig, time() + 60 * 60 * 24 * 365, '/', '', true, true);
}

function pw_cookie_identifier() {
    if (empty($_COOKIE[PW_COOKIE])) { return ''; }
    $parts = explode('|', $_COOKIE[PW_COOKIE], 2);
    if (count($parts) !== 2) { return ''; }
    list($identifier, $sig) = $parts;
    if (!hash_equals(hash_hmac('sha256', $identifier, pw_secret()), $sig)) { return ''; }
    return $identifier;
}

function pw_is_unlocked($page_key) {
    $identifier = pw_cookie_identifier();
    return $identifier !== '' && pw_has_purchase($identifier, $page_key);
}

// ---------------------------------------------------------------------------
// PayPal サーバー側検証 (Secretは data/paypal_secret.txt にサーバー直置き・非公開)
// ---------------------------------------------------------------------------
define('PW_PAYPAL_API', 'https://api-m.paypal.com');
define('PW_PAYPAL_SECRET_FILE', PW_DATA_DIR . '/paypal_secret.txt');

function pw_paypal_secret() {
    return file_exists(PW_PAYPAL_SECRET_FILE)
        ? trim((string)@file_get_contents(PW_PAYPAL_SECRET_FILE)) : '';
}

function pw_http_json($url, $headers, $post_body = null) {
    $ch = curl_init($url);
    $opts = array(CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 25,
                  CURLOPT_HTTPHEADER => $headers);
    if ($post_body !== null) { $opts[CURLOPT_POST] = true; $opts[CURLOPT_POSTFIELDS] = $post_body; }
    curl_setopt_array($ch, $opts);
    $res = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return array($code, json_decode((string)$res, true));
}

/** 注文IDをPayPal APIで照合: COMPLETED かつ 200 JPY なら (true, payerメール)。 */
function pw_paypal_verify_order($order_id) {
    $secret = pw_paypal_secret();
    if ($secret === '') { return array(null, '', 'secret未設定'); }  // null=検証不能(呼び出し側で扱い判断)
    list($code, $tok) = pw_http_json(PW_PAYPAL_API . '/v1/oauth2/token',
        array('Authorization: Basic ' . base64_encode(PW_PAYPAL_CLIENT_ID . ':' . $secret),
              'Content-Type: application/x-www-form-urlencoded'),
        'grant_type=client_credentials');
    if ($code !== 200 || empty($tok['access_token'])) { return array(false, '', 'PayPal認証に失敗しました'); }
    list($code, $order) = pw_http_json(PW_PAYPAL_API . '/v2/checkout/orders/' . rawurlencode($order_id),
        array('Authorization: Bearer ' . $tok['access_token'], 'Content-Type: application/json'));
    if ($code !== 200 || !is_array($order)) { return array(false, '', '注文が見つかりません'); }
    if (($order['status'] ?? '') !== 'COMPLETED') { return array(false, '', '決済が完了していません(status=' . ($order['status'] ?? '?') . ')'); }
    $pu = $order['purchase_units'][0] ?? array();
    $amt = $pu['amount'] ?? ($pu['payments']['captures'][0]['amount'] ?? array());
    if (($amt['currency_code'] ?? '') !== 'JPY' || (float)($amt['value'] ?? 0) < PW_PRICE_JPY) {
        return array(false, '', '決済金額が一致しません');
    }
    $email = strtolower(trim($order['payer']['email_address'] ?? ''));
    return array(true, $email, 'ok');
}

// ---------------------------------------------------------------------------
// URLAI オンチェーン検証 (Base mainnet, ERC20 Transfer(from→受け取り) >= 20000)
// ---------------------------------------------------------------------------
function pw_rpc($method, $params) {
    $body = json_encode(array('jsonrpc' => '2.0', 'id' => 1, 'method' => $method, 'params' => $params));
    $ch = curl_init(PW_BASE_RPC);
    curl_setopt_array($ch, array(
        CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body,
        CURLOPT_HTTPHEADER => array('Content-Type: application/json'),
        CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 20,
    ));
    $res = curl_exec($ch);
    curl_close($ch);
    $j = json_decode((string)$res, true);
    return isset($j['result']) ? $j['result'] : null;
}

function pw_topic_addr($addr) {
    return '0x' . str_pad(substr(strtolower($addr), 2), 64, '0', STR_PAD_LEFT);
}

/** hex文字列(wei) を URLAI枚数(float, 18 decimals) へ。閾値判定用途なのでfloat精度で足りる。 */
function pw_hex_to_tokens($hex) {
    $hex = ltrim(str_replace('0x', '', $hex), '0');
    if ($hex === '') { return 0.0; }
    if (function_exists('bcadd')) {
        $dec = '0';
        foreach (str_split($hex) as $c) {
            $dec = bcadd(bcmul($dec, '16'), (string)hexdec($c));
        }
        return (float)bcdiv($dec, bcpow('10', '18'), 6);
    }
    $val = 0.0;
    foreach (str_split($hex) as $c) { $val = $val * 16 + hexdec($c); }
    return $val / 1e18;
}

/** wallet から受け取りウォレットへの URLAI Transfer 合計(直近~5日) が20000以上か。 */
function pw_verify_urlai($wallet) {
    $wallet = strtolower(trim($wallet));
    if (!preg_match('/^0x[a-f0-9]{40}$/', $wallet)) { return array(false, 'ウォレットアドレスの形式が不正です'); }
    $latest_hex = pw_rpc('eth_blockNumber', array());
    if (!$latest_hex) { return array(false, 'チェーンに接続できませんでした。少し待って再試行してください'); }
    $latest = hexdec($latest_hex);
    $topic0 = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';
    $total = 0.0;
    // Base(2秒/block)で約5日分を5万blockずつ照会
    for ($i = 0; $i < 4; $i++) {
        $to = $latest - $i * 50000;
        $from = max(0, $to - 49999);
        $logs = pw_rpc('eth_getLogs', array(array(
            'address' => PW_URLAI_CONTRACT,
            'topics' => array($topic0, pw_topic_addr($wallet), pw_topic_addr(PW_URLAI_RECEIVER)),
            'fromBlock' => '0x' . dechex($from), 'toBlock' => '0x' . dechex($to),
        )));
        if (is_array($logs)) {
            foreach ($logs as $lg) { $total += pw_hex_to_tokens($lg['data']); }
        }
        if ($total >= PW_PRICE_URLAI) { break; }
    }
    if ($total >= PW_PRICE_URLAI) { return array(true, sprintf('%s URLAI の受領を確認しました', number_format($total))); }
    return array(false, sprintf('受領を確認できませんでした(確認できた額: %s URLAI)。送金後、数十秒待ってから再試行してください', number_format($total)));
}
