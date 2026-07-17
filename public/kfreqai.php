<?php
require_once __DIR__ . '/config.php';
require_once __DIR__ . '/auth_common.php';

$auth = url2ai_auth_bootstrap();

// Kurageさん戦略チャット(chat_api.py :18322)のプロキシ。バックエンドはhttpのため
// httpsのこのページから直接は呼べない(mixed content)。同一オリジンで中継する。
if (isset($_GET['api']) && in_array($_GET['api'], array('chat', 'chat_job', 'halt'), true)) {
    $chat_base = defined('KFREQAI_CHAT_API_BASE')
        ? KFREQAI_CHAT_API_BASE : 'http://exbridge.ddns.net:18322';
    header('Content-Type: application/json; charset=utf-8');
    if ($_GET['api'] === 'halt') {
        // 緊急停止トグル。ダッシュボードの管理者セッション必須。
        // バックエンド側でもfreqtrade API資格情報のBasic認証を要求する二重チェック。
        if (empty($auth['is_admin']) || $_SERVER['REQUEST_METHOD'] !== 'POST') {
            http_response_code(403); echo '{"error":"admin only"}'; exit;
        }
        $ch = curl_init(rtrim($chat_base, '/') . '/api/halt');
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, file_get_contents('php://input'));
        curl_setopt($ch, CURLOPT_HTTPHEADER, array(
            'Content-Type: application/json',
            'Authorization: Basic ' . base64_encode(KFREQAI_API_USER . ':' . KFREQAI_API_PASS),
        ));
        curl_setopt($ch, CURLOPT_TIMEOUT, 15);
    } elseif ($_GET['api'] === 'chat' && $_SERVER['REQUEST_METHOD'] === 'POST') {
        $ch = curl_init(rtrim($chat_base, '/') . '/api/chat');
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, file_get_contents('php://input'));
        curl_setopt($ch, CURLOPT_HTTPHEADER, array('Content-Type: application/json'));
        curl_setopt($ch, CURLOPT_TIMEOUT, 120);  // gemma4の応答待ち
    } else {
        $sid = preg_replace('/[^A-Za-z0-9_-]/', '', isset($_GET['sid']) ? $_GET['sid'] : '');
        $jid = preg_replace('/[^a-f0-9]/', '', isset($_GET['jid']) ? $_GET['jid'] : '');
        if ($sid === '' || $jid === '') { http_response_code(422); echo '{"error":"bad params"}'; exit; }
        $ch = curl_init(rtrim($chat_base, '/') . '/api/job/' . $sid . '/' . $jid);
        curl_setopt($ch, CURLOPT_TIMEOUT, 60);
    }
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
    $chat_res = curl_exec($ch);
    $chat_code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($chat_res === false) { http_response_code(502); echo '{"error":"Kurageさんに繋がりませんでした"}'; exit; }
    http_response_code($chat_code ?: 200);
    echo $chat_res;
    exit;
}

function kfreqai_curl($method, $path, $token = null, $body = null) {
    $ch = curl_init(rtrim(KFREQAI_API_BASE, '/') . $path);
    $headers = array('Accept: application/json');
    if ($token !== null) { $headers[] = 'Authorization: Bearer ' . $token; }
    if ($body !== null) { $headers[] = 'Content-Type: application/json'; }
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
    if ($method === 'POST' && $token === null) {
        curl_setopt($ch, CURLOPT_USERPWD, KFREQAI_API_USER . ':' . KFREQAI_API_PASS);
    }
    if ($body !== null) { curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body)); }
    $res = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);
    if ($res === false || $code >= 400) { return array(null, $code, $err); }
    return array(json_decode($res, true), $code, '');
}

function kfreqai_token($force = false) {
    if (!$force && !empty($_SESSION['kfreqai_token']) && !empty($_SESSION['kfreqai_token_exp']) && time() < $_SESSION['kfreqai_token_exp']) {
        return $_SESSION['kfreqai_token'];
    }
    list($data, $code, ) = kfreqai_curl('POST', '/api/v1/token/login');
    if ($code === 200 && !empty($data['access_token'])) {
        $_SESSION['kfreqai_token'] = $data['access_token'];
        // freqtradeのアクセストークンは15分で失効する。キャッシュを失効より長く持つと
        // その間ずっと全APIが401になり画面が空になる(ログインし直すと直る、の正体)。
        $_SESSION['kfreqai_token_exp'] = time() + 10 * 60;
        return $data['access_token'];
    }
    return null;
}

// 401(トークン失効)を検知したら1回だけ再ログインしてリトライするAPI呼び出し
function kfreqai_api($method, $path) {
    $token = kfreqai_token();
    if ($token === null) { return array(null, 0, 'no token'); }
    list($data, $code, $err) = kfreqai_curl($method, $path, $token);
    if ($code === 401) {
        $token = kfreqai_token(true);
        if ($token !== null) {
            list($data, $code, $err) = kfreqai_curl($method, $path, $token);
        }
    }
    return array($data, $code, $err);
}

function kfreqai_latest_blog_posts($limit = 5) {
    $ch = curl_init(rtrim(KFREQAI_BLOG_BASE, '/') . '/api/pages?token=' . urlencode(KFREQAI_BLOG_API_TOKEN));
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 8);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
    $res = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($res === false || $code >= 400) { return array(); }
    $data = json_decode($res, true);
    $pages = isset($data['data']) ? $data['data'] : array();
    return array_slice($pages, 0, $limit);
}

function h($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }
function fmt_num($n, $d = 2) { return number_format((float) $n, $d); }

// 約定履歴(close_date UTC)を日本時間の暦日で日次集計する。
// 当日(JST)は取引ゼロでも必ず行を出す。返り値は/daily互換の {data: [...]} 形式。
function kfreqai_daily_jst($trades, $days) {
    $jst = new DateTimeZone('Asia/Tokyo');
    $byday = array();
    foreach ($trades as $t) {
        if (empty($t['close_date']) || !isset($t['close_profit_abs'])) { continue; }
        try {
            $d = new DateTime($t['close_date'], new DateTimeZone('UTC'));
            $d->setTimezone($jst);
            $key = $d->format('Y-m-d');
        } catch (Exception $e) { continue; }
        if (!isset($byday[$key])) { $byday[$key] = array('abs_profit' => 0.0, 'trade_count' => 0); }
        $byday[$key]['abs_profit'] += (float) $t['close_profit_abs'];
        $byday[$key]['trade_count'] += 1;
    }
    $rows = array();
    $cursor = new DateTime('now', $jst);
    for ($i = 0; $i < $days; $i++) {
        $key = $cursor->format('Y-m-d');
        $rows[] = array(
            'date' => $key . ($i === 0 ? ' (今日)' : ''),
            'abs_profit' => isset($byday[$key]) ? $byday[$key]['abs_profit'] : 0.0,
            'trade_count' => isset($byday[$key]) ? $byday[$key]['trade_count'] : 0,
        );
        $cursor->modify('-1 day');
    }
    return array('data' => $rows);
}

// freqtrade APIの日時(UTC)を日本時間表記に変換する
function fmt_jst($s) {
    if (!$s) { return '-'; }
    try {
        $d = new DateTime($s, new DateTimeZone('UTC'));
        $d->setTimezone(new DateTimeZone('Asia/Tokyo'));
        return $d->format('m-d H:i');
    } catch (Exception $e) {
        return h($s);
    }
}

$view = 'summary';
if (isset($_GET['view']) && $_GET['view'] === 'native') { $view = 'native'; }
if (isset($_GET['view']) && $_GET['view'] === 'pair') { $view = 'pair'; }
if (isset($_GET['view']) && $_GET['view'] === 'chat') { $view = 'chat'; }
if (isset($_GET['view']) && $_GET['view'] === 'arena') { $view = 'arena'; }
if ($view === 'native' && !$auth['is_admin']) { $view = 'summary'; }

// ペア詳細ビューの対象ペア(不正な形式なら概要へフォールバック)
$pv_pair = '';
if ($view === 'pair') {
    $pv_pair = strtoupper(trim(isset($_GET['pair']) ? $_GET['pair'] : ''));
    if (!preg_match('#^[A-Z0-9]{1,20}/[A-Z]{3,6}$#', $pv_pair)) { $view = 'summary'; $pv_pair = ''; }
}

$page_error = '';
$status = array();
$balance = array();
$profit = array();
$trades = array();
$daily = array();
$show_config = array();
$signals = array();
$blog_posts = array();

// 稼働状況のAJAXエンドポイント: 20銘柄ずつ返す(初期表示を速くするため、
// 160銘柄分のpair_candles取得はページ描画時ではなくスクロール時に分割実行する)
if (isset($_GET['ajax']) && $_GET['ajax'] === 'signals') {
    header('Content-Type: application/json; charset=utf-8');
    $token = kfreqai_token();
    if ($token === null) {
        echo json_encode(array('error' => 'api unavailable'));
        exit;
    }
    list($wl, ) = kfreqai_api('GET', '/api/v1/whitelist');
    $pairs = isset($wl['whitelist']) ? $wl['whitelist'] : array();
    sort($pairs, SORT_STRING);
    $offset = max(0, (int) (isset($_GET['offset']) ? $_GET['offset'] : 0));
    $limit = 20;
    list($sc, ) = kfreqai_api('GET', '/api/v1/show_config');
    $timeframe = isset($sc['timeframe']) ? $sc['timeframe'] : '5m';
    $rows = array();
    foreach (array_slice($pairs, $offset, $limit) as $pair) {
        list($cd, ) = kfreqai_api(
            'GET',
            '/api/v1/pair_candles?pair=' . urlencode($pair) . '&timeframe=' . urlencode($timeframe) . '&limit=1'
        );
        $row = array('pair' => $pair, 'date' => null, 'close' => null, 'pred' => null, 'do_predict' => null);
        if (is_array($cd) && !empty($cd['columns']) && !empty($cd['data'])) {
            $rec = array_combine($cd['columns'], end($cd['data']));
            $row['date'] = isset($rec['date']) ? $rec['date'] : null;
            $row['close'] = isset($rec['close']) ? $rec['close'] : null;
            $row['pred'] = isset($rec['&-s_close']) ? $rec['&-s_close'] : null;
            $row['do_predict'] = isset($rec['do_predict']) ? $rec['do_predict'] : null;
        }
        $rows[] = $row;
    }
    echo json_encode(array('total' => count($pairs), 'offset' => $offset,
                           'rows' => $rows, 'has_more' => $offset + $limit < count($pairs)));
    exit;
}

if ($view === 'summary') {
    $blog_posts = kfreqai_latest_blog_posts(5);
    $token = kfreqai_token();
    if ($token === null) {
        $page_error = 'freqtrade APIに接続できませんでした（起動中か、認証情報の不一致の可能性があります）。';
    } else {
        list($status, ) = kfreqai_api('GET', '/api/v1/status');
        list($balance, ) = kfreqai_api('GET', '/api/v1/balance');
        list($profit, ) = kfreqai_api('GET', '/api/v1/profit');
        // 約定履歴は直近500件を1回で取得し、表には50件・日次集計には全件を使う
        // (以前はlimit=10で実質1日分しか表示されなかった)
        list($tr_daily, ) = kfreqai_api('GET', '/api/v1/trades?limit=500&order_by_id=false');
        $all_trades = isset($tr_daily['trades']) ? $tr_daily['trades'] : array();
        $trades = array_slice($all_trades, 0, 50);
        // 日次損益はfreqtradeの/dailyがUTC日付単位のため使わず、約定履歴から
        // 日本時間の暦日で集計し直す(当日分もリアルタイムに出す)
        $daily = kfreqai_daily_jst($all_trades, 7);
        list($show_config, ) = kfreqai_api('GET', '/api/v1/show_config');
    }
}

// ペア詳細ビュー: 現状(価格/AI予測/保有/出禁)・取引履歴・収支を1ペア分集める
$pv = array();
if ($view === 'pair') {
    $token = kfreqai_token();
    if ($token === null) {
        $page_error = 'freqtrade APIに接続できませんでした（起動中か、認証情報の不一致の可能性があります）。';
    } else {
        $pv = array('position' => null, 'locks' => array(), 'trades' => array(),
                    'candles' => array(), 'last' => array(), 'range' => null, 'stake' => null);
        list($st, ) = kfreqai_api('GET', '/api/v1/status');
        foreach ((is_array($st) ? $st : array()) as $t) {
            if (isset($t['pair']) && $t['pair'] === $pv_pair) { $pv['position'] = $t; break; }
        }
        list($lk, ) = kfreqai_api('GET', '/api/v1/locks');
        foreach ((isset($lk['locks']) ? $lk['locks'] : array()) as $l) {
            if (isset($l['pair']) && $l['pair'] === $pv_pair && !empty($l['active'])) { $pv['locks'][] = $l; }
        }
        list($tr, ) = kfreqai_api('GET', '/api/v1/trades?limit=500&order_by_id=false');
        foreach ((isset($tr['trades']) ? $tr['trades'] : array()) as $t) {
            if (isset($t['pair']) && $t['pair'] === $pv_pair) { $pv['trades'][] = $t; }
        }
        list($cd, ) = kfreqai_api('GET', '/api/v1/pair_candles?pair=' . urlencode($pv_pair) . '&timeframe=5m&limit=288');
        if (is_array($cd) && !empty($cd['columns']) && !empty($cd['data'])) {
            $ci = array_flip($cd['columns']);
            $range_sum = 0.0; $range_n = 0;
            foreach ($cd['data'] as $row) {
                $close = isset($ci['close']) ? (float) $row[$ci['close']] : 0.0;
                if ($close <= 0) { continue; }
                $pv['candles'][] = $close;
                if (isset($ci['high'], $ci['low'])) {
                    $range_sum += ((float) $row[$ci['high']] - (float) $row[$ci['low']]) / $close;
                    $range_n += 1;
                }
            }
            $last = array_combine($cd['columns'], end($cd['data']));
            $pv['last'] = array(
                'date' => isset($last['date']) ? $last['date'] : null,
                'close' => isset($last['close']) ? $last['close'] : null,
                'pred' => isset($last['&-s_close']) ? $last['&-s_close'] : null,
                'do_predict' => isset($last['do_predict']) ? $last['do_predict'] : null,
            );
            if ($range_n >= 100) {
                $pv['range'] = $range_sum / $range_n;
                // 本体戦略のボラ連動賭け金と同じ式(基準0.4%、下限25%、上限100%)
                $pv['stake'] = 3000 * max(0.25, min(1.0, 0.004 / max($pv['range'], 1e-9)));
            }
        }
    }
}
$status = is_array($status) ? $status : array();
$daily_entries = isset($daily['data']) ? $daily['data'] : array();
?>
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kurage Freq AI Trade — kurage.exbridge.jp</title>
<meta name="robots" content="noindex, nofollow">
<meta name="description" content="Kurageの暗号資産AI自動取引ボット。FreqAI(LightGBM)とローカルLLMの二重アドバイザリー層で、24時間紙上取引(dry-run)の市場と向き合っています。">
<meta property="og:title" content="Kurage 暗号資産AI自動取引ボット、24時間奮闘中。">
<meta property="og:description" content="FreqAI × LightGBM が市場と向き合う日々の記録。実資金は動かさない紙上取引(dry-run)です。">
<meta property="og:type" content="website">
<meta property="og:url" content="https://kurage.exbridge.jp/kfreqai.php">
<meta property="og:site_name" content="Kurage Freq AI Trade">
<meta property="og:image" content="https://kurage.exbridge.jp/images/kfreqai_ogp.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:type" content="image/png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://kurage.exbridge.jp/images/kfreqai_ogp.png">
<link rel="stylesheet" href="assets/kurage-avatar.css">
<style>
  :root {
    --indigo: #3949ab; --cyan: #00acc1; --bg: #f6f8fb; --card: #ffffff;
    --ink: #1c2536; --muted: #66748f; --border: #e3e8f0;
    --up: #1baf7a; --down: #d6453d;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: linear-gradient(180deg, #eef2fb 0%, var(--bg) 320px);
    color: var(--ink);
  }
  header {
    padding: 28px 20px 18px; max-width: 1080px; margin: 0 auto;
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;
  }
  header .brand { display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 20px; margin: 0; }
  header h1 span { color: var(--indigo); }
  .badge {
    display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
    margin-left: 8px; vertical-align: middle;
  }
  .badge.dry { background: #fff3cd; color: #8a6100; }
  .badge.live { background: #fde2e1; color: #a4201b; }
  .userbar { font-size: 13px; color: var(--muted); }
  .userbar a { color: var(--indigo); text-decoration: none; margin-left: 10px; }
  main { max-width: 1080px; margin: 0 auto; padding: 0 20px 60px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 20px; }
  .card {
    background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px;
  }
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
  .gate a.btn {
    display: inline-block; margin-top: 18px; padding: 10px 22px; border-radius: 999px;
    background: linear-gradient(90deg, var(--indigo), var(--cyan)); color: #fff; text-decoration: none; font-weight: 600;
  }
  .error { background: #fde2e1; color: #a4201b; padding: 12px 16px; border-radius: 10px; font-size: 13px; margin-bottom: 20px; }
  footer { text-align: center; color: var(--muted); font-size: 12px; padding: 30px 20px; }
  .tabs { display: flex; gap: 6px; margin: 0 0 20px; }
  .tabs a {
    padding: 7px 16px; border-radius: 999px; font-size: 13px; text-decoration: none; color: var(--muted);
    border: 1px solid var(--border); background: var(--card);
  }
  .tabs a.active { background: var(--indigo); color: #fff; border-color: var(--indigo); }
  .native-wrap iframe {
    width: 100%; height: 82vh; border: 1px solid var(--border); border-radius: 14px; background: #fff;
  }
  .native-note { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
  .blog-links { list-style: none; padding: 0; margin: 0; background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .blog-links li { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  .blog-links li:last-child { border-bottom: none; }
  .blog-links a { color: var(--indigo); text-decoration: none; font-size: 14px; }
  .blog-links a:hover { text-decoration: underline; }
  .blog-date { font-size: 12px; color: var(--muted); white-space: nowrap; }
  .pairlink { color: inherit; font-weight: 700; text-decoration: none; border-bottom: 1px dashed rgba(102,116,143,.55); }
  .pairlink:hover { border-bottom-style: solid; }
  /* 表はカード幅(=画面幅)で揃え、列が多い表はカード内で横スクロールさせる。
     カードの枠・角丸はスクロール枠側に描く。表本体に描くと、はみ出す表だけ
     カードが画面の右外まで伸びて幅が不揃いに見える(保有中ポジションで発生) */
  .tscroll { overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%;
             border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
  /* 全表とも同じ固定幅720pxに統一(画面が広い場合は全表とも画面幅)。
     表ごとの自然幅で揃えると列数の多い表だけ大きくなるため数値で固定する */
  .tscroll table { width: 720px; min-width: 100%; border: 0; border-radius: 0; table-layout: fixed; }
  .tscroll th, .tscroll td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
</head>
<body>

  <header>
    <div class="brand">
      <span class="kurage-avatar-stage kurage-avatar-mini" role="img" aria-label="Kurage avatar"><span class="kurage-avatar-motion"><span class="kurage-avatar-breath"><img class="kurage-avatar-frame kurage-avatar-frame-0" src="avatar/lipsync/kurage_mouth_0.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-1" src="avatar/lipsync/kurage_mouth_1.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-2" src="avatar/lipsync/kurage_mouth_2.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-3" src="avatar/lipsync/kurage_mouth_3.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-4" src="avatar/lipsync/kurage_mouth_4.png" alt=""></span></span></span>
      <h1><span>Kurage</span> Freq AI Trade
        <?php if (!empty($show_config['dry_run'])): ?>
          <span class="badge dry">DRY-RUN（ペーパートレード）</span>
        <?php elseif (!empty($show_config)): ?>
          <span class="badge live">LIVE</span>
        <?php endif; ?>
      </h1>
    </div>
    <div class="userbar">
      <?php if ($auth['is_admin']): ?>
        @<?php echo h($auth['session_user']); ?>
        <a href="<?php echo h($auth['logout_url']); ?>">ログアウト</a>
      <?php else: ?>
        <a href="<?php echo h($auth['login_url']); ?>">管理者ログイン</a>
      <?php endif; ?>
    </div>
  </header>
  <main>
    <div class="tabs">
      <a href="?view=summary" class="<?php echo $view === 'summary' ? 'active' : ''; ?>">本番（メイン戦略）</a>
      <a href="?view=arena" class="<?php echo $view === 'arena' ? 'active' : ''; ?>">アリーナ（戦略エージェント）</a>
      <a href="?view=chat" class="<?php echo $view === 'chat' ? 'active' : ''; ?>">Kurageさんと戦略会議</a>
      <?php if ($auth['is_admin']): ?>
      <a href="?view=native" class="<?php echo $view === 'native' ? 'active' : ''; ?>">本家FreqUI</a>
      <?php endif; ?>
    </div>

    <?php if ($view === 'chat'): ?>
      <style>
        .chatgrid { display:flex; gap:20px; align-items:flex-start; }
        .chatside { width:220px; flex-shrink:0; text-align:center; padding-top:12px; }
        .chatside .kurage-avatar-stage { --kurage-avatar-size: 180px; }
        .chatside .chatname { margin-top:14px; font-weight:700; color:var(--indigo); }
        .chatside .chatsub { font-size:12px; color:var(--muted); margin-top:4px; line-height:1.6; }
        .chatmain { flex:1; min-width:0; display:flex; flex-direction:column;
          background:var(--card); border:1px solid var(--border); border-radius:12px;
          height:calc(100vh - 220px); min-height:420px; }
        #chatlog { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:10px; }
        .cmsg { max-width:82%; padding:10px 14px; border-radius:14px; line-height:1.7;
          font-size:14px; white-space:pre-wrap; word-break:break-word; }
        .cmsg.user { align-self:flex-end; background:var(--indigo); color:#fff; border-bottom-right-radius:4px; }
        .cmsg.kurage { align-self:flex-start; background:rgba(103,213,232,.12);
          border:1px solid rgba(103,213,232,.35); border-bottom-left-radius:4px; }
        .cmsg.kurage.thinking::after { content:"…"; animation:cblink 1s infinite; }
        @keyframes cblink { 50%{opacity:.2} }
        .ccard { align-self:flex-start; border:1px solid var(--indigo); border-radius:12px;
          padding:12px 16px; font-size:13px; max-width:82%; }
        .ccard table { margin-top:6px; width:auto; border:0; background:transparent; }
        .ccard td { padding:2px 12px 2px 0; border:0; }
        .ccard .dpos { color:#0a8f4d; font-weight:700; } .ccard .dneg { color:#d33; font-weight:700; }
        .ccard .crun { color:#b8860b; }
        #chatform { display:flex; gap:8px; padding:12px; border-top:1px solid var(--border); }
        #chatinp { flex:1; border:1px solid var(--border); border-radius:22px; padding:11px 18px;
          font-size:14px; outline:none; }
        #chatsend { border:none; background:var(--indigo); color:#fff; font-weight:700;
          border-radius:22px; padding:0 22px; font-size:14px; cursor:pointer; }
        #chatsend:disabled { opacity:.5; }
        .chatnote { font-size:11px; color:var(--muted); text-align:center; padding:8px 0 0; }
        @media (max-width:700px){ .chatside{display:none} .chatmain{height:calc(100vh - 180px)} }
      </style>
      <div class="chatgrid">
        <div class="chatside">
          <span class="kurage-avatar-stage" role="img" aria-label="Kurageさん"><span class="kurage-avatar-motion"><span class="kurage-avatar-breath"><img class="kurage-avatar-frame kurage-avatar-frame-0" src="avatar/lipsync/kurage_mouth_0.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-1" src="avatar/lipsync/kurage_mouth_1.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-2" src="avatar/lipsync/kurage_mouth_2.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-3" src="avatar/lipsync/kurage_mouth_3.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-4" src="avatar/lipsync/kurage_mouth_4.png" alt=""></span></span></span>
          <div class="chatname">Kurageさん</div>
          <div class="chatsub">AIトレードbotの相棒<br>戦略のアイデア、聞かせてね</div>
        </div>
        <div class="chatmain">
          <div id="chatlog"></div>
          <form id="chatform">
            <input id="chatinp" placeholder="例: 急騰してる銘柄は追いかけない方がいいと思うんだよね" autocomplete="off">
            <button id="chatsend">送る</button>
          </form>
        </div>
      </div>
      <div class="chatnote">紙上取引(dry-run)の遊び場です。実際のお金は動きません。仮説はバックテスト(過去30日・主要13銘柄)で検証されます。投資助言ではありません。</div>
      <script>
      (function(){
        var log=document.getElementById('chatlog'),form=document.getElementById('chatform'),
            inp=document.getElementById('chatinp'),send=document.getElementById('chatsend');
        var SID=localStorage.getItem('kurage_sid')||
          (localStorage.setItem('kurage_sid','u'+Math.random().toString(36).slice(2,10)),
           localStorage.getItem('kurage_sid'));
        function add(cls,text){var d=document.createElement('div');d.className='cmsg '+cls;
          d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;return d;}
        function fmt(r){return r?r.trades+'回 / '+r.profit_abs.toFixed(1)+' USDT':'-';}
        function addCard(jid){var d=document.createElement('div');d.className='ccard';d.id='ccard-'+jid;
          d.innerHTML='<b>バックテスト実行中…</b><div class="crun">数分かかります。学習が必要なときは10分くらい待ってね</div>';
          log.appendChild(d);log.scrollTop=log.scrollHeight;}
        function poll(jid){
          fetch('?api=chat_job&sid='+encodeURIComponent(SID)+'&jid='+encodeURIComponent(jid))
          .then(function(r){return r.json();}).then(function(j){
            var card=document.getElementById('ccard-'+jid);
            if(j.status==='done'){
              var cls=j.delta_usdt>=0?'dpos':'dneg';
              card.innerHTML='<b>バックテスト結果</b><table>'+
                '<tr><td>いつもの戦略</td><td>'+fmt(j.baseline)+'</td></tr>'+
                '<tr><td>あなたの仮説</td><td>'+fmt(j.result)+'</td></tr>'+
                '<tr><td>差分</td><td class="'+cls+'">'+(j.delta_usdt>=0?'+':'')+j.delta_usdt+' USDT</td></tr></table>';
              if(j.kurage_says)add('kurage',j.kurage_says);
              return;
            }
            if(j.status==='failed'){
              card.innerHTML='<b>バックテスト失敗</b><div class="dneg">'+(j.error||'')+'</div>';
              if(j.kurage_says)add('kurage',j.kurage_says);
              return;
            }
            setTimeout(function(){poll(jid);},10000);
          }).catch(function(){setTimeout(function(){poll(jid);},10000);});
        }
        form.addEventListener('submit',function(ev){
          ev.preventDefault();
          var text=inp.value.trim();
          if(!text)return;
          inp.value='';send.disabled=true;
          add('user',text);
          var th=add('kurage thinking','');
          fetch('?api=chat',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({session_id:SID,message:text})})
          .then(function(r){return r.json();}).then(function(j){
            th.classList.remove('thinking');
            th.textContent=j.reply||'(返事に失敗しちゃった、もう一回話しかけて)';
            if(j.job_id){addCard(j.job_id);poll(j.job_id);}
          }).catch(function(){
            th.classList.remove('thinking');
            th.textContent='(通信エラーみたい。もう一回試してみて)';
          }).then(function(){send.disabled=false;inp.focus();});
        });
        add('kurage','こんにちは、Kurageさんです🪼 わたしのbotの戦略、一緒に考えてくれるの?「こういうときは買わない方がいいんじゃない?」みたいな思いつきで大丈夫。試したくなったらバックテストで確かめてくるね。');
        inp.focus();
      })();
      </script>

    <?php elseif ($view === 'native'): ?>
      <div class="native-wrap">
        <iframe src="<?php echo h(KFREQAI_UI_URL); ?>" title="FreqUI" allow="clipboard-write"></iframe>
      </div>
    <?php elseif ($view === 'arena'): ?>
      <?php
        // 戦略エージェントアリーナ(dry-run)。advisory-state応答に同梱されたarenaを使う
        // (nginx 18314は完全一致プロキシのため独立パスを増やさない設計)。
        $arena = null;
        $ch = curl_init(KFREQAI_ADVISORY_API_BASE);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 12);
        curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
        $arena_res = curl_exec($ch);
        $arena_code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if ($arena_res !== false && $arena_code === 200) {
            $adv_all = json_decode($arena_res, true);
            $arena = is_array($adv_all) && isset($adv_all['arena']) ? $adv_all['arena'] : null;
        }
        $usd = function ($v) { return ($v < 0 ? '-' : '') . '$' . number_format(abs((float)$v), 2); };
        $pcls = function ($v) { return $v > 0 ? 'up' : ($v < 0 ? 'down' : ''); };
      ?>
      <section>
        <h2>戦略エージェントアリーナ（dry-run・各: 枠<?php echo (int)($arena['slots'] ?? 3); ?>・予算$<?php echo number_format((float)($arena['budget_usdt'] ?? 2000)); ?>・DD<?php echo (int)($arena['dd_suspend_pct'] ?? 10); ?>%で停止扱い）</h2>
        <p style="font-size:13px;color:var(--muted);line-height:1.8">
          複数の戦略エージェントが独自の予算と枠で並走する検証の場。①baseline=本番と同じ戦略（比較の基準）、
          ②giveback=nofx由来のピーク割れクローズ、③rebalsession=低勝率時間帯veto（チャレンジ枠）。
          アリーナ実績とバックテストの両方が良い戦略は本番（メイン）へ昇格候補。ペアは3体共通の40銘柄で公平比較。
        </p>
        <?php if (!is_array($arena) || empty($arena['agents'])): ?>
          <div class="empty">アリーナ情報を取得できませんでした。</div>
        <?php else: ?>
        <div style="overflow-x:auto"><table>
          <thead><tr><th>エージェント</th><th>戦略</th><th>状態</th><th>残高</th><th>収益率</th><th>本日</th><th>決済数</th><th>勝率</th><th>累計損益</th><th>含み</th><th>枠(使用/上限)</th></tr></thead>
          <tbody>
          <?php foreach ($arena['agents'] as $a): ?>
            <tr>
              <td><b><?php echo h($a['label']); ?></b><div style="font-size:11px;color:var(--muted)"><?php echo h($a['desc']); ?></div></td>
              <td style="font-size:12px"><?php echo h($a['strategy']); ?></td>
              <?php if (($a['status'] ?? '') === 'offline'): ?>
                <td class="down">オフライン</td><td colspan="8" style="color:var(--muted);font-size:12px">エージェントに接続できません</td>
              <?php else: ?>
                <td class="<?php echo $a['status'] === 'suspended' ? 'down' : 'up'; ?>"><?php echo $a['status'] === 'suspended' ? '停止(DD超過)' : '稼働中'; ?></td>
                <td><?php echo $usd($a['equity_usdt']); ?></td>
                <td class="<?php echo $pcls($a['return_pct']); ?>"><?php echo number_format((float)$a['return_pct'], 2); ?>%</td>
                <td class="<?php echo $pcls($a['today_pnl_usdt']); ?>"><?php echo $usd($a['today_pnl_usdt']); ?></td>
                <td><?php echo (int)$a['trades']; ?></td>
                <td><?php echo $a['win_rate'] === null ? '-' : round($a['win_rate'] * 100) . '%'; ?></td>
                <td class="<?php echo $pcls($a['pnl_usdt']); ?>"><?php echo $usd($a['pnl_usdt']); ?></td>
                <td class="<?php echo $pcls($a['open_profit_usdt']); ?>"><?php echo $usd($a['open_profit_usdt']); ?></td>
                <td class="<?php echo ((int)$a['open_now'] >= (int)$a['max_open_trades']) ? 'down' : ''; ?>"><?php echo (int)$a['open_now']; ?> / <?php echo (int)$a['max_open_trades']; ?></td>
              <?php endif; ?>
            </tr>
          <?php endforeach; ?>
          </tbody>
        </table></div>
        <p style="font-size:12px;color:var(--muted);margin-top:8px">
          更新: <?php echo h($arena['updated_at'] ?? '-'); ?>（ページ再読み込みで最新化）。
          本番タブは現在dry-run、いずれ本資金化予定。アリーナは常時dry-runの検証専用。
        </p>
        <?php endif; ?>
      </section>
    <?php elseif ($page_error !== ''): ?>
      <div class="error"><?php echo h($page_error); ?></div>
    <?php elseif ($view === 'pair'): ?>
      <?php
        $pv_trades = $pv['trades'];
        $pv_closed = array(); $pv_total = 0.0; $pv_wins = 0;
        $pv_reasons = array();
        foreach ($pv_trades as $t) {
            if (!empty($t['is_open'])) { continue; }
            $pv_closed[] = $t;
            $abs = isset($t['close_profit_abs']) ? (float) $t['close_profit_abs'] : 0.0;
            $pv_total += $abs;
            if ($abs > 0) { $pv_wins += 1; }
            $r = isset($t['exit_reason']) ? $t['exit_reason'] : '-';
            if (!isset($pv_reasons[$r])) { $pv_reasons[$r] = array('n' => 0, 'sum' => 0.0); }
            $pv_reasons[$r]['n'] += 1;
            $pv_reasons[$r]['sum'] += $abs;
        }
        $pos = $pv['position'];
        $last = $pv['last'];
      ?>
      <section>
        <h2><?php echo h($pv_pair); ?> の詳細 <a href="?view=summary" style="font-size:13px;font-weight:normal;margin-left:10px">← 概要に戻る</a></h2>
        <div class="grid">
          <div class="card">
            <div class="label">現在値 / AI予測</div>
            <div class="value" style="font-size:20px"><?php echo isset($last['close']) && $last['close'] !== null ? fmt_num($last['close'], 4) : '-'; ?></div>
            <div class="sub <?php echo (isset($last['pred']) && $last['pred'] !== null && $last['pred'] < 0) ? 'down' : 'up'; ?>">
              予測: <?php echo (isset($last['pred']) && $last['pred'] !== null) ? (($last['pred'] >= 0 ? '+' : '') . fmt_num($last['pred'] * 100) . '%') : '-'; ?>
              / 最終分析 <?php echo fmt_jst(isset($last['date']) ? $last['date'] : ''); ?>
            </div>
          </div>
          <div class="card">
            <div class="label">保有状況</div>
            <?php if ($pos): ?>
            <div class="value <?php echo (isset($pos['profit_ratio']) && $pos['profit_ratio'] < 0) ? 'down' : 'up'; ?>" style="font-size:20px">
              <?php echo isset($pos['profit_ratio']) ? (($pos['profit_ratio'] >= 0 ? '+' : '') . fmt_num($pos['profit_ratio'] * 100) . '%') : '-'; ?>
            </div>
            <div class="sub">建値 <?php echo fmt_num($pos['open_rate'], 4); ?> / 賭け金 <?php echo fmt_num($pos['stake_amount'], 0); ?> USDT / <?php echo fmt_jst($pos['open_date']); ?>〜</div>
            <?php else: ?>
            <div class="value" style="font-size:20px">-</div>
            <div class="sub">現在ポジションなし</div>
            <?php endif; ?>
          </div>
          <div class="card">
            <div class="label">この銘柄の累計収支（直近500約定の範囲）</div>
            <div class="value <?php echo $pv_total < 0 ? 'down' : 'up'; ?>" style="font-size:20px">
              <?php echo ($pv_total >= 0 ? '+' : '') . fmt_num($pv_total); ?> <span style="font-size:13px;color:var(--muted)">USDT</span>
            </div>
            <div class="sub"><?php echo count($pv_closed); ?>回決済 / 勝率 <?php echo count($pv_closed) > 0 ? fmt_num($pv_wins * 100 / count($pv_closed), 1) . '%' : '-'; ?></div>
          </div>
          <div class="card">
            <div class="label">賭け金スケール / 状態</div>
            <div class="value" style="font-size:20px"><?php echo $pv['stake'] !== null ? fmt_num($pv['stake'], 0) . ' USDT' : '-'; ?></div>
            <div class="sub">
              5分足平均レンジ <?php echo $pv['range'] !== null ? fmt_num($pv['range'] * 100) . '%' : '-'; ?>
              <?php if (!empty($pv['locks'])):
                  $lock_end = '';
                  foreach ($pv['locks'] as $l) {
                      if (isset($l['lock_end_time']) && $l['lock_end_time'] > $lock_end) { $lock_end = $l['lock_end_time']; }
                  }
              ?>
                / <span class="down">出禁中(〜<?php echo fmt_jst($lock_end); ?>)</span>
              <?php else: ?>
                / 取引可
              <?php endif; ?>
            </div>
          </div>
        </div>
      </section>

      <?php if (count($pv['candles']) >= 20): ?>
      <section>
        <h2>直近24時間の値動き（5分足終値）</h2>
        <?php
          $cs = $pv['candles'];
          $mn = min($cs); $mx = max($cs); $span = max($mx - $mn, 1e-12);
          $n = count($cs);
          $pts = array();
          foreach ($cs as $i => $c) {
              $x = $n > 1 ? ($i * 600.0 / ($n - 1)) : 0;
              $y = 8 + (1 - ($c - $mn) / $span) * 104;
              $pts[] = round($x, 1) . ',' . round($y, 1);
          }
          $up = end($cs) >= $cs[0];
        ?>
        <div style="background:var(--panel,#fff);border:1px solid rgba(128,128,128,.25);border-radius:8px;padding:10px">
          <svg viewBox="0 0 600 120" style="width:100%;height:120px;display:block" preserveAspectRatio="none">
            <polyline fill="none" stroke="<?php echo $up ? '#12915c' : '#c0392b'; ?>" stroke-width="1.6"
              points="<?php echo implode(' ', $pts); ?>"/>
          </svg>
          <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted)">
            <span>安値 <?php echo fmt_num($mn, 4); ?></span><span>高値 <?php echo fmt_num($mx, 4); ?></span>
          </div>
        </div>
      </section>
      <?php endif; ?>

      <?php if (!empty($pv_reasons)): ?>
      <section>
        <h2>決済理由別の内訳</h2>
        <div class="tscroll">
        <table>
          <tr><th>決済理由</th><th>回数</th><th>合計損益</th></tr>
          <?php foreach ($pv_reasons as $r => $v): ?>
          <tr>
            <td><?php echo h($r); ?></td>
            <td><?php echo (int) $v['n']; ?></td>
            <td class="<?php echo $v['sum'] < 0 ? 'down' : 'up'; ?>"><?php echo ($v['sum'] >= 0 ? '+' : '') . fmt_num($v['sum']); ?> USDT</td>
          </tr>
          <?php endforeach; ?>
        </table>
        </div>
      </section>
      <?php endif; ?>

      <section>
        <h2>取引履歴（<?php echo count($pv_trades); ?>件）</h2>
        <?php if (empty($pv_trades)): ?>
          <div class="empty">直近500約定の範囲にこのペアの取引はありません。</div>
        <?php else: ?>
        <div class="tscroll" style="max-height:520px;overflow-y:auto">
        <table>
          <tr><th>建玉(日本時間)</th><th>クローズ(日本時間)</th><th>建値→決済値</th><th>賭け金</th><th>損益</th><th>決済理由</th></tr>
          <?php foreach ($pv_trades as $t): ?>
          <tr>
            <td><?php echo fmt_jst(isset($t['open_date']) ? $t['open_date'] : ''); ?></td>
            <td><?php echo !empty($t['is_open']) ? '保有中' : fmt_jst(isset($t['close_date']) ? $t['close_date'] : ''); ?></td>
            <td><?php echo fmt_num($t['open_rate'], 4); ?> → <?php echo (!empty($t['is_open']) || !isset($t['close_rate']) || $t['close_rate'] === null) ? '-' : fmt_num($t['close_rate'], 4); ?></td>
            <td><?php echo isset($t['stake_amount']) ? fmt_num($t['stake_amount'], 0) : '-'; ?></td>
            <?php $cp = !empty($t['is_open']) ? (isset($t['profit_ratio']) ? $t['profit_ratio'] : null) : (isset($t['close_profit']) ? $t['close_profit'] : null);
                  $ca = !empty($t['is_open']) ? (isset($t['profit_abs']) ? $t['profit_abs'] : null) : (isset($t['close_profit_abs']) ? $t['close_profit_abs'] : null); ?>
            <td class="<?php echo ($cp !== null && $cp < 0) ? 'down' : 'up'; ?>">
              <div><?php echo $cp !== null ? (($cp >= 0 ? '+' : '') . fmt_num($cp * 100) . '%') : '-'; ?></div>
              <div style="font-size:11.5px;opacity:.75"><?php echo $ca !== null ? (($ca >= 0 ? '+' : '') . fmt_num($ca) . ' USDT') : ''; ?></div>
            </td>
            <td><?php echo !empty($t['is_open']) ? '(保有中)' : h(isset($t['exit_reason']) ? $t['exit_reason'] : '-'); ?></td>
          </tr>
          <?php endforeach; ?>
        </table>
        </div>
        <?php endif; ?>
      </section>
    <?php else: ?>

      <?php
        $advisory = null;
        $ch = curl_init(KFREQAI_ADVISORY_API_BASE);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 8);
        curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
        $adv_res = curl_exec($ch);
        $adv_code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        if ($adv_res !== false && $adv_code === 200) {
            $advisory = json_decode($adv_res, true);
        }
        $adv_directive = is_array($advisory) ? (isset($advisory['directive']) ? $advisory['directive'] : null) : null;
        $adv_regime = is_array($advisory) ? (isset($advisory['regime']) ? $advisory['regime'] : null) : null;
        $is_blocked = is_array($adv_directive) && isset($adv_directive['value']) && $adv_directive['value'] === 'risk_off';
        $directive_label = array('risk_on' => '通常運用', 'risk_off' => 'ブロック中', 'neutral' => '様子見');
        $regime_label = array('bullish' => '強気', 'bearish' => '弱気', 'neutral' => '中立');
        $manual_halt = is_array($advisory) && isset($advisory['manual_halt']) ? $advisory['manual_halt'] : array();
        $halt_active = !empty($manual_halt['active']);
      ?>
      <?php if (is_array($advisory)): ?>
      <?php if ($halt_active): ?>
      <div class="error" style="display:flex;justify-content:space-between;align-items:center;gap:12px">
        <span>🛑 <b>手動の緊急停止が発動中です</b>（<?php echo fmt_jst(isset($manual_halt['updated_at_iso']) ? $manual_halt['updated_at_iso'] : ''); ?>〜）。新規エントリーは全て停止しています。保有中の決済は通常どおり行われます。</span>
        <?php if ($auth['is_admin']): ?><button id="haltbtn" data-active="1" style="border:none;border-radius:8px;padding:8px 16px;font-weight:700;cursor:pointer;background:#0a8f4d;color:#fff;white-space:nowrap">停止を解除する</button><?php endif; ?>
      </div>
      <?php elseif ($auth['is_admin']): ?>
      <div style="display:flex;justify-content:flex-end;margin:0 0 12px">
        <button id="haltbtn" data-active="0" style="border:none;border-radius:8px;padding:8px 16px;font-weight:700;cursor:pointer;background:#c0392b;color:#fff">🛑 新規取引を緊急停止</button>
      </div>
      <?php endif; ?>
      <?php if ($auth['is_admin']): ?>
      <script>
      (function(){
        var b=document.getElementById('haltbtn');
        if(!b)return;
        b.addEventListener('click',function(){
          var toActive=b.getAttribute('data-active')==='0';
          if(!confirm(toActive?'新規エントリーを緊急停止します。よろしいですか?\n(保有中の決済は通常どおり続きます)':'緊急停止を解除して通常運転に戻します。よろしいですか?'))return;
          b.disabled=true;
          fetch('?api=halt',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({active:toActive})})
          .then(function(r){return r.json();})
          .then(function(j){if(j.error){alert(j.error);b.disabled=false;}else{location.reload();}})
          .catch(function(){alert('通信エラー');b.disabled=false;});
        });
      })();
      </script>
      <?php endif; ?>
      <section>
        <h2>地合い・新規エントリー判定</h2>
        <div class="grid">
          <div class="card" style="<?php echo $is_blocked ? 'background:#fde2e1;border-color:#f3b4b0' : ''; ?>">
            <div class="label">新規エントリー</div>
            <div class="value <?php echo $is_blocked ? 'down' : 'up'; ?>" style="font-size:20px">
              <?php echo $is_blocked ? '停止中（risk_off）' : '許可'; ?>
            </div>
            <div class="sub">
              リスク方針: <?php echo h(isset($adv_directive['value']) ? (isset($directive_label[$adv_directive['value']]) ? $directive_label[$adv_directive['value']] : $adv_directive['value']) : '不明'); ?>
              （<?php echo h(isset($adv_directive['note']) ? $adv_directive['note'] : ''); ?>）
              <br>判定: <?php echo fmt_jst(isset($adv_directive['updated_at_iso']) ? $adv_directive['updated_at_iso'] : ''); ?>（<?php echo h(isset($adv_directive['model']) ? $adv_directive['model'] : '-'); ?>、1日3回＋地合い急変時は即時再評価）
            </div>
          </div>
          <div class="card">
            <div class="label">市場全体の地合い</div>
            <div class="value" style="font-size:20px">
              <?php echo h(isset($adv_regime['value']) ? (isset($regime_label[$adv_regime['value']]) ? $regime_label[$adv_regime['value']] : $adv_regime['value']) : '不明'); ?>
            </div>
            <div class="sub">
              <?php echo h(isset($adv_regime['note']) ? $adv_regime['note'] : ''); ?>
              <br>判定: <?php echo fmt_jst(isset($adv_regime['updated_at_iso']) ? $adv_regime['updated_at_iso'] : ''); ?>（毎時更新）
            </div>
          </div>
        </div>
        <p class="native-note">「新規エントリー停止中」は、地合いが崩れているときにAIが新規の買いを一時的に見合わせている状態です。保有中のポジションには影響しません。地合いが回復すると自動で解除されます。</p>
      </section>
      <?php endif; ?>

      <?php
        // 銘柄ニュース(market_facts): advisory-state応答に同梱されている。
        // 収集対象は保有中+直近損失銘柄のみ(6時間ごと)。negative高確度は24hエントリー禁止。
        $mf = is_array($advisory) && isset($advisory['market_facts']) ? $advisory['market_facts'] : null;
        $mf_blocks = is_array($mf) && isset($mf['blocks']) ? $mf['blocks'] : array();
        $mf_facts = is_array($mf) && isset($mf['facts']) ? $mf['facts'] : array();
      ?>
      <?php if (!empty($mf_blocks) || !empty($mf_facts)): ?>
      <section>
        <h2>銘柄ニュース（保有中・監視中の銘柄のみ）</h2>
        <?php if (!empty($mf_blocks)): ?>
        <div class="grid">
          <?php foreach ($mf_blocks as $sym => $sig): ?>
          <div class="card" style="background:#fde2e1;border-color:#f3b4b0">
            <div class="label">⛔ 24時間エントリー禁止</div>
            <div class="value down" style="font-size:20px"><?php echo h($sym); ?></div>
            <div class="sub">
              <?php echo h(isset($sig['event_type']) ? $sig['event_type'] : '-'); ?>（確度<?php echo isset($sig['confidence']) ? fmt_num((float)$sig['confidence'], 1) : '-'; ?>）:
              <?php echo h(isset($sig['title']) ? $sig['title'] : ''); ?>
            </div>
          </div>
          <?php endforeach; ?>
        </div>
        <?php endif; ?>
        <?php if (!empty($mf_facts)): ?>
        <div style="overflow-x:auto">
        <table>
          <tr><th>銘柄</th><th>判定</th><th>見出し</th><th>観測(日本時間)</th></tr>
          <?php foreach (array_slice($mf_facts, 0, 12) as $f): ?>
          <tr>
            <td><b><?php echo h(isset($f['pair']) ? $f['pair'] : '-'); ?></b></td>
            <td>
              <?php $sen = isset($f['sentiment']) ? $f['sentiment'] : ''; ?>
              <span class="<?php echo $sen === 'negative' ? 'down' : ($sen === 'positive' ? 'up' : ''); ?>">
                <?php echo h($sen . '/' . (isset($f['event_type']) ? $f['event_type'] : '-')); ?>
              </span>
            </td>
            <td style="font-size:12.5px"><?php
              $t = isset($f['raw_title']) ? $f['raw_title'] : '';
              $u = isset($f['source_url']) ? $f['source_url'] : '';
              if ($u) { echo '<a href="' . h($u) . '" target="_blank" rel="noopener">' . h(mb_substr($t, 0, 70)) . '</a>'; }
              else { echo h(mb_substr($t, 0, 70)); }
            ?></td>
            <td style="font-size:12px"><?php echo fmt_jst(isset($f['observed_at']) ? $f['observed_at'] . '+0000' : ''); ?></td>
          </tr>
          <?php endforeach; ?>
        </table>
        </div>
        <p class="native-note">保有中の銘柄と直近の損失銘柄について、6時間ごとにニュースを自動収集しています。ハッキング・上場廃止などの悪材料（確度0.6以上）が出た銘柄は、24時間新規エントリーを自動停止します。</p>
        <?php endif; ?>
      </section>
      <?php endif; ?>

      <div class="grid">
        <div class="card">
          <div class="label">Bot</div>
          <div class="value" style="font-size:18px"><?php echo h(isset($show_config['bot_name']) ? $show_config['bot_name'] : '-'); ?></div>
          <div class="sub">戦略: <?php echo h(isset($show_config['strategy']) ? $show_config['strategy'] : '-'); ?> / <?php echo h(isset($show_config['state']) ? $show_config['state'] : '-'); ?></div>
        </div>
        <div class="card">
          <div class="label">残高（推定）</div>
          <div class="value"><?php echo isset($balance['total']) ? fmt_num($balance['total']) : '-'; ?> <span style="font-size:14px;color:var(--muted)">USDT</span></div>
          <div class="sub">Bot管理分: <?php echo isset($balance['total_bot']) ? fmt_num($balance['total_bot']) : '-'; ?> USDT</div>
        </div>
        <div class="card">
          <div class="label">累計損益</div>
          <?php $pt = isset($profit['profit_all_percent']) ? (float) $profit['profit_all_percent'] : null; ?>
          <div class="value <?php echo ($pt !== null && $pt < 0) ? 'down' : 'up'; ?>">
            <?php echo $pt !== null ? ($pt >= 0 ? '+' : '') . fmt_num($pt) . '%' : '-'; ?>
          </div>
          <div class="sub"><?php echo isset($profit['profit_closed_coin']) ? fmt_num($profit['profit_closed_coin']) . ' USDT（確定分）' : ''; ?></div>
        </div>
        <div class="card">
          <div class="label">保有中ポジション</div>
          <div class="value"><?php echo count($status); ?></div>
          <div class="sub">勝率: <?php echo isset($profit['winrate']) ? fmt_num($profit['winrate'] * 100, 1) . '%' : '-'; ?></div>
        </div>
      </div>

      <section>
        <h2>保有中ポジション</h2>
        <?php if (empty($status)): ?>
          <div class="empty">現在保有中のポジションはありません。</div>
        <?php else: ?>
        <div class="tscroll">
        <table>
          <tr><th>ペア</th><th>方向</th><th>金額(USDT)</th><th>平均建値</th><th>現在値</th><th>含み損益</th><th>建玉時刻(日本時間)</th></tr>
          <?php foreach ($status as $t): ?>
          <tr>
            <td><a class="pairlink" href="?view=pair&amp;pair=<?php echo h(rawurlencode($t['pair'])); ?>"><?php echo h($t['pair']); ?></a></td>
            <td><?php echo !empty($t['is_short']) ? 'Short' : 'Long'; ?></td>
            <td>
              <div><b><?php echo isset($t['stake_amount']) ? fmt_num($t['stake_amount'], 0) : '-'; ?></b></div>
              <div style="font-size:11px;opacity:.7"><?php echo fmt_num($t['amount'], 2); ?> 枚</div>
            </td>
            <td><?php echo fmt_num($t['open_rate'], 4); ?></td>
            <td><?php echo isset($t['current_rate']) ? fmt_num($t['current_rate'], 4) : '-'; ?></td>
            <td class="<?php echo (isset($t['profit_ratio']) && $t['profit_ratio'] < 0) ? 'down' : 'up'; ?>">
              <div><?php echo isset($t['profit_ratio']) ? (($t['profit_ratio'] >= 0) ? '+' : '') . fmt_num($t['profit_ratio'] * 100) . '%' : '-'; ?></div>
              <div style="font-size:11.5px;opacity:.75"><?php echo isset($t['profit_abs']) ? (($t['profit_abs'] >= 0) ? '+' : '') . fmt_num($t['profit_abs']) . ' USDT' : ''; ?></div>
            </td>
            <td><?php echo fmt_jst(isset($t['open_date']) ? $t['open_date'] : ''); ?></td>
          </tr>
          <?php endforeach; ?>
        </table>
        </div>
        <?php endif; ?>
      </section>

      <section>
        <h2>直近の約定履歴（最新50件）</h2>
        <?php if (empty($trades)): ?>
          <div class="empty">まだ約定履歴がありません。</div>
        <?php else: ?>
        <div class="tscroll" style="max-height:430px;overflow-y:auto">
        <table>
          <tr><th>ペア</th><th>建玉時刻(日本時間)</th><th>金額(USDT)</th><th>損益</th><th>決済理由</th><th>クローズ時刻(日本時間)</th></tr>
          <?php foreach ($trades as $t): ?>
          <tr>
            <td><a class="pairlink" href="?view=pair&amp;pair=<?php echo h(rawurlencode($t['pair'])); ?>"><?php echo h($t['pair']); ?></a></td>
            <td><?php echo fmt_jst(isset($t['open_date']) ? $t['open_date'] : ''); ?></td>
            <td class="<?php echo (isset($t['close_profit']) && $t['close_profit'] < 0) ? 'down' : 'up'; ?>">
              <div><?php echo isset($t['close_profit']) ? (($t['close_profit'] >= 0) ? '+' : '') . fmt_num($t['close_profit'] * 100) . '%' : '-'; ?></div>
              <div style="font-size:11.5px;opacity:.75"><?php echo isset($t['close_profit_abs']) ? (($t['close_profit_abs'] >= 0) ? '+' : '') . fmt_num($t['close_profit_abs']) . ' USDT' : ''; ?></div>
            </td>
            <td><?php echo h(isset($t['exit_reason']) ? $t['exit_reason'] : '-'); ?></td>
            <td><?php echo fmt_jst(isset($t['close_date']) ? $t['close_date'] : ''); ?></td>
          </tr>
          <?php endforeach; ?>
        </table>
        </div>
        <?php endif; ?>
      </section>

      <section>
        <h2>日次損益（直近7日・日本時間）</h2>
        <?php if (empty($daily_entries)): ?>
          <div class="empty">データがありません。</div>
        <?php else: ?>
        <table>
          <tr><th>日付</th><th>損益</th><th>約定数</th></tr>
          <?php foreach ($daily_entries as $d): ?>
          <tr>
            <td><?php echo h(isset($d['date']) ? $d['date'] : '-'); ?></td>
            <td class="<?php echo (isset($d['abs_profit']) && $d['abs_profit'] < 0) ? 'down' : 'up'; ?>">
              <?php echo isset($d['abs_profit']) ? (($d['abs_profit'] >= 0) ? '+' : '') . fmt_num($d['abs_profit']) . ' USDT' : '-'; ?>
            </td>
            <td><?php echo isset($d['trade_count']) ? (int) $d['trade_count'] : '-'; ?></td>
          </tr>
          <?php endforeach; ?>
        </table>
        <?php endif; ?>
      </section>

      <section>
        <h2>最新記事（Kurage AI 暗号資産自動取引ブログ）</h2>
        <?php if (empty($blog_posts)): ?>
          <div class="empty">まだ記事がありません。</div>
        <?php else: ?>
        <ul class="blog-links">
          <?php foreach ($blog_posts as $p): ?>
          <li><a href="<?php echo h(isset($p['permalink']) ? $p['permalink'] : '#'); ?>"><?php echo h(isset($p['title']) ? $p['title'] : '(無題)'); ?></a>
            <span class="blog-date"><?php echo h(isset($p['date']) ? $p['date'] : ''); ?></span></li>
          <?php endforeach; ?>
        </ul>
        <p class="native-note"><a href="https://kurage.exbridge.jp/blog/">ブログ一覧を見る →</a></p>
        <?php endif; ?>
      </section>

      <?php
        $growth = null;
        $growth_file = __DIR__ . '/kfreqai-growth.json';
        if (file_exists($growth_file)) {
            $growth = json_decode(file_get_contents($growth_file), true);
        }
      ?>
      <?php if (is_array($growth)): ?>
      <section>
        <h2>ブログ検索成績（Google検索 28日 — 成長ループ計測）</h2>
        <?php if (empty($growth['blog_pages'])): ?>
          <div class="empty">Googleにブログ記事のインデックスが進むと、記事別の検索成績がここに表示されます。（最終計測: <?php echo h($growth['updated_at'] ?? '-'); ?>）</div>
        <?php else: ?>
        <div class="tscroll">
        <table>
          <tr><th>記事</th><th>表示</th><th>クリック</th><th>CTR</th><th>平均順位</th></tr>
          <?php foreach ($growth['blog_pages'] as $g): ?>
          <tr>
            <td><a href="https://kurage.exbridge.jp/blog/<?php echo h($g['slug']); ?>"><?php echo h(mb_substr($g['slug'], 0, 40)); ?></a></td>
            <td><?php echo (int) $g['impressions']; ?></td>
            <td><?php echo (int) $g['clicks']; ?></td>
            <td><?php echo fmt_num($g['ctr'] * 100, 1); ?>%</td>
            <td><?php echo fmt_num($g['position'], 1); ?></td>
          </tr>
          <?php endforeach; ?>
        </table>
        <?php endif; ?>
        <?php if (!empty($growth['next_topics'])): ?>
        <p class="native-note" style="margin-top:10px">次に狙う検索クエリ:
          <?php echo h(implode(' / ', array_map(function ($t) { return $t['query']; }, array_slice($growth['next_topics'], 0, 3)))); ?>
        </p>
        <?php endif; ?>
      </section>
      <?php endif; ?>

      <section>
        <h2>稼働状況（AIの予測 — 取引が0件でも生きて動いているかの確認用）</h2>
        <div class="tscroll">
        <table id="signals-table">
          <tr><th>ペア</th><th>最終分析(日本時間)</th><th>終値</th><th>AI予測（次の数時間の変化率）</th><th>判定</th></tr>
        </table>
        </div>
        <div id="signals-status" class="empty" style="margin-top:10px">読み込み中…</div>
        <script>
        (function() {
          var offset = 0, loading = false, done = false;
          var table = document.getElementById('signals-table');
          var statusEl = document.getElementById('signals-status');
          function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '-' : String(s); return d.innerHTML; }
          function fmt(n, dgt) { return n == null ? '-' : Number(n).toLocaleString('ja-JP', {minimumFractionDigits: dgt, maximumFractionDigits: dgt}); }
          function jst(s) {
            if (!s) return '-';
            var t = String(s).replace(' ', 'T');
            if (!/[+Zz]/.test(t.slice(10))) t += 'Z';  // タイムゾーン無しはUTCとみなす
            var d = new Date(t);
            if (isNaN(d)) return esc(s);
            return d.toLocaleString('ja-JP', {timeZone: 'Asia/Tokyo', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
          }
          function loadMore() {
            if (loading || done) return;
            loading = true;
            statusEl.textContent = '読み込み中…';
            fetch('kfreqai.php?ajax=signals&offset=' + offset)
              .then(function(r) { return r.json(); })
              .then(function(data) {
                if (data.error) { statusEl.textContent = 'データを取得できませんでした。'; done = true; return; }
                (data.rows || []).forEach(function(s) {
                  var tr = document.createElement('tr');
                  var predCls = (s.pred != null && s.pred < 0) ? 'down' : 'up';
                  var predTxt = s.pred != null ? ((s.pred >= 0 ? '+' : '') + fmt(s.pred * 100, 2) + '%') : '-';
                  tr.innerHTML = '<td><a class="pairlink" href="?view=pair&pair=' + encodeURIComponent(s.pair) + '">' + esc(s.pair) + '</a></td>'
                    + '<td>' + jst(s.date) + '</td>'
                    + '<td>' + fmt(s.close, 4) + '</td>'
                    + '<td class="' + predCls + '">' + predTxt + '</td>'
                    + '<td>' + ((s.do_predict != null && Number(s.do_predict) === 1) ? '稼働中' : '対象外') + '</td>';
                  table.appendChild(tr);
                });
                offset += (data.rows || []).length;
                if (!data.has_more) {
                  done = true;
                  statusEl.textContent = '全' + (data.total || offset) + '銘柄を表示済み';
                } else {
                  statusEl.textContent = offset + ' / ' + data.total + ' 銘柄（スクロールで続きを読み込み）';
                }
                loading = false;
                // 画面内にステータス行が見えたままなら続きを自動で読む
                requestAnimationFrame(function() {
                  var rect = statusEl.getBoundingClientRect();
                  if (!done && rect.top < window.innerHeight) loadMore();
                });
              })
              .catch(function() { statusEl.textContent = '読み込みに失敗しました。再読み込みしてください。'; loading = false; });
          }
          if ('IntersectionObserver' in window) {
            new IntersectionObserver(function(entries) {
              if (entries[0].isIntersecting) loadMore();
            }, {rootMargin: '400px'}).observe(statusEl);
          } else {
            loadMore();
            window.addEventListener('scroll', function() {
              var rect = statusEl.getBoundingClientRect();
              if (rect.top < window.innerHeight + 400) loadMore();
            });
          }
        })();
        </script>
      </section>

    <?php endif; ?>
  </main>
  <footer>このダッシュボードは閲覧専用です。売買の発注・停止はサーバー側でのみ行います。</footer>
</body>
</html>
