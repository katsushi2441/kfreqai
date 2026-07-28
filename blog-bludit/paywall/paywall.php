<?php
/**
 * ペイウォールAPI (POST JSON)。
 *   action=record_paypal {order_id, email, page}  PayPal決済完了の記録→解錠Cookie
 *   action=verify_urlai  {wallet, page}           URLAI送金のオンチェーン検証→記録→解錠Cookie
 *   action=restore       {identifier, page, order_id?} 購入済みの人の再解錠
 *       - メール識別: PayPalの注文ID(領収メール記載)も一致したときだけ解錠(他人のメールでの解錠を防ぐ)
 *       - ウォレット識別: 購入記録があればオンチェーン再検証して解錠
 */
require __DIR__ . '/lib.php';

header('Content-Type: application/json; charset=utf-8');
$in = json_decode((string)file_get_contents('php://input'), true);
if (!is_array($in)) { http_response_code(400); echo json_encode(array('ok' => false, 'error' => 'bad request')); exit; }

$action = isset($in['action']) ? $in['action'] : '';
$page = isset($in['page']) ? preg_replace('/[^a-z0-9\-]/', '', strtolower($in['page'])) : '';
if ($page === '') { http_response_code(422); echo json_encode(array('ok' => false, 'error' => 'page required')); exit; }

if ($action === 'record_paypal') {
    $order_id = isset($in['order_id']) ? trim($in['order_id']) : '';
    $email = isset($in['email']) ? pw_norm_identifier($in['email']) : '';
    if ($order_id === '' || !preg_match('/^[A-Z0-9]{8,32}$/i', $order_id)) {
        http_response_code(422); echo json_encode(array('ok' => false, 'error' => 'order_id required')); exit;
    }
    if ($email === '' || !filter_var($email, FILTER_VALIDATE_EMAIL)) {
        http_response_code(422); echo json_encode(array('ok' => false, 'error' => 'email required')); exit;
    }
    // サーバー側でPayPal APIに注文を照合(COMPLETED・200 JPY)。ブラウザの自己申告は信用しない。
    list($vok, $vemail, $vmsg) = pw_paypal_verify_order($order_id);
    if ($vok === false) {
        echo json_encode(array('ok' => false, 'error' => 'PayPal決済を確認できませんでした: ' . $vmsg)); exit;
    }
    if ($vok === true && $vemail !== '') { $email = $vemail; }  // メールはPayPal側の値を正とする
    pw_add_purchase('paypal', $email, $page, $order_id);
    pw_issue_cookie($email);
    echo json_encode(array('ok' => true, 'unlocked' => true));
    exit;
}

if ($action === 'verify_urlai') {
    $wallet = isset($in['wallet']) ? strtolower(trim($in['wallet'])) : '';
    list($ok, $msg) = pw_verify_urlai($wallet);
    if (!$ok) { echo json_encode(array('ok' => false, 'error' => $msg)); exit; }
    pw_add_purchase('urlai', $wallet, $page, 'onchain');
    pw_issue_cookie($wallet);
    echo json_encode(array('ok' => true, 'unlocked' => true, 'detail' => $msg));
    exit;
}

if ($action === 'restore') {
    $identifier = isset($in['identifier']) ? pw_norm_identifier($in['identifier']) : '';
    if ($identifier === '') { http_response_code(422); echo json_encode(array('ok' => false, 'error' => 'identifier required')); exit; }
    if (!pw_has_purchase($identifier, $page)) {
        echo json_encode(array('ok' => false, 'error' => 'この記事の購入記録が見つかりませんでした')); exit;
    }
    if (preg_match('/^0x[a-f0-9]{40}$/', $identifier)) {
        // ウォレット: オンチェーンに実送金の跡があることを再確認して解錠
        list($ok, $msg) = pw_verify_urlai($identifier);
        if (!$ok) { echo json_encode(array('ok' => false, 'error' => $msg)); exit; }
    } else {
        // メール: 領収メールに記載のPayPal注文IDの一致を要求(他人のメールでの解錠防止)
        $order_id = isset($in['order_id']) ? trim($in['order_id']) : '';
        $found = false;
        foreach (pw_load()['purchases'] as $p) {
            if ($p['identifier'] === $identifier && $p['page'] === $page
                && strcasecmp($p['ref'], $order_id) === 0) { $found = true; break; }
        }
        if (!$found) { echo json_encode(array('ok' => false, 'error' => 'メールアドレスとPayPal注文IDの組み合わせが一致しません')); exit; }
    }
    pw_issue_cookie($identifier);
    echo json_encode(array('ok' => true, 'unlocked' => true));
    exit;
}

http_response_code(422);
echo json_encode(array('ok' => false, 'error' => 'unknown action'));
