<?php
require_once __DIR__ . '/config.php';
require_once __DIR__ . '/auth_common.php';

$auth = url2ai_auth_bootstrap();
$ADMIN_USERNAME = url2ai_auth_admin_user(); // xb_bittensor

// 招待制(allowlist): このファイルに載っているXユーザー名か管理者だけ利用できる。
// Xでログインできても、招待されていない人はダッシュボード/APIを使えない。
$KFREQAIHL_ALLOWLIST = @include __DIR__ . '/kfreqaihl_allowlist.php';
if (!is_array($KFREQAIHL_ALLOWLIST)) { $KFREQAIHL_ALLOWLIST = array(); }
$KFREQAIHL_ALLOWLIST = array_map('strtolower', $KFREQAIHL_ALLOWLIST);
function kfreqaihl_is_allowed($auth, $allowlist, $admin_user) {
    if (empty($auth['logged_in'])) { return false; }
    $u = strtolower($auth['session_user']);
    return ($u === strtolower($admin_user)) || in_array($u, $allowlist, true);
}
$is_allowed = kfreqaihl_is_allowed($auth, $KFREQAIHL_ALLOWLIST, $ADMIN_USERNAME);

// --- 同一オリジン中継: hl_api.py はHTTPSページから直接叩けない(mixed content)ので
// PHPがcurlで中継する。X-Hl-Tokenはここでだけ付与し、ブラウザには渡さない。
// 公開参照(read-only): xb_bittensor(公開アカウント)の取引情報は未ログインでも見れる。
// 書き込み・管理系(委任/設定/戦略会議/発注/ペーパー開始)はログイン+招待必須。
$KFREQAIHL_READ_ACTIONS = array(
    'dashboard', 'paper_fx_dashboard', 'paper_spot_dashboard',
    'decide', 'strategy_info', 'schema', 'fx_info', 'fx_judgment');
// 管理操作ができるのは「ログイン済み かつ 招待リストに載っている」人だけ
$can_manage = (!empty($auth['logged_in']) && $is_allowed);

if (isset($_GET['api'])) {
    $action = $_GET['api'];
    $is_read = in_array($action, $KFREQAIHL_READ_ACTIONS, true);
    if (!$is_read) {
        // 管理系は従来どおりログイン+招待を要求
        if (empty($auth['logged_in'])) { http_response_code(401); echo '{"error":"login required"}'; exit; }
        if (!$is_allowed) { http_response_code(403); echo '{"error":"invite only: このアカウントは招待されていません"}'; exit; }
    }
    // 参照するデータの持ち主: 管理可能ユーザーは自分、それ以外は公開アカウント(admin)
    $username = $can_manage ? $auth['session_user'] : $ADMIN_USERNAME;
    $is_admin = ($username === $ADMIN_USERNAME);
    $base = rtrim(KFREQAI_HL_API_BASE, '/');
    header('Content-Type: application/json; charset=utf-8');
    $headers = array('Content-Type: application/json', 'X-Hl-Token: ' . KFREQAI_HL_TOKEN);
    $method = 'GET';
    $url = '';
    $body = null;

    if ($action === 'dashboard') {
        $url = $base . '/api/dashboard?username=' . rawurlencode($username);
    } elseif ($action === 'register' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $url = $base . '/api/tenant/register'; $method = 'POST';
        $body = json_encode(array('username' => $username));
    } elseif ($action === 'main_wallet' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $in = json_decode(file_get_contents('php://input'), true) ?: array();
        $addr = preg_replace('/[^0-9a-fA-Fx]/', '', isset($in['address']) ? $in['address'] : '');
        $url = $base . '/api/tenant/main-wallet'; $method = 'POST';
        $body = json_encode(array('username' => $username, 'address' => $addr));
    } elseif ($action === 'confirm_approval' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $url = $base . '/api/tenant/confirm-approval'; $method = 'POST';
        $body = json_encode(array('username' => $username));
    } elseif ($action === 'decide') {
        $coin = preg_replace('/[^A-Za-z0-9]/', '', isset($_GET['coin']) ? $_GET['coin'] : 'ETH');
        $url = $base . '/api/decide?username=' . rawurlencode($username) . '&coin=' . rawurlencode($coin ?: 'ETH');
    } elseif ($action === 'execute' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $in = json_decode(file_get_contents('php://input'), true) ?: array();
        $coin = preg_replace('/[^A-Za-z0-9]/', '', isset($in['coin']) ? $in['coin'] : 'ETH');
        $url = $base . '/api/execute'; $method = 'POST';
        $body = json_encode(array('username' => $username, 'coin' => $coin ?: 'ETH'));
    } elseif ($action === 'schema') {
        $url = $base . '/api/strategy-schema';
    } elseif ($action === 'strategy_info') {
        $url = $base . '/api/strategy-info?username=' . rawurlencode($username);
    } elseif ($action === 'fx_info') {
        $url = $base . '/api/fx-info';
    } elseif ($action === 'paper_fx_dashboard') {
        $url = $base . '/api/paper-fx/dashboard?username=' . rawurlencode($username);
    } elseif ($action === 'paper_fx_start' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $in = json_decode(file_get_contents('php://input'), true) ?: array();
        $payer = preg_replace('/[^0-9a-fA-Fx]/', '', isset($in['payer_wallet']) ? $in['payer_wallet'] : '');
        $url = $base . '/api/paper-fx/start'; $method = 'POST';
        $body = json_encode(array('username' => $username, 'payer_wallet' => $payer));
    } elseif ($action === 'paper_fx_reset' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $url = $base . '/api/paper-fx/reset'; $method = 'POST';
        $body = json_encode(array('username' => $username));
    } elseif ($action === 'paper_spot_dashboard') {
        $url = $base . '/api/paper-spot/dashboard?username=' . rawurlencode($username);
    } elseif ($action === 'paper_spot_start' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $in = json_decode(file_get_contents('php://input'), true) ?: array();
        $payer = preg_replace('/[^0-9a-fA-Fx]/', '', isset($in['payer_wallet']) ? $in['payer_wallet'] : '');
        $url = $base . '/api/paper-spot/start'; $method = 'POST';
        $body = json_encode(array('username' => $username, 'payer_wallet' => $payer));
    } elseif ($action === 'fx_judgment') {
        $url = $base . '/api/fx-judgment?username=' . rawurlencode($username);
        if (!$is_admin) {
            $headers[] = 'X-HL-Payment-Ref: internal-test-' . substr(md5($username . date('Ymd')), 0, 8);
        }
    } elseif ($action === 'backtest' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $in = json_decode(file_get_contents('php://input'), true) ?: array();
        $market = preg_replace('/[^a-z]/', '', isset($in['market']) ? strtolower($in['market']) : 'crypto');
        $days = isset($in['days']) ? (int)$in['days'] : 60;
        $url = $base . '/api/backtest'; $method = 'POST';
        $body = json_encode(array('username' => $username, 'market' => ($market ?: 'crypto'), 'days' => $days));
    } elseif ($action === 'apply_preset' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $in = json_decode(file_get_contents('php://input'), true) ?: array();
        $preset = preg_replace('/[^a-z]/', '', isset($in['preset']) ? strtolower($in['preset']) : '');
        $url = $base . '/api/apply-preset'; $method = 'POST';
        $body = json_encode(array('username' => $username, 'preset' => $preset));
    } elseif ($action === 'params') {
        $url = $base . '/api/strategy-params?username=' . rawurlencode($username);
    } elseif ($action === 'params_save' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $in = json_decode(file_get_contents('php://input'), true) ?: array();
        $url = $base . '/api/strategy-params'; $method = 'POST';
        $body = json_encode(array('username' => $username, 'updates' => isset($in['updates']) ? $in['updates'] : array()));
    } elseif ($action === 'chat' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $in = json_decode(file_get_contents('php://input'), true) ?: array();
        $msg = isset($in['message']) ? mb_substr((string)$in['message'], 0, 1000) : '';
        $url = $base . '/api/chat'; $method = 'POST';
        $body = json_encode(array('username' => $username, 'message' => $msg));
        if (!$is_admin) {
            // Phase1: x402実決済は未配線(アンバサダー内部テスト)。ヘッダーはスタブ判定用のみ。
            $headers[] = 'X-Hl-Payment-Ref: internal-test-' . substr(md5($username . date('Ymd')), 0, 8);
        }
    } else {
        http_response_code(404); echo '{"error":"unknown api"}'; exit;
    }

    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    curl_setopt($ch, CURLOPT_TIMEOUT, in_array($action, array('chat', 'fx_judgment', 'backtest'), true) ? 120 : 20);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
    if ($body !== null) { curl_setopt($ch, CURLOPT_POSTFIELDS, $body); }
    $res = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($res === false) { http_response_code(502); echo '{"error":"kfreqaihlバックエンドに繋がりませんでした"}'; exit; }
    http_response_code($code ?: 200);
    echo $res;
    exit;
}

$is_admin = !empty($auth['is_admin']);
$view = isset($_GET['view']) ? $_GET['view'] : 'summary';
if (!in_array($view, array('summary', 'fx', 'chat', 'settings'), true)) { $view = 'summary'; }
// 管理系ビュー(戦略会議/設定)は管理可能ユーザーのみ。参照者はsummaryへ戻す。
if (in_array($view, array('chat', 'settings'), true) && !$can_manage) { $view = 'summary'; }
?>
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kurage FreqAI Trade for Hyperliquid — AI自動取引（Crypto / FX）</title>
<meta name="description" content="ウォレット1つで始める、Hyperliquid上のAI自動取引。kcbrain/kfxbrainのAI判断とkfreqai共通戦略で、Crypto・FX・商品・指数を非カストディ・サーバー不要で。ペーパートレードで先行体験。">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://kurage.exbridge.jp/kfreqaihl.php">
<meta property="og:title" content="Kurage FreqAI Trade for Hyperliquid — AI自動取引（Crypto / FX）">
<meta property="og:description" content="ウォレット1つ・サーバー不要で始める、非カストディのAI自動取引。kcbrain/kfxbrainのAI判断で、Crypto・FX・商品・指数に対応。ペーパートレードで先行体験。">
<meta property="og:type" content="website">
<meta property="og:url" content="https://kurage.exbridge.jp/kfreqaihl.php">
<meta property="og:site_name" content="Kurage FreqAI Trade for Hyperliquid">
<meta property="og:image" content="https://kurage.exbridge.jp/images/kfreqaihl_ogp.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Kurage FreqAI Trade for Hyperliquid — AI自動取引（Crypto / FX）">
<meta name="twitter:description" content="ウォレット1つ・サーバー不要で始める非カストディのAI自動取引。Crypto・FX・商品・指数に対応、ペーパートレードで先行体験。">
<meta name="twitter:image" content="https://kurage.exbridge.jp/images/kfreqaihl_ogp.png">
<link rel="stylesheet" href="assets/kurage-avatar.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Zen+Maru+Gothic:wght@700;900&family=Noto+Sans+JP:wght@400;600;800&display=swap" rel="stylesheet">
<style>
  :root {
    --indigo: #2f6bd8; --cyan: #0b91a7; --glow: #0b91a7; --coin: #b98422;
    --bg: #f5f8fb; --card: #ffffff;
    --ink: #17324d; --muted: #64788a; --border: #dbe6ee;
    --up: #16805f; --down: #d6453d;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: "Noto Sans JP", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background:
      radial-gradient(1000px 600px at 85% -5%, rgba(11,145,167,.10), transparent 60%),
      radial-gradient(800px 700px at -5% 45%, rgba(47,107,216,.07), transparent 55%),
      linear-gradient(170deg, #ffffff 0%, #f2f8fa 45%, #eaf5f4 100%);
    background-attachment: fixed; color: var(--ink); min-height: 100vh; }
  .disp, h1, h2, h3 { font-family: "Zen Maru Gothic","Noto Sans JP",sans-serif; }
  header { padding: 22px 20px 12px; max-width: 1080px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
  header .brand { display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 19px; margin: 0; line-height: 1.35; font-weight: 900; }
  header h1 span { color: var(--glow); }
  header h1 .sub { display: block; font-size: 11px; color: var(--muted); font-weight: 700; letter-spacing: .02em; }
  .brainicon { width: 22px; height: 22px; border-radius: 6px; vertical-align: middle; object-fit: cover; }
  .brain-chip { display: inline-flex; align-items: center; gap: 7px; background: var(--card); border: 1.5px solid var(--border); border-radius: 999px; padding: 4px 12px 4px 5px; font-size: 12px; color: var(--muted); }
  .brain-chip img { width: 24px; height: 24px; border-radius: 50%; object-fit: cover; }
  .brain-chip b { color: var(--ink); }
  .badge { display: inline-block; padding: 3px 11px; border-radius: 999px; font-size: 11.5px; font-weight: 800; margin-left: 8px; vertical-align: middle; }
  .badge.dry { background: rgba(185,132,34,.10); color: var(--coin); border: 1px solid rgba(185,132,34,.35); }
  .badge.live { background: rgba(214,69,61,.10); color: var(--down); border: 1px solid rgba(214,69,61,.35); }
  .badge.gemma { background: rgba(11,145,167,.10); color: var(--cyan); border: 1px solid rgba(11,145,167,.35); }
  .badge.deepseek { background: rgba(94,74,227,.10); color: #5e4ae3; border: 1px solid rgba(94,74,227,.35); }
  .userbar { font-size: 13px; color: var(--muted); }
  .userbar a { color: var(--glow); text-decoration: none; margin-left: 10px; font-weight: 700; }
  main { max-width: 1080px; margin: 0 auto; padding: 0 20px 60px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 20px; }
  .card { background: var(--card); border: 1.5px solid var(--border); border-radius: 16px; padding: 18px 20px; backdrop-filter: blur(6px); }
  .card .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; font-weight: 700; }
  .card .value { font-size: 26px; font-weight: 900; font-family: "Zen Maru Gothic","Noto Sans JP",sans-serif; }
  .card .sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .up { color: var(--up); } .down { color: var(--down); }
  section { margin-bottom: 30px; }
  section h2 { font-size: 14px; color: var(--glow); text-transform: uppercase; letter-spacing: .08em; margin: 0 0 10px; font-weight: 900; }
  .card h2, .strat-card h2 { text-transform: none; letter-spacing: 0; font-size: 16px; color: var(--ink); }
  table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 14px; overflow: hidden; border: 1px solid var(--border); }
  th, td { text-align: left; padding: 10px 14px; font-size: 13px; border-bottom: 1px solid var(--border); }
  th { color: var(--cyan); font-weight: 800; background: rgba(11,145,167,.06); font-size: 12px; letter-spacing: .04em; }
  tr:last-child td { border-bottom: none; }
  td a { color: var(--indigo); }
  .empty { color: var(--muted); font-size: 13px; padding: 16px; background: var(--card); border: 1.5px dashed #c3d6df; border-radius: 14px; }
  .gate { max-width: 480px; margin: 80px auto; text-align: center; }
  .btn { display: inline-block; padding: 11px 24px; border-radius: 999px; background: linear-gradient(90deg, #0b91a7, #2f6bd8); color: #fff; text-decoration: none; font-weight: 900; border: none; cursor: pointer; font-size: 14px; font-family: "Zen Maru Gothic","Noto Sans JP",sans-serif; box-shadow: 0 8px 24px rgba(31,150,190,.3); transition: transform .15s; }
  .btn:hover { transform: translateY(-1px); }
  .btn.ghost { background: #64788a; border: none; box-shadow: none; }
  .btn:disabled { opacity: .5; cursor: default; transform: none; }
  .tabs { display: flex; gap: 8px; margin: 4px 0 22px; overflow-x: auto; -webkit-overflow-scrolling: touch; flex-wrap: nowrap; padding: 4px 2px 8px; scrollbar-width: none; }
  .tabs::-webkit-scrollbar { display: none; }
  .tabs a { padding: 9px 17px; border-radius: 999px; font-size: 13px; text-decoration: none; color: var(--ink); border: 1px solid var(--border); background: var(--card); white-space: nowrap; flex: 0 0 auto; font-weight: 800; font-family: "Zen Maru Gothic","Noto Sans JP",sans-serif; transition: border-color .15s, transform .15s; }
  .tabs a:hover { border-color: var(--glow); transform: translateY(-1px); }
  .tabs a.active { background: linear-gradient(90deg, #0b91a7, #2f6bd8); color: #fff; border-color: transparent; box-shadow: 0 8px 20px rgba(11,145,167,.3); }
  .notice { background: rgba(185,132,34,.08); border: 1px solid rgba(185,132,34,.3); color: var(--coin); padding: 12px 16px; border-radius: 12px; font-size: 13px; margin-bottom: 18px; }
  .notice a { color: var(--glow); }
  .error { background: rgba(214,69,61,.08); border: 1px solid rgba(214,69,61,.3); color: var(--down); padding: 12px 16px; border-radius: 12px; font-size: 13px; margin-bottom: 18px; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; background: #f2f7fa; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; color: #14506b; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  input[type=text], input[type=number], select { background: #fff; border: 1px solid var(--border); border-radius: 9px; color: var(--ink); }
  input[type=text] { flex: 1; min-width: 200px; padding: 10px 12px; font-size: 13px; }
  input:focus, select:focus { outline: none; border-color: var(--glow); }
  .tscroll { overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; border: 1px solid var(--border); border-radius: 14px; background: var(--card); box-shadow: 0 10px 26px rgba(25,72,78,.06); }
  .tscroll table { border: 0; border-radius: 0; }
  .chatlog { max-height: 380px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; padding: 4px; }
  .msg { padding: 10px 14px; border-radius: 14px; font-size: 13.5px; line-height: 1.7; max-width: 85%; }
  .msg.user { align-self: flex-end; background: linear-gradient(90deg, #2f6bd8, #19bfd3); color: #fff; border-bottom-right-radius: 4px; }
  .msg.kurage { align-self: flex-start; background: #eef8fa; border: 1px solid #cde8ee; color: var(--ink); border-bottom-left-radius: 4px; white-space: pre-wrap; }
  .composer { display: flex; gap: 10px; align-items: flex-end; border: 1px solid var(--border); border-radius: 16px; padding: 8px 8px 8px 14px; background: #fff; }
  .composer textarea { flex: 1; border: none; outline: none; resize: none; font-size: 15px; line-height: 1.5; font-family: inherit; max-height: 160px; background: transparent; color: var(--ink); padding: 6px 0; }
  .composer .btn { flex: 0 0 auto; padding: 9px 18px; }
  .msg.thinking { display: flex; gap: 5px; align-items: center; }
  .msg.thinking span { width: 7px; height: 7px; border-radius: 50%; background: var(--cyan); opacity: .5; animation: kblink 1.2s infinite ease-in-out; }
  .msg.thinking span:nth-child(2) { animation-delay: .2s; }
  .msg.thinking span:nth-child(3) { animation-delay: .4s; }
  @keyframes kblink { 0%, 60%, 100% { opacity: .3; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-3px); } }
  .params-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-top: 8px; }
  .param { border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; background: #fbfdfe; }
  .param label { font-size: 12px; color: var(--muted); display: block; margin-bottom: 6px; }
  .param input[type=number] { width: 100%; padding: 7px 9px; }
  footer { text-align: center; color: var(--muted); font-size: 12px; padding: 30px 20px; }
  .setup-step { font-size: 13px; line-height: 1.8; }
  .setup-step b { color: var(--glow); }
  /* 動いている戦略カード */
  .strat-card { background: linear-gradient(135deg, #eef6ff 0%, #f6fbfc 100%); border: 1px solid #cfe2ef; border-radius: 16px; padding: 18px 20px; box-shadow: 0 10px 26px rgba(25,72,78,.06); }
  .strat-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .strat-head .name { font-size: 17px; font-weight: 900; font-family: "Zen Maru Gothic","Noto Sans JP",sans-serif; }
  .preset-badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 12px; border-radius: 999px; background: linear-gradient(90deg, #0b91a7, #2f6bd8); color: #fff; font-size: 12px; font-weight: 800; }
  .preset-badge.custom { background: #e5edf2; color: var(--ink); }
  .strat-tagline { font-size: 13px; color: var(--muted); margin: 8px 0 2px; }
  .strat-how { margin: 10px 0 0; padding-left: 18px; font-size: 12.5px; color: #33546b; line-height: 1.7; }
  .strat-how li { margin-bottom: 3px; }
  .strat-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  .chip { background: #fff; border: 1px solid var(--border); border-radius: 9px; padding: 5px 10px; font-size: 12px; box-shadow: 0 3px 10px rgba(25,72,78,.05); }
  .chip b { color: var(--coin); }
  .strat-adjust { font-size: 12px; color: var(--muted); margin-top: 12px; line-height: 1.7; }
  .strat-adjust a { color: var(--glow); text-decoration: none; font-weight: 800; }
  /* プリセット選択(設定画面) */
  .preset-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 8px 0 4px; }
  .preset-opt { text-align: left; border: 2px solid var(--border); border-radius: 14px; padding: 14px; background: var(--card); cursor: pointer; transition: border-color .15s, box-shadow .15s; color: var(--ink); font-family: inherit; }
  .preset-opt:hover { border-color: var(--glow); }
  .preset-opt.active { border-color: var(--glow); box-shadow: 0 0 0 3px rgba(79,227,242,.15); }
  .preset-opt .pname { font-size: 15px; font-weight: 900; margin-bottom: 4px; font-family: "Zen Maru Gothic","Noto Sans JP",sans-serif; }
  .preset-opt .pdesc { font-size: 12px; color: var(--muted); line-height: 1.6; }
  .preset-opt .pmeta { font-size: 11px; color: var(--glow); margin-top: 8px; font-weight: 800; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="kurage-avatar-stage kurage-avatar-mini" role="img" aria-label="Kurage avatar"><img class="kurage-avatar-still" src="images/kurage_avatar_face.webp" alt=""></span>
    <h1><span>Kurage</span> FreqAI Trade <span class="badge dry" id="netbadge">…</span>
      <span class="sub">for Hyperliquid ・ AI自動取引（Crypto / FX）</span></h1>
  </div>
  <div class="userbar">
    <?php if (!empty($auth['logged_in'])): ?>
      @<?php echo htmlspecialchars($auth['session_user']); ?><?php if ($is_admin): ?> <span class="badge gemma">admin / gemma4</span><?php else: ?> <span class="badge deepseek">DeepSeek</span><?php endif; ?>
      <a href="<?php echo htmlspecialchars($auth['logout_url']); ?>">ログアウト</a>
    <?php endif; ?>
  </div>
</header>
<main>
<?php if (!$can_manage): /* 参照モード: xb_bittensorの取引情報を誰でも閲覧できる。操作は不可 */ ?>
  <div class="notice" style="margin-bottom:16px">
    👀 <b>公開ビュー</b>：<b>@<?php echo htmlspecialchars($ADMIN_USERNAME); ?></b> のAI自動取引（Hyperliquid）をリアルタイムで閲覧しています（参照のみ）。
    <?php if (empty($auth['logged_in'])): ?>
      アンバサダーの方は <a href="<?php echo htmlspecialchars($auth['login_url']); ?>" style="color:var(--indigo);font-weight:700">Xでログイン</a> すると自分の口座で運用できます。
    <?php else: ?>
      @<?php echo htmlspecialchars($auth['session_user']); ?> さんはまだ招待されていません。参加希望は運営（<a href="https://exbridge.jp/" style="color:var(--indigo)">エクスブリッジ</a>）まで。
      <a href="<?php echo htmlspecialchars($auth['logout_url']); ?>" style="color:var(--muted)">ログアウト</a>
    <?php endif; ?>
  </div>
<?php endif; ?>

  <div class="tabs">
    <a href="?view=summary" class="<?php echo $view === 'summary' ? 'active' : ''; ?>">📈 Crypto本番</a>
    <a href="?view=fx" class="<?php echo $view === 'fx' ? 'active' : ''; ?>">💱 FX・商品・指数</a>
    <?php if ($can_manage): /* 戦略会議・設定は管理可能ユーザーのみ。参照者には出さない */ ?>
    <a href="?view=chat" class="<?php echo $view === 'chat' ? 'active' : ''; ?>">💬 戦略会議</a>
    <a href="?view=settings" class="<?php echo $view === 'settings' ? 'active' : ''; ?>">⚙️ 設定</a>
    <?php endif; ?>
  </div>

  <div id="mock-notice"></div>

<?php if ($view === 'summary'): ?>
  <!-- ウォレット委任セットアップ(未承認のときだけ表示) -->
  <section id="setup-card" style="display:none">
    <div class="card">
      <h2 style="margin-top:0">Cryptoペーパー（testnet）を始める：ウォレット委任</h2>
      <div id="setup-body">読み込み中...</div>
    </div>
  </section>

  <!-- Unified Account 有効化(spot資金をperp担保に。Hyperliquid画面を触らずここで署名) -->
  <section id="unified-card" style="display:none">
    <div class="card">
      <h2 style="margin-top:0">Unified Account を有効化</h2>
      <p style="font-size:13px;color:var(--muted);margin-top:0">spotの残高をそのまま先物(perp)の担保として使えるようにします（Hyperliquidの画面を触らず、ここで1回署名するだけ）。有効化すると資金移動は不要になります。</p>
      <div class="row"><button class="btn" id="unifiedbtn">Unified Accountを有効化</button></div>
      <div id="unified-msg" style="font-size:12px;color:var(--muted);margin-top:8px"></div>
    </div>
  </section>

  <div class="grid" id="kpi-grid"></div>

  <section>
    <h2>保有中ポジション</h2>
    <div id="positions-body"><div class="empty">読み込み中…</div></div>
  </section>

  <section>
    <h2>直近の約定履歴（最新50件）</h2>
    <div id="fills-body"><div class="empty">読み込み中…</div></div>
  </section>

  <section>
    <h2>日次損益（直近7日・日本時間）</h2>
    <div id="daily-body"><div class="empty">読み込み中…</div></div>
  </section>

  <section id="strategy-section" style="display:none">
    <h2>動いている戦略</h2>
    <div class="strat-card" id="strategy-card">読み込み中…</div>
  </section>

<?php elseif ($view === 'fx'): ?>
  <div class="notice">FX・商品・指数はHyperliquidのbuilder-dex（xyz）の実価格で動きます。<b>ペーパートレード（仮想資金・実弾ゼロ）</b>で先行体験できます。<?php if (!$is_admin): ?>AI判断（kfxbrain）はx402課金のため<b>ウォレット接続が必要（取引の委任は不要）</b>。<?php endif; ?>実弾の自動売買は近日対応。<br>Cryptoのペーパー（testnet）を試したい方は <a href="?view=summary" style="color:var(--indigo);font-weight:600">本番（Crypto）タブ</a> で委任してください。</div>

  <!-- ペーパーFX(仮想売買) -->
  <section id="paperfx-section">
    <h2>ペーパーFX（仮想トレード）</h2>
    <div id="paperfx-body"><div class="empty">読み込み中…</div></div>
  </section>
  <section id="paperfx-detail" style="display:none">
    <h2>保有中ポジション</h2>
    <div id="paperfx-positions"></div>
    <h2 style="margin-top:24px">直近の約定履歴（最新50件）</h2>
    <div id="paperfx-fills"></div>
    <h2 style="margin-top:24px">日次損益（直近7日・日本時間）</h2>
    <div id="paperfx-daily"></div>
  </section>

  <section id="fx-strategy-section">
    <h2>FX戦略とAI判断エンジン</h2>
    <div class="strat-card" id="fx-strategy-card">読み込み中…</div>
  </section>

  <section class="card">
    <h2 style="margin-top:0">FXバックテスト（mainnet実データ）</h2>
    <p style="font-size:13px;color:var(--muted);margin-top:0">FX既定プロファイルで過去相場を再生します（本番と同じ共通コア）。</p>
    <div class="row">
      <label style="font-size:13px;color:var(--muted)">期間:
        <select id="fx-bt-days"><option value="30">30日</option><option value="60" selected>60日</option><option value="90">90日</option></select></label>
      <button class="btn" id="fx-bt-run">バックテスト実行</button>
    </div>
    <div id="fx-bt-result" style="margin-top:12px"></div>
  </section>

  <section class="card">
    <h2 style="margin-top:0"><img class="brainicon" src="images/kfxbrain-icon.png" alt=""> kfxbrainのAI市場判断</h2>
    <p style="font-size:13px;color:var(--muted);margin-top:0">FX/商品/指数をAIが判定します<?php if (!$is_admin): ?>（DeepSeek・x402）<?php else: ?>（gemma4）<?php endif; ?>。数十秒かかります。</p>
    <div class="row"><button class="btn" id="fx-judge-run">AIに市場を判断してもらう</button></div>
    <div id="fx-judge-result" style="margin-top:12px"></div>
  </section>

<?php elseif ($view === 'chat'): ?>
  <section class="card">
    <h2 style="margin-top:0">Kurageさんと戦略会議</h2>
    <p style="font-size:13px;color:var(--muted);margin-top:0">「積極型にして」「もっと安全に」で戦略プリセットを、「レバレッジを3倍にして」で個別の数値を調整。「BTCどう思う？」でkcbrain/kfxbrainのAI判断、「バックテストして」で過去検証ができます。<?php if (!$is_admin): ?>（AIはDeepSeek/x402）<?php else: ?>（AIはgemma4）<?php endif; ?></p>
    <div class="chatlog" id="chatlog"></div>
    <div class="composer">
      <textarea id="chatinput" rows="1" placeholder="メッセージを入力（例：枠を5つにして / ショートも有効にして / 損切りを浅く）"></textarea>
      <button class="btn" id="chatsend">送信</button>
    </div>
    <div style="font-size:11px;color:var(--muted);margin-top:6px">Enterで送信 / Shift+Enterで改行</div>
  </section>

<?php elseif ($view === 'settings'): ?>
  <section class="card">
    <h2 style="margin-top:0">戦略プリセット</h2>
    <p style="font-size:13px;color:var(--muted);margin-top:0">まずは大まかな性格を選ぶだけでOK。中身は同じ「トレンド追随（EMAクロス）戦略」で、レバや枠数・回数のバランスが変わります。選ぶと即反映されます。</p>
    <div class="preset-grid" id="preset-grid">読み込み中...</div>
    <div id="presetmsg" style="font-size:12px;color:var(--muted);margin-top:4px"></div>
  </section>
  <section class="card">
    <h2 style="margin-top:0">詳細設定（数値で微調整）</h2>
    <p style="font-size:13px;color:var(--muted);margin-top:0">プリセットをベースに、数値を直接いじれます（コード変更なし＝バイブトレーディング）。ここを触ると表示は「カスタム」になります。保存すると次のループから反映されます。</p>
    <div class="params-grid" id="params-grid">読み込み中...</div>
    <div class="row" style="margin-top:12px"><button class="btn" id="paramssave">保存</button><span id="paramsmsg" style="font-size:12px;color:var(--muted)"></span></div>
  </section>
<?php endif; ?>
</main>
<footer>kfreqaihl — Hyperliquid Agent Wallet委任方式（非カストディ）。資金は常にご自身のHyperliquid口座に残ります。戦略ロジックはkfreqaiと共通（strategy_core）。</footer>

<?php /* JSは参照モードでも出力する。管理系関数(chat送信/設定保存)はボタンが無いので無害 */ ?>
<script>
const VIEW = <?php echo json_encode($view); ?>;
const IS_ADMIN = <?php echo $is_admin ? 'true' : 'false'; ?>;
const CAN_MANAGE = <?php echo $can_manage ? 'true' : 'false'; ?>;  // 参照モード(未ログイン/非招待)ではfalse=操作UIを出さない

async function api(action, opts) {
  opts = opts || {};
  const res = await fetch('?api=' + action, {
    method: opts.method || 'GET',
    headers: {'Content-Type': 'application/json'},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { data.__error = true; data.__status = res.status; }
  return data;
}
function usd(n) { return (typeof n === 'number') ? ('$' + n.toLocaleString(undefined, {maximumFractionDigits: 2})) : '-'; }
function pct(n) { return (typeof n === 'number') ? ((n >= 0 ? '+' : '') + (n * 100).toFixed(2) + '%') : '-'; }
function jst(ms) { if (!ms) return '-'; const d = new Date(ms); return d.toLocaleString('ja-JP', {timeZone: 'Asia/Tokyo', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}); }
function setBadge(d) {
  let label = (d.is_testnet ? 'testnet' : 'mainnet') + (d.live_trading_enabled ? ' / live' : ' / dry-run');
  if (d.mock) label = 'シミュレーション(モック)';
  const b = document.getElementById('netbadge');
  b.textContent = label; b.className = 'badge ' + (d.live_trading_enabled ? 'live' : 'dry');
  if (d.mock) document.getElementById('mock-notice').innerHTML = '<div class="notice">これはシミュレーション(モック)です。実際のHyperliquid取引・残高ではありません。発注も実行されません。</div>';
}

// ---------- 本番サマリ ----------
function renderSetup(d) {
  const card = document.getElementById('setup-card');
  if (d.agent_approved) { card.style.display = 'none'; return; }
  card.style.display = '';
  let html = '<div class="setup-step">';
  html += '<div class="notice" style="margin-bottom:14px">Cryptoは現在 <b>testnet（偽USDCのペーパー）</b> で、Hyperliquid上で実際に約定させるため<b>ウォレットの委任（取引のみ・出金不可）が必要</b>です。<br>'
    + '手軽に試すだけなら、<a href="?view=fx" style="color:var(--indigo);font-weight:600">FX・商品・指数（β）のペーパー</a>が<b>委任不要（ウォレット接続のみ）</b>で始められます。</div>';
  html += '<p style="margin-top:0">このシステムがあなたの口座で<b>取引だけ</b>を行うためのAgent Walletアドレス（出金はできません）：</p>';
  html += '<div class="mono">' + d.agent_address + '</div>';
  html += '<p style="margin-top:12px"><b>①</b> あなたのメイン口座アドレス（資金を置く側）を登録：</p>';
  html += '<div class="row"><input type="text" id="mainaddr" placeholder="0x..." value="' + (d.main_wallet_address || '') + '"><button class="btn" id="savemain">登録</button></div>';
  if (d.main_wallet_address) {
    html += '<p style="margin-top:14px"><b>②</b> ウォレット（MetaMask等）を接続して、この画面で委任署名します。<br>'
      + '<span style="font-size:12px;color:var(--muted)">署名するのは「上のAgentアドレスに<b>取引だけ</b>許可する（出金は不可）」という内容です。資金は動きません。</span></p>';
    html += '<div class="row"><button class="btn" id="approvebtn">ウォレットを接続して委任署名</button>'
      + '<button class="btn ghost" id="confirmbtn">委任済みか確認</button></div>';
    html += '<div id="approve-msg" style="font-size:12px;color:var(--muted);margin-top:8px"></div>';
  }
  html += '</div>';
  document.getElementById('setup-body').innerHTML = html;
  const sm = document.getElementById('savemain');
  if (sm) sm.onclick = async () => {
    const a = document.getElementById('mainaddr').value.trim();
    if (!a.startsWith('0x') || a.length !== 42) { alert('0xで始まる42文字のアドレスを入力してください'); return; }
    await api('main_wallet', {method:'POST', body:{address:a}}); loadDashboard();
  };
  const ab = document.getElementById('approvebtn');
  if (ab) ab.onclick = () => approveAgentFlow(d);
  const cb = document.getElementById('confirmbtn');
  if (cb) cb.onclick = async () => {
    const r = await api('confirm_approval', {method:'POST'});
    setApproveMsg(r.agent_approved ? '委任を確認しました。' : (r.message || '委任が確認できませんでした。'), !r.agent_approved);
    if (r.agent_approved) loadDashboard();
  };
}

function setApproveMsg(t, isErr) {
  const el = document.getElementById('approve-msg');
  if (el) { el.textContent = t; el.style.color = isErr ? 'var(--down)' : 'var(--up)'; }
}

// Hyperliquid approveAgent をブラウザのウォレットで署名し、/exchange に送信する。
// EIP-712構造は公式SDK(sign_agent)から確認済み。署名するのは「取引専用(出金不可)」の
// 委任のみ。signatureChainIdは「任意チェーン可」仕様なので、ウォレットの現在チェーンに
// 合わせて強制切替を避ける。成功後、サーバーがオンチェーンで実在検証してから承認印を立てる。
async function approveAgentFlow(d) {
  if (!window.ethereum) { setApproveMsg('MetaMask等のウォレットが見つかりません。', true); return; }
  try {
    setApproveMsg('ウォレットに接続中…');
    const accounts = await window.ethereum.request({method: 'eth_requestAccounts'});
    const from = accounts[0];
    if (d.main_wallet_address && from.toLowerCase() !== d.main_wallet_address.toLowerCase()) {
      setApproveMsg('接続中のアドレスが登録メイン口座と違います（' + from + '）。同じ口座に切り替えてください。', true); return;
    }
    const chainIdHex = await window.ethereum.request({method: 'eth_chainId'});
    const chainId = parseInt(chainIdHex, 16);
    const nonce = Date.now();
    const action = {
      type: 'approveAgent',
      signatureChainId: chainIdHex,
      hyperliquidChain: d.is_testnet ? 'Testnet' : 'Mainnet',
      agentAddress: d.agent_address,
      agentName: 'kfreqaihl',
      nonce: nonce,
    };
    const typedData = {
      domain: {name: 'HyperliquidSignTransaction', version: '1', chainId: chainId,
               verifyingContract: '0x0000000000000000000000000000000000000000'},
      types: {
        EIP712Domain: [
          {name: 'name', type: 'string'}, {name: 'version', type: 'string'},
          {name: 'chainId', type: 'uint256'}, {name: 'verifyingContract', type: 'address'},
        ],
        'HyperliquidTransaction:ApproveAgent': [
          {name: 'hyperliquidChain', type: 'string'},
          {name: 'agentAddress', type: 'address'},
          {name: 'agentName', type: 'string'},
          {name: 'nonce', type: 'uint64'},
        ],
      },
      primaryType: 'HyperliquidTransaction:ApproveAgent',
      message: action,
    };
    setApproveMsg('ウォレットで署名してください…');
    const sig = await window.ethereum.request({
      method: 'eth_signTypedData_v4', params: [from, JSON.stringify(typedData)]});
    const signature = {r: '0x' + sig.slice(2, 66), s: '0x' + sig.slice(66, 130), v: parseInt(sig.slice(130, 132), 16)};
    const url = (d.is_testnet ? 'https://api.hyperliquid-testnet.xyz' : 'https://api.hyperliquid.xyz') + '/exchange';
    setApproveMsg('Hyperliquidに送信中…');
    const res = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action, nonce, signature})});
    const out = await res.json().catch(() => ({}));
    if (out.status === 'ok') {
      setApproveMsg('委任署名を送信しました。オンチェーン確認中…');
      const r = await api('confirm_approval', {method: 'POST'});
      if (r.agent_approved) { setApproveMsg('委任が完了しました。'); loadDashboard(); }
      else setApproveMsg('送信は成功しましたが、まだオンチェーンに反映されていません。数秒後に「委任済みか確認」を押してください。', true);
    } else {
      setApproveMsg('Hyperliquidが受理しませんでした: ' + JSON.stringify(out).slice(0, 200), true);
    }
  } catch (e) {
    setApproveMsg('委任に失敗: ' + (e && e.message ? e.message : e), true);
  }
}

// Unified Account を有効化(userSetAbstraction)。approveAgentと同じユーザー署名方式。
// 構造は公式SDK(sign_user_set_abstraction_action)から確認済み。
async function enableUnifiedAccount(d) {
  const el = document.getElementById('unified-msg');
  const setMsg = (t, err) => { if (el) { el.textContent = t; el.style.color = err ? 'var(--down)' : 'var(--up)'; } };
  if (!window.ethereum) { setMsg('MetaMask等のウォレットが見つかりません。', true); return; }
  try {
    setMsg('ウォレットに接続中…');
    const accounts = await window.ethereum.request({method: 'eth_requestAccounts'});
    const from = accounts[0];
    const chainIdHex = await window.ethereum.request({method: 'eth_chainId'});
    const nonce = Date.now();
    const action = {
      type: 'userSetAbstraction',
      signatureChainId: chainIdHex,
      hyperliquidChain: d.is_testnet ? 'Testnet' : 'Mainnet',
      user: from.toLowerCase(),
      abstraction: 'unifiedAccount',
      nonce: nonce,
    };
    const typedData = {
      domain: {name: 'HyperliquidSignTransaction', version: '1', chainId: parseInt(chainIdHex, 16),
               verifyingContract: '0x0000000000000000000000000000000000000000'},
      types: {
        EIP712Domain: [
          {name: 'name', type: 'string'}, {name: 'version', type: 'string'},
          {name: 'chainId', type: 'uint256'}, {name: 'verifyingContract', type: 'address'},
        ],
        'HyperliquidTransaction:UserSetAbstraction': [
          {name: 'hyperliquidChain', type: 'string'},
          {name: 'user', type: 'address'},
          {name: 'abstraction', type: 'string'},
          {name: 'nonce', type: 'uint64'},
        ],
      },
      primaryType: 'HyperliquidTransaction:UserSetAbstraction',
      message: action,
    };
    setMsg('ウォレットで署名してください…');
    const sig = await window.ethereum.request({method: 'eth_signTypedData_v4', params: [from, JSON.stringify(typedData)]});
    const signature = {r: '0x' + sig.slice(2, 66), s: '0x' + sig.slice(66, 130), v: parseInt(sig.slice(130, 132), 16)};
    const url = (d.is_testnet ? 'https://api.hyperliquid-testnet.xyz' : 'https://api.hyperliquid.xyz') + '/exchange';
    setMsg('Hyperliquidに送信中…');
    const res = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action, nonce, signature})});
    const out = await res.json().catch(() => ({}));
    if (out.status === 'ok') { setMsg('Unified Accountを有効化しました。spot残高がそのまま先物担保になります。'); setTimeout(loadDashboard, 1500); }
    else setMsg('受理されませんでした: ' + JSON.stringify(out).slice(0, 200), true);
  } catch (e) {
    setMsg('失敗: ' + (e && e.message ? e.message : e), true);
  }
}

function renderKpi(d, spot) {
  const dash = d.dashboard || {};
  const perpCount = (dash.positions || []).length;
  const spotCount = spot ? (spot.positions || []).length : 0;
  const spotBal = spot ? (spot.account_value_usd || 0) : 0;
  // kfreqaiと完全に同じ4カード(Bot / 残高（推定） / 累計損益（確定分） / 保有中ポジション)。
  // 現物(ペーパー)+先物(testnet)を合算して1つの残高・損益にする(kfreqaiと同じ構成)。
  const totalBal = (dash.account_value_usd || 0) + spotBal;
  const totalPnl = (dash.closed_pnl_total_usd || 0) + (spot ? (spot.closed_pnl_total_usd || 0) : 0);
  // 勝率: freqtradeのprofit.winrate相当を、先物+現物の決済fillsから算出
  const cf = (dash.fills || []).concat(spot ? (spot.fills || []) : []).filter(f => Math.abs(f.closed_pnl_usd || 0) > 1e-9);
  const wins = cf.filter(f => (f.closed_pnl_usd || 0) > 0).length;
  const wr = cf.length ? (wins / cf.length * 100).toFixed(1) + '%' : '-';
  document.getElementById('kpi-grid').innerHTML =
    card('Bot', 'kfreqaihl', '現物ロング＋先物の2エンジンを1画面表示 / ' + (d.is_testnet ? 'testnet（検証）' : 'live')) +
    card('残高（推定）', usd(totalBal), '先物 ' + usd(dash.account_value_usd) + ' ＋ 現物 ' + usd(spotBal)) +
    card('累計損益（確定分）', (totalPnl >= 0 ? '+' : '') + usd(totalPnl), '現物ロング＋先物合計', totalPnl < 0 ? 'down' : 'up') +
    card('保有中ポジション', (perpCount + spotCount), '勝率: ' + wr);
}
function card(label, value, sub, cls) {
  return '<div class="card"><div class="label">' + label + '</div><div class="value ' + (cls||'') + '">' + value + '</div><div class="sub">' + (sub||'') + '</div></div>';
}

function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }

// 「動いている戦略」カード: 戦略名＋説明＋現在のプリセット＋主要設定＋調整導線
async function renderStrategyCard() {
  const info = await api('strategy_info');
  const sec = document.getElementById('strategy-section');
  if (!info || info.__error) { if (sec) sec.style.display = 'none'; return; }
  if (sec) sec.style.display = '';
  const st = info.strategy || {};
  const cur = info.presets.find(p => p.id === info.current_preset);
  const badge = cur
    ? '<span class="preset-badge">' + esc(cur.emoji + ' ' + cur.name) + '</span>'
    : '<span class="preset-badge custom">✎ カスタム</span>';
  const s = info.summary || {};
  const sideTxt = (s.is_long_enabled && s.is_short_enabled) ? '両建て'
    : s.is_long_enabled ? 'ロングのみ' : s.is_short_enabled ? 'ショートのみ' : '停止';
  const chips =
    '<span class="chip">枠 <b>' + (s.max_open_trades ?? '-') + '</b></span>' +
    '<span class="chip">レバ <b>' + (s.leverage ?? '-') + '倍</b></span>' +
    '<span class="chip">方向 <b>' + sideTxt + '</b></span>' +
    '<span class="chip">ブレイクゲート <b>' + (s.enable_breakout_gate ? 'ON' : 'OFF') + '</b></span>' +
    '<span class="chip">損切り <b>' + (s.stoploss_pct ?? '-') + '%</b></span>' +
    '<span class="chip">EMA <b>' + (s.ema_fast ?? '-') + '/' + (s.ema_slow ?? '-') + '</b></span>';
  const how = (st.how || []).map(h => '<li>' + esc(h) + '</li>').join('');
  const uniCount = info.universe_count || (info.universe || []).length;
  const uni = (info.universe || []).map(u => '<span class="chip"><b>' + esc(u) + '</b></span>').join(' ');
  document.getElementById('strategy-card').innerHTML =
    '<div class="strat-head"><img class="brainicon" src="images/kcbrain-icon.png" alt="kcbrain" title="判断エンジン: kcbrain"><span class="name">' + esc(st.name) + '</span>' + badge + '</div>' +
    '<div class="strat-tagline">' + esc(st.tagline) + '</div>' +
    '<div class="strat-tagline">' + esc(st.market) + '</div>' +
    '<ul class="strat-how">' + how + '</ul>' +
    '<div class="strat-chips">' + chips + '</div>' +
    // 取引対象ユニバース(全員に表示・なぜ53かの説明つき)
    '<div class="strat-adjust" style="margin-top:14px">取引対象：<b>' + uniCount + '銘柄</b>。現物・先物（ロング／ショート）・アリーナのすべてを<b>同じ' + uniCount + '銘柄の同一ユニバース</b>で統一しています。MEXC と Hyperliquid の両取引所で取引でき、kfreqai（現物）と同じ土俵で横比較できる銘柄に絞りました。</div>' +
    '<div class="strat-chips" style="margin-top:6px">' + uni + '</div>' +
    // 調整導線(戦略設定/戦略会議)は管理可能ユーザーのみ。参照モードでは出さない
    (CAN_MANAGE
      ? '<div class="strat-adjust">調整するには <a href="?view=settings">戦略設定</a> でプリセットを選ぶか、'
        + '<a href="?view=chat">Kurageさんと戦略会議</a> で「積極型にして」などと話しかけてください。</div>'
      : '<div class="strat-adjust" style="color:var(--muted)">この戦略で運用中です（公開ビュー・参照のみ）。</div>');
}

function renderPositions(dash, elId) {
  elId = elId || 'positions-body';
  const rows = dash.positions || [];
  if (!rows.length) { document.getElementById(elId).innerHTML = '<div class="empty">現在保有中のポジションはありません。</div>'; return; }
  // kfreqaiと同じ列構成: ペア / 方向 / 金額 / 平均建値 / 現在値 / 含み損益 / 建玉時刻
  let h = '<div class="tscroll"><table><tr><th>ペア</th><th>方向</th><th>金額(USDC)</th><th>平均建値</th><th>現在値</th><th>含み損益</th><th>建玉時刻(日本時間)</th></tr>';
  for (const p of rows) {
    const cls = (p.unrealized_pnl_usd < 0) ? 'down' : 'up';
    const dir = p.is_short ? '<span style="color:var(--down);font-weight:700">Short</span>' : 'Long';
    h += '<tr><td><b>' + esc(p.coin) + '</b></td><td>' + dir + '</td>'
      + '<td><div><b>' + usd(p.position_value_usd) + '</b></div><div style="font-size:11px;opacity:.7">' + p.size + ' 枚</div></td>'
      + '<td>' + p.entry_px + '</td><td>' + (p.cur_px != null ? p.cur_px : '-') + '</td>'
      + '<td class="' + cls + '"><div>' + pct(p.return_on_equity) + '</div><div style="font-size:11.5px;opacity:.75">' + usd(p.unrealized_pnl_usd) + '</div></td>'
      + '<td>' + (p.opened_at ? jst(p.opened_at) : '—') + '</td></tr>';
  }
  document.getElementById(elId).innerHTML = h + '</table></div>';
}

function renderFills(dash, elId) {
  elId = elId || 'fills-body';
  const rows = dash.fills || [];
  if (!rows.length) { document.getElementById(elId).innerHTML = '<div class="empty">まだ約定履歴がありません。</div>'; return; }
  // kfreqaiと同じ列構成: ペア / 方向 / 建玉時刻 / 損益 / 決済理由 / クローズ時刻
  // (Hyperliquidの約定は建玉/クローズが別イベントのため、建玉時刻は約定単位では—)
  let h = '<div class="tscroll" style="max-height:430px;overflow-y:auto"><table><tr><th>ペア</th><th>方向</th><th>建玉時刻(日本時間)</th><th>損益</th><th>決済理由</th><th>クローズ時刻(日本時間)</th></tr>';
  for (const f of rows) {
    const cls = ((f.closed_pnl_usd || 0) < 0) ? 'down' : 'up';
    const dir = String(f.dir || '').includes('short') ? '<span style="color:var(--down);font-weight:700">Short</span>' : 'Long';
    const pnl = f.closed_pnl_usd ? '<span class="' + cls + '">' + usd(f.closed_pnl_usd) + '</span>' : '-';
    h += '<tr><td><b>' + esc(f.coin) + '</b></td><td>' + dir + '</td><td>—</td><td>' + pnl + '</td><td>' + esc(f.dir || '-') + '</td><td>' + jst(f.time_ms) + '</td></tr>';
  }
  document.getElementById(elId).innerHTML = h + '</table></div>';
}

function renderDaily(dash, elId) {
  elId = elId || 'daily-body';
  const rows = dash.daily || [];
  if (!rows.length) { document.getElementById(elId).innerHTML = '<div class="empty">データがありません。</div>'; return; }
  let h = '<table><tr><th>日付</th><th>損益</th><th>約定数</th></tr>';
  for (const d of rows) {
    const cls = (d.abs_profit < 0) ? 'down' : 'up';
    h += '<tr><td>' + d.date + '</td><td class="' + cls + '">' + usd(d.abs_profit) + '</td><td>' + d.trade_count + '</td></tr>';
  }
  document.getElementById(elId).innerHTML = h + '</table>';
}

async function loadDashboard() {
  const d = await api('dashboard');
  if (d.__error) { setBadge({}); document.getElementById('positions-body').innerHTML = '<div class="error">読み込みに失敗しました</div>'; return; }
  setBadge(d);
  renderSetup(d);
  // 委任済みで、メイン口座がある間はUnified Account有効化ボタンを出す
  // (spot資金をperp担保にするため。既に有効なら押しても無害)
  // Unified Account有効化ボタンは「まだ有効化していない」ときだけ出す。
  // 一度有効化すると d.unified_enabled=true になり、以後は隠す(再表示不要)。
  const uc = document.getElementById('unified-card');
  if (uc && d.agent_approved && d.main_wallet_address && !d.mock && !d.unified_enabled) {
    uc.style.display = '';
    const ub = document.getElementById('unifiedbtn');
    if (ub && !ub._wired) { ub._wired = true; ub.onclick = () => enableUnifiedAccount(d); }
  } else if (uc) { uc.style.display = 'none'; }
  renderStrategyCard();
  // 現物ペーパー(委任不要)も取得して、先物の表・約定に種別列で合流させる。
  // 口座が別(先物=testnet実 / 現物=ペーパー)なので、資産カードは合算せず併記する。
  let spot = null;
  try { const s = await api('paper_spot_dashboard'); if (s && !s.__error && s.enabled) spot = s; } catch (e) {}
  const perp = d.dashboard || {};
  const merged = {
    positions: [].concat((perp.positions || []),
                         (spot ? (spot.positions || []).map(p => Object.assign({_spot: true}, p)) : [])),
    fills: [].concat((perp.fills || []),
                     (spot ? (spot.fills || []).map(f => Object.assign({_spot: true}, f)) : []))
                .sort((a, b) => (b.time_ms || 0) - (a.time_ms || 0)),
    daily: perp.daily || [],
  };
  window._spotDash = spot;
  if (d.dashboard || spot) {
    renderKpi(d, spot);
    renderPositions(merged); renderFills(merged); renderDaily(perp);
  }
  else if (d.dashboard_error) { document.getElementById('positions-body').innerHTML = '<div class="error">口座照会に失敗: ' + d.dashboard_error + '</div>'; }
  else { document.getElementById('kpi-grid').innerHTML = ''; document.getElementById('positions-body').innerHTML = '<div class="empty">メイン口座を登録すると口座状況が表示されます。</div>'; }
}

// ---------- チャット ----------
function addMsg(role, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'kurage');
  div.textContent = text;
  document.getElementById('chatlog').appendChild(div);
  document.getElementById('chatlog').scrollTop = 1e9;
}
function initChat() {
  const input = document.getElementById('chatinput');
  const grow = () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; };
  input.addEventListener('input', grow);
  const sendBtn = document.getElementById('chatsend');
  const send = async () => {
    const msg = input.value.trim(); if (!msg) return;
    addMsg('user', msg); input.value = ''; grow();
    // 「考え中…」インジケータ(3点アニメ)を出す。応答が来たら消す。
    const thinking = document.createElement('div');
    thinking.className = 'msg kurage thinking';
    thinking.innerHTML = '<span></span><span></span><span></span>';
    document.getElementById('chatlog').appendChild(thinking);
    document.getElementById('chatlog').scrollTop = 1e9;
    sendBtn.disabled = true;
    try {
      const out = await api('chat', {method:'POST', body:{message: msg}});
      thinking.remove();
      if (out.__error) { addMsg('kurage', 'ごめんなさい、うまく応答できませんでした（' + (out.detail || out.__status) + '）'); }
      else { addMsg('kurage', out.reply || '(応答なし)'); }
    } catch (e) {
      thinking.remove();
      addMsg('kurage', 'ごめんなさい、通信に失敗しました。');
    } finally {
      sendBtn.disabled = false; input.focus();
    }
  };
  sendBtn.onclick = send;
  // Enterで送信、Shift+Enterで改行。IME変換確定のEnter(isComposing/229)は無視
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); send(); }
  });
}

// ---------- 戦略設定 ----------
async function loadPresets() {
  const info = await api('strategy_info');
  const grid = document.getElementById('preset-grid');
  if (!grid) return;
  if (info.__error) { grid.innerHTML = '<div class="error">プリセットを取得できませんでした</div>'; return; }
  let html = '';
  for (const p of info.presets) {
    const active = (p.id === info.current_preset) ? ' active' : '';
    html += '<button class="preset-opt' + active + '" data-preset="' + p.id + '">' +
      '<div class="pname">' + esc(p.emoji + ' ' + p.name) + '</div>' +
      '<div class="pdesc">' + esc(p.desc) + '</div>' +
      '<div class="pmeta">レバ' + p.params.leverage + '倍 / 枠' + p.params.max_open_trades +
      ' / ゲート' + (p.params.enable_breakout_gate ? 'ON' : 'OFF') + '</div></button>';
  }
  if (info.current_preset === 'custom') {
    html += '<div class="preset-opt active" style="cursor:default"><div class="pname">✎ カスタム</div>' +
      '<div class="pdesc">下の詳細設定で数値を調整した状態です。プリセットを選ぶと上書きされます。</div></div>';
  }
  grid.innerHTML = html;
  grid.querySelectorAll('[data-preset]').forEach((btn) => {
    btn.onclick = async () => {
      document.getElementById('presetmsg').textContent = '適用中…';
      const out = await api('apply_preset', {method:'POST', body:{preset: btn.dataset.preset}});
      document.getElementById('presetmsg').textContent = out.__error ? '適用に失敗しました' : 'プリセットを適用しました';
      loadPresets(); loadParams();
    };
  });
}
async function loadParams() {
  const p = await api('params');
  if (p.__error) { document.getElementById('params-grid').innerHTML = '<div class="error">未取得</div>'; return; }
  let html = '';
  for (const spec of p.params) {
    const label = (spec.label && spec.label.ja) || spec.key;
    if (spec.type === 'bool') {
      html += '<div class="param"><label>' + label + '</label><label><input type="checkbox" data-key="' + spec.key + '" ' + (spec.value ? 'checked' : '') + '> 有効</label></div>';
    } else {
      html += '<div class="param"><label>' + label + ' (' + spec.min + '〜' + spec.max + ')</label><input type="number" data-key="' + spec.key + '" value="' + spec.value + '" min="' + spec.min + '" max="' + spec.max + '" step="' + (spec.step||1) + '"></div>';
    }
  }
  document.getElementById('params-grid').innerHTML = html;
}
function initSettings() {
  document.getElementById('paramssave').onclick = async () => {
    const updates = {};
    document.querySelectorAll('#params-grid [data-key]').forEach((el) => { updates[el.dataset.key] = el.type === 'checkbox' ? el.checked : Number(el.value); });
    const out = await api('params_save', {method:'POST', body:{updates}});
    document.getElementById('paramsmsg').textContent = out.__error ? '保存失敗' : '保存しました（カスタム設定）';
    loadPresets(); loadParams();
  };
  loadPresets();
  loadParams();
}

// ---------- FX・商品・指数（β） ----------
function dirJa(d) {
  return ({long:'ロング（買い）有望', short:'ショート（売り）有望', watch:'様子見', avoid:'見送り推奨'})[d] || '様子見';
}
async function renderFxStrategy() {
  const info = await api('fx_info');
  const el = document.getElementById('fx-strategy-card');
  if (!info || info.__error) { el.innerHTML = '<div class="error">FX情報を取得できませんでした</div>'; return; }
  const st = info.strategy || {}, s = info.settings || {};
  const sideTxt = (s.is_long_enabled && s.is_short_enabled) ? '両建て' : s.is_long_enabled ? 'ロングのみ' : 'ショートのみ';
  const chips =
    '<span class="chip">枠 <b>' + s.max_open_trades + '</b></span>' +
    '<span class="chip">レバ <b>' + s.leverage + '倍</b></span>' +
    '<span class="chip">方向 <b>' + sideTxt + '</b></span>' +
    '<span class="chip">損切り <b>' + s.stoploss_pct + '%</b></span>' +
    '<span class="chip">トレール発動 <b>' + s.peak_trail_trigger_pct + '%</b></span>' +
    '<span class="chip">EMA <b>' + s.ema_fast + '/' + s.ema_slow + '</b></span>';
  const how = (st.how || []).map(h => '<li>' + esc(h) + '</li>').join('');
  const uni = (info.universe || []).map(u => '<span class="chip">' + esc(u) + '</span>').join(' ');
  document.getElementById('fx-strategy-card').innerHTML =
    '<div class="strat-head"><img class="brainicon" src="images/kfxbrain-icon.png" alt="kfxbrain" title="判断エンジン: kfxbrain"><span class="name">' + esc(st.name) + '</span>' +
      '<span class="preset-badge custom">β 先行体験</span></div>' +
    '<div class="strat-tagline">' + esc(st.tagline) + '</div>' +
    '<div class="strat-tagline">' + esc(st.market) + '</div>' +
    '<ul class="strat-how">' + how + '</ul>' +
    '<div class="strat-chips">' + chips + '</div>' +
    '<div class="strat-adjust" style="margin-top:14px">対象銘柄：<b>' + (info.universe || []).length + '銘柄</b>（FX通貨・貴金属・エネルギー・穀物・株価指数。Hyperliquid の builder-dex で30日以上の価格履歴が取れる銘柄に絞っています）</div><div class="strat-chips" style="margin-top:6px">' + uni + '</div>';
}
async function loadPaperFx() {
  const d = await api('paper_fx_dashboard');
  const body = document.getElementById('paperfx-body');
  const detail = document.getElementById('paperfx-detail');
  if (d.__error) { body.innerHTML = '<div class="error">ペーパー口座の取得に失敗しました</div>'; return; }
  if (!d.enabled) {
    detail.style.display = 'none';
    // 一般ユーザーはkfxbrainをx402(DeepSeek)で使うため、支払い用ウォレットの接続が必要
    // (取引の委任approveAgentは不要・接続のみ)。adminは無料gemmaなので接続不要。
    const walletNote = IS_ADMIN
      ? '<div style="font-size:12px;color:var(--muted);margin-top:8px">開始すると毎時、mainnetの実FX価格で戦略＋kfxbrainのAI判断に沿って自動売買をシミュレーションします（管理者はgemma4・無料）。</div>'
      : '<div style="font-size:12px;color:var(--muted);margin-top:8px">AIの判断（kfxbrain）は<b>x402で従量課金</b>のため、支払い用ウォレットの接続が必要です（<b>取引の委任は不要・接続のみ</b>。支払いにはUSDCが必要）。実弾の取引は行いません。</div>';
    body.innerHTML = '<div class="card"><p style="margin-top:0;font-size:14px">仮想資金 <b>$' + (d.starting_equity || 1000) +
      '</b> でFXのAI自動売買を体験できます。<b>実弾は動きません</b>（ペーパー）。</p>' +
      '<div class="row"><button class="btn" id="paperfx-start">' + (IS_ADMIN ? 'ペーパートレードを始める' : 'ウォレットを接続して始める') + '</button></div>' +
      '<div id="paperfx-start-msg" style="font-size:12px;color:var(--down);margin-top:6px"></div>' +
      walletNote + '</div>';
    document.getElementById('paperfx-start').onclick = async () => {
      const btn = document.getElementById('paperfx-start');
      const msg = document.getElementById('paperfx-start-msg');
      let payer = '';
      if (!IS_ADMIN) {
        if (!window.ethereum) { msg.textContent = 'MetaMask等のウォレットが見つかりません。'; return; }
        try {
          btn.disabled = true; msg.style.color = 'var(--muted)'; msg.textContent = 'ウォレットに接続中…';
          const accounts = await window.ethereum.request({method: 'eth_requestAccounts'});
          payer = accounts[0];
        } catch (e) { btn.disabled = false; msg.style.color = 'var(--down)'; msg.textContent = '接続に失敗しました。'; return; }
      }
      btn.disabled = true;
      const r = await api('paper_fx_start', {method:'POST', body:{payer_wallet: payer}});
      if (r.__error) { btn.disabled = false; msg.style.color = 'var(--down)'; msg.textContent = (r.detail || '開始に失敗しました'); return; }
      loadPaperFx();
    };
    return;
  }
  detail.style.display = '';
  const posCount = (d.positions || []).length;
  const cls = (d.closed_pnl_total_usd || 0) < 0 ? 'down' : 'up';
  const ucls = (d.unrealized_pnl_usd || 0) < 0 ? 'down' : 'up';
  body.innerHTML = '<div class="grid">' +
    card('口座評価額（仮想）', usd(d.account_value_usd), '初期 $' + d.starting_equity) +
    card('確定損益', '<span class="' + cls + '">' + usd(d.closed_pnl_total_usd) + '</span>', '約定 ' + (d.fills_count || 0) + ' 件') +
    card('含み損益', '<span class="' + ucls + '">' + usd(d.unrealized_pnl_usd) + '</span>', '保有中の評価') +
    card('保有ポジション', posCount + ' / ' + (d.max_open_trades || 8) + ' 枠', '同時保有の枠数') +
    '</div>' +
    '<div class="row" style="margin-top:4px"><button class="btn ghost" id="paperfx-reset">口座をリセット</button>' +
    '<span style="font-size:12px;color:var(--muted)">仮想$' + d.starting_equity + 'で最初からやり直します</span></div>' +
    (d.payer_wallet ? '<div style="font-size:11px;color:var(--muted);margin-top:8px">AI利用料(x402)の支払いウォレット: <span class="mono" style="padding:2px 6px">' + esc(d.payer_wallet) + '</span>（取引の委任はしていません）</div>' : '');
  document.getElementById('paperfx-reset').onclick = async () => {
    if (!confirm('ペーパー口座をリセットしますか？（建玉・履歴が消えます）')) return;
    await api('paper_fx_reset', {method:'POST', body:{}});
    loadPaperFx();
  };
  renderPositions(d, 'paperfx-positions');
  renderFills(d, 'paperfx-fills');
  renderDaily(d, 'paperfx-daily');
}
function initFx() {
  loadPaperFx();
  renderFxStrategy();
  document.getElementById('fx-bt-run').onclick = async () => {
    const out = document.getElementById('fx-bt-result');
    const days = Number(document.getElementById('fx-bt-days').value);
    out.innerHTML = '<div class="empty">バックテスト実行中…（mainnet実データを取得します。少しお待ちください）</div>';
    const r = await api('backtest', {method:'POST', body:{market:'fx', days}});
    if (r.__error || !r.ok) { out.innerHTML = '<div class="error">バックテストに失敗しました' + (r.reason ? '（' + esc(r.reason) + '）' : '') + '</div>'; return; }
    const cls = (r.total_return_pct >= 0) ? 'up' : 'down';
    out.innerHTML =
      '<div class="grid" style="margin-bottom:12px">' +
      card('リターン', '<span class="' + cls + '">' + (r.total_return_pct>=0?'+':'') + r.total_return_pct + '%</span>', '初期$' + r.starting_equity + ' → $' + r.final_equity) +
      card('取引回数', r.closed_trades + ' 回', '約' + r.trades_per_day + ' 回/日') +
      card('勝率', r.win_rate_pct + '%', '勝' + r.wins + ' / 負' + r.losses) +
      card('最大DD', r.max_drawdown_pct + '%', r.covered_days + '日・' + r.coins_used + '銘柄') +
      '</div>' +
      '<div class="mono" style="white-space:pre-wrap">' + esc(r.summary_ja || '') + '</div>';
  };
  document.getElementById('fx-judge-run').onclick = async () => {
    const out = document.getElementById('fx-judge-result');
    out.innerHTML = '<div class="empty"><img class="brainicon" src="images/kfxbrain-icon.png" alt=""> kfxbrainが判断中…（数十秒）</div>';
    const r = await api('fx_judgment');
    if (r.__error || !r.rows) { out.innerHTML = '<div class="error">AI判断に失敗しました' + (r.detail ? '（' + esc(r.detail) + '）' : '') + '</div>'; return; }
    if (!r.rows.length) { out.innerHTML = '<div class="empty">判断結果が空でした。しばらくして再度お試しください。</div>'; return; }
    let h = '<div class="tscroll"><table><tr><th>銘柄</th><th>AI判断</th><th>スコア</th><th>信頼度</th><th>理由</th></tr>';
    for (const row of r.rows) {
      const veto = row.veto ? ' <span class="down">(見送り)</span>' : '';
      h += '<tr><td><b>' + esc(row.symbol) + '</b></td><td>' + esc(dirJa(row.direction)) + veto + '</td><td>' + (row.score ?? '-') + '</td><td>' + (row.confidence ?? '-') + '</td><td style="max-width:320px">' + esc(row.why || '') + '</td></tr>';
    }
    out.innerHTML = h + '</table></div><div style="font-size:11px;color:var(--muted);margin-top:6px">判断: kfxbrain（' + esc(r.model || '') + '）。AIの参考判断です。</div>';
  };
}

if (VIEW === 'summary') loadDashboard();
else if (VIEW === 'fx' && typeof initFx === 'function') { setBadgeFromDashboard(); initFx(); }
else if (VIEW === 'chat' && typeof initChat === 'function') { setBadgeFromDashboard(); initChat(); }
else if (VIEW === 'settings' && typeof initSettings === 'function') { setBadgeFromDashboard(); initSettings(); }

async function setBadgeFromDashboard() { const d = await api('dashboard'); if (!d.__error) setBadge(d); }
</script>
</body>
</html>
