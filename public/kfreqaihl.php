<?php
require_once __DIR__ . '/config.php';
require_once __DIR__ . '/auth_common.php';

$auth = url2ai_auth_bootstrap();
$ADMIN_USERNAME = url2ai_auth_admin_user(); // xb_bittensor

// --- 同一オリジン中継: hl_api.py はHTTPSページから直接叩けない(mixed content)ので
// PHPがcurlで中継する。X-Hl-Tokenはここでだけ付与し、ブラウザには渡さない。
if (isset($_GET['api'])) {
    if (empty($auth['logged_in'])) { http_response_code(401); echo '{"error":"login required"}'; exit; }
    $username = $auth['session_user'];
    $is_admin = ($username === $ADMIN_USERNAME);
    $base = rtrim(KFREQAI_HL_API_BASE, '/');
    header('Content-Type: application/json; charset=utf-8');

    $action = $_GET['api'];
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
?>
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kurage FreqAI Trade for Hyperliquid — AI自動取引（Crypto / FX）</title>
<meta name="description" content="ウォレット1つで始める、Hyperliquid上のAI自動取引。kcbrain/kfxbrainのAI判断とkfreqai共通戦略を、サーバー不要で。">
<link rel="stylesheet" href="assets/kurage-avatar.css">
<style>
  :root {
    --indigo: #3949ab; --cyan: #00acc1; --bg: #f6f8fb; --card: #ffffff;
    --ink: #1c2536; --muted: #66748f; --border: #e3e8f0;
    --up: #1baf7a; --down: #d6453d;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: linear-gradient(180deg, #eef2fb 0%, var(--bg) 320px); color: var(--ink); }
  header { padding: 28px 20px 18px; max-width: 1080px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
  header .brand { display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 20px; margin: 0; line-height: 1.3; }
  header h1 span { color: var(--indigo); }
  header h1 .sub { display: block; font-size: 11px; color: var(--muted); font-weight: 500; letter-spacing: .02em; }
  .brainicon { width: 22px; height: 22px; border-radius: 6px; vertical-align: middle; object-fit: cover; }
  .brain-chip { display: inline-flex; align-items: center; gap: 7px; background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px 4px 5px; font-size: 12px; color: var(--muted); }
  .brain-chip img { width: 24px; height: 24px; border-radius: 50%; object-fit: cover; }
  .brain-chip b { color: var(--ink); }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; margin-left: 8px; vertical-align: middle; }
  .badge.dry { background: #fff3cd; color: #8a6100; }
  .badge.live { background: #fde2e1; color: #a4201b; }
  .badge.gemma { background: #e3f2fd; color: #0d47a1; }
  .badge.deepseek { background: #ede7f6; color: #4527a0; }
  .userbar { font-size: 13px; color: var(--muted); }
  .userbar a { color: var(--indigo); text-decoration: none; margin-left: 10px; }
  main { max-width: 1080px; margin: 0 auto; padding: 0 20px 60px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 20px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px; }
  .card .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .card .value { font-size: 26px; font-weight: 700; }
  .card .sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .up { color: var(--up); } .down { color: var(--down); }
  section { margin-bottom: 28px; }
  section h2 { font-size: 15px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin: 0 0 10px; }
  table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
  th, td { text-align: left; padding: 10px 14px; font-size: 13px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; background: #f9fafc; }
  tr:last-child td { border-bottom: none; }
  .empty { color: var(--muted); font-size: 13px; padding: 16px; background: var(--card); border: 1px dashed var(--border); border-radius: 12px; }
  .gate { max-width: 480px; margin: 80px auto; text-align: center; }
  .btn { display: inline-block; padding: 10px 22px; border-radius: 999px; background: linear-gradient(90deg, var(--indigo), var(--cyan)); color: #fff; text-decoration: none; font-weight: 600; border: none; cursor: pointer; font-size: 14px; }
  .btn.ghost { background: #66748f; }
  .btn:disabled { opacity: .5; cursor: default; }
  .tabs { display: flex; gap: 6px; margin: 0 0 20px; flex-wrap: wrap; }
  .tabs a { padding: 7px 16px; border-radius: 999px; font-size: 13px; text-decoration: none; color: var(--muted); border: 1px solid var(--border); background: var(--card); }
  .tabs a.active { background: var(--indigo); color: #fff; border-color: var(--indigo); }
  .notice { background: #fff3cd; color: #8a6100; padding: 12px 16px; border-radius: 10px; font-size: 13px; margin-bottom: 18px; }
  .error { background: #fde2e1; color: #a4201b; padding: 12px 16px; border-radius: 10px; font-size: 13px; margin-bottom: 18px; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; background: #f9fafc; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  input[type=text] { flex: 1; min-width: 200px; padding: 9px 12px; border-radius: 8px; border: 1px solid var(--border); font-size: 13px; }
  .tscroll { overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
  .tscroll table { border: 0; border-radius: 0; }
  .chatlog { max-height: 380px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; padding: 4px; }
  .msg { padding: 10px 14px; border-radius: 12px; font-size: 13px; line-height: 1.6; max-width: 85%; }
  .msg.user { align-self: flex-end; background: var(--indigo); color: #fff; }
  .msg.kurage { align-self: flex-start; background: #f1f3f9; color: var(--ink); }
  .composer { display: flex; gap: 10px; align-items: flex-end; border: 1px solid var(--border); border-radius: 14px; padding: 8px 8px 8px 14px; background: var(--card); }
  .composer textarea { flex: 1; border: none; outline: none; resize: none; font-size: 15px; line-height: 1.5; font-family: inherit; max-height: 160px; background: transparent; color: var(--ink); padding: 6px 0; }
  .composer .btn { flex: 0 0 auto; padding: 9px 18px; }
  .msg.thinking { display: flex; gap: 5px; align-items: center; }
  .msg.thinking span { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); opacity: .5; animation: kblink 1.2s infinite ease-in-out; }
  .msg.thinking span:nth-child(2) { animation-delay: .2s; }
  .msg.thinking span:nth-child(3) { animation-delay: .4s; }
  @keyframes kblink { 0%, 60%, 100% { opacity: .3; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-3px); } }
  .params-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-top: 8px; }
  .param { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }
  .param label { font-size: 12px; color: var(--muted); display: block; margin-bottom: 6px; }
  .param input[type=number] { width: 100%; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--border); }
  footer { text-align: center; color: var(--muted); font-size: 12px; padding: 30px 20px; }
  .setup-step { font-size: 13px; line-height: 1.8; }
  .setup-step b { color: var(--indigo); }
  /* 動いている戦略カード */
  .strat-card { background: linear-gradient(135deg, #eef2ff 0%, #f6f9ff 100%); border: 1px solid #d6def5; border-radius: 14px; padding: 18px 20px; }
  .strat-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .strat-head .name { font-size: 17px; font-weight: 700; }
  .preset-badge { display: inline-flex; align-items: center; gap: 4px; padding: 3px 12px; border-radius: 999px; background: var(--indigo); color: #fff; font-size: 12px; font-weight: 600; }
  .preset-badge.custom { background: #66748f; }
  .strat-tagline { font-size: 13px; color: var(--muted); margin: 8px 0 2px; }
  .strat-how { margin: 10px 0 0; padding-left: 18px; font-size: 12.5px; color: var(--ink); line-height: 1.7; }
  .strat-how li { margin-bottom: 3px; }
  .strat-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  .chip { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px; font-size: 12px; }
  .chip b { color: var(--indigo); }
  .strat-adjust { font-size: 12px; color: var(--muted); margin-top: 12px; line-height: 1.7; }
  .strat-adjust a { color: var(--indigo); text-decoration: none; font-weight: 600; }
  /* プリセット選択(設定画面) */
  .preset-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 8px 0 4px; }
  .preset-opt { text-align: left; border: 2px solid var(--border); border-radius: 12px; padding: 14px; background: var(--card); cursor: pointer; transition: border-color .15s, box-shadow .15s; }
  .preset-opt:hover { border-color: var(--cyan); }
  .preset-opt.active { border-color: var(--indigo); box-shadow: 0 0 0 3px rgba(57,73,171,.12); }
  .preset-opt .pname { font-size: 15px; font-weight: 700; margin-bottom: 4px; }
  .preset-opt .pdesc { font-size: 12px; color: var(--muted); line-height: 1.6; }
  .preset-opt .pmeta { font-size: 11px; color: var(--indigo); margin-top: 8px; font-weight: 600; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="kurage-avatar-stage kurage-avatar-mini" role="img" aria-label="Kurage avatar"><span class="kurage-avatar-motion"><span class="kurage-avatar-breath"><img class="kurage-avatar-frame kurage-avatar-frame-0" src="avatar/lipsync/kurage_mouth_0.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-1" src="avatar/lipsync/kurage_mouth_1.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-2" src="avatar/lipsync/kurage_mouth_2.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-3" src="avatar/lipsync/kurage_mouth_3.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-4" src="avatar/lipsync/kurage_mouth_4.png" alt=""></span></span></span>
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
<?php if (empty($auth['logged_in'])): ?>
  <div class="gate">
    <p>Hyperliquid上でAIが自動売買する、kfreqaiと同じ戦略の入門版です。<br>
    ウォレット1つとUSDCがあれば、サーバー不要で始められます。</p>
    <a class="btn" href="<?php echo htmlspecialchars($auth['login_url']); ?>">Xでログイン</a>
  </div>
<?php else: ?>

  <div class="tabs">
    <a href="?view=summary" class="<?php echo $view === 'summary' ? 'active' : ''; ?>">本番（Crypto）</a>
    <a href="?view=fx" class="<?php echo $view === 'fx' ? 'active' : ''; ?>">FX・商品・指数（β）</a>
    <a href="?view=chat" class="<?php echo $view === 'chat' ? 'active' : ''; ?>">Kurageさんと戦略会議</a>
    <a href="?view=settings" class="<?php echo $view === 'settings' ? 'active' : ''; ?>">戦略設定</a>
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

  <section id="strategy-section" style="display:none">
    <h2>動いている戦略</h2>
    <div class="strat-card" id="strategy-card">読み込み中…</div>
  </section>

  <section>
    <h2>保有中ポジション（枠）</h2>
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

<?php elseif ($view === 'fx'): ?>
  <div class="notice">FX・商品・指数はHyperliquidのbuilder-dex（xyz）の実価格で動きます。<b>ペーパートレード（仮想資金・実弾ゼロ）</b>で先行体験できます。<?php if (!$is_admin): ?>AI判断（kfxbrain）はx402課金のため<b>ウォレット接続が必要（取引の委任は不要）</b>。<?php endif; ?>実弾の自動売買は近日対応。<br>Cryptoのペーパー（testnet）を試したい方は <a href="?view=summary" style="color:var(--indigo);font-weight:600">本番（Crypto）タブ</a> で委任してください。</div>

  <!-- ペーパーFX(仮想売買) -->
  <section id="paperfx-section">
    <h2>ペーパーFX（仮想トレード）</h2>
    <div id="paperfx-body"><div class="empty">読み込み中…</div></div>
  </section>
  <section id="paperfx-detail" style="display:none">
    <h2>保有中ポジション（ペーパー）</h2>
    <div id="paperfx-positions"></div>
    <h2 style="margin-top:24px">直近の約定（ペーパー）</h2>
    <div id="paperfx-fills"></div>
    <h2 style="margin-top:24px">日次損益（直近7日・JST）</h2>
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

<?php endif; ?>
</main>
<footer>kfreqaihl — Hyperliquid Agent Wallet委任方式（非カストディ）。資金は常にご自身のHyperliquid口座に残ります。戦略ロジックはkfreqaiと共通（strategy_core）。</footer>

<?php if (!empty($auth['logged_in'])): ?>
<script>
const VIEW = <?php echo json_encode($view); ?>;
const IS_ADMIN = <?php echo $is_admin ? 'true' : 'false'; ?>;

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

function renderKpi(d) {
  const dash = d.dashboard || {};
  const posCount = (dash.positions || []).length;
  const slots = d.max_open_trades || 10;
  document.getElementById('kpi-grid').innerHTML =
    card('残高（口座評価額）', usd(dash.account_value_usd), '出金可能: ' + usd(dash.withdrawable_usd)) +
    card('累計損益（確定）', usd(dash.closed_pnl_total_usd), '約定 ' + (dash.fills_count || 0) + ' 件', (dash.closed_pnl_total_usd || 0) < 0 ? 'down' : 'up') +
    card('含み損益', usd(dash.unrealized_pnl_usd), '保有中の評価損益', (dash.unrealized_pnl_usd || 0) < 0 ? 'down' : 'up') +
    card('保有中ポジション', posCount + ' / ' + slots + ' 枠', '同時保有の枠数');
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
  document.getElementById('strategy-card').innerHTML =
    '<div class="strat-head"><img class="brainicon" src="images/kcbrain-icon.png" alt="kcbrain" title="判断エンジン: kcbrain"><span class="name">' + esc(st.name) + '</span>' + badge + '</div>' +
    '<div class="strat-tagline">' + esc(st.tagline) + '</div>' +
    '<div class="strat-tagline">' + esc(st.market) + '</div>' +
    '<ul class="strat-how">' + how + '</ul>' +
    '<div class="strat-chips">' + chips + '</div>' +
    '<div class="strat-adjust">調整するには <a href="?view=settings">戦略設定</a> でプリセットを選ぶか、' +
    '<a href="?view=chat">Kurageさんと戦略会議</a> で「積極型にして」などと話しかけてください。</div>';
}

function renderPositions(dash, elId) {
  elId = elId || 'positions-body';
  const rows = dash.positions || [];
  if (!rows.length) { document.getElementById(elId).innerHTML = '<div class="empty">現在保有中のポジションはありません。</div>'; return; }
  let h = '<div class="tscroll"><table><tr><th>銘柄</th><th>方向</th><th>サイズ</th><th>平均建値</th><th>名目($)</th><th>含み損益</th><th>レバ</th><th>清算価格</th></tr>';
  for (const p of rows) {
    const cls = (p.unrealized_pnl_usd < 0) ? 'down' : 'up';
    h += '<tr><td><b>' + p.coin + '</b></td><td>' + (p.is_short ? 'Short' : 'Long') + '</td><td>' + p.size + '</td><td>' + p.entry_px + '</td><td>' + usd(p.position_value_usd) + '</td>'
      + '<td class="' + cls + '">' + usd(p.unrealized_pnl_usd) + ' <span style="font-size:11px;opacity:.75">' + pct(p.return_on_equity) + '</span></td>'
      + '<td>' + (p.leverage || '-') + 'x</td><td>' + (p.liquidation_px || '-') + '</td></tr>';
  }
  document.getElementById(elId).innerHTML = h + '</table></div>';
}

function renderFills(dash, elId) {
  elId = elId || 'fills-body';
  const rows = dash.fills || [];
  if (!rows.length) { document.getElementById(elId).innerHTML = '<div class="empty">まだ約定履歴がありません。</div>'; return; }
  let h = '<div class="tscroll" style="max-height:430px;overflow-y:auto"><table><tr><th>銘柄</th><th>方向</th><th>種別</th><th>価格</th><th>サイズ</th><th>確定損益</th><th>時刻(JST)</th></tr>';
  for (const f of rows) {
    const cls = (f.closed_pnl_usd < 0) ? 'down' : 'up';
    h += '<tr><td><b>' + f.coin + '</b></td><td>' + (f.side === 'sell' ? '売' : '買') + '</td><td>' + (f.dir || '-') + '</td><td>' + f.px + '</td><td>' + f.sz + '</td>'
      + '<td class="' + cls + '">' + (f.closed_pnl_usd ? usd(f.closed_pnl_usd) : '-') + '</td><td>' + jst(f.time_ms) + '</td></tr>';
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
  if (d.dashboard) { renderKpi(d); renderPositions(d.dashboard); renderFills(d.dashboard); renderDaily(d.dashboard); }
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
    '<div class="strat-adjust" style="margin-top:14px">対象銘柄：</div><div class="strat-chips" style="margin-top:6px">' + uni + '</div>';
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
else if (VIEW === 'fx') { setBadgeFromDashboard(); initFx(); }
else if (VIEW === 'chat') { setBadgeFromDashboard(); initChat(); }
else if (VIEW === 'settings') { setBadgeFromDashboard(); initSettings(); }

async function setBadgeFromDashboard() { const d = await api('dashboard'); if (!d.__error) setBadge(d); }
</script>
<?php endif; ?>
</body>
</html>
