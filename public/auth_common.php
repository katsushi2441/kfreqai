<?php
require_once __DIR__ . '/config.php';

if (!defined('URL2AI_AUTH_SESSION_LIFETIME')) {
    define('URL2AI_AUTH_SESSION_LIFETIME', 60 * 60 * 24 * 365);
}

if (!defined('URL2AI_AUTH_SESSION_NAME')) {
    define('URL2AI_AUTH_SESSION_NAME', 'EXBRIDGESESSID');
}

// セッションファイルの保存先を専用ディレクトリに分ける（2026-08-03）。
//
// 既定では save_path が空で /tmp に保存され、同一hetemlアカウントの全ドメインと
// 混在していた。PHPのGCは「実行中リクエストの gc_maxlifetime」で save_path 全体を
// 走査して消すため、gc_maxlifetime を延ばしていない他アプリ（既定1440秒＝24分）が
// session_start() するたび、こちらの365日セッションまで巻き込まれて削除される。
// 実測ではセッションファイルが39.5時間以上生き残っていなかった（/tmpに22,325件）。
// 新しいシステムを追加するほどGCの発火機会が増え、ログアウトが頻発していた。
if (!defined('URL2AI_AUTH_SESSION_PATH')) {
    // /web/<ドメイン>/ に置かれる前提。Web公開領域の外に置く。
    define('URL2AI_AUTH_SESSION_PATH', dirname(dirname(__DIR__)) . '/exbridge_sessions');
}

// セッションファイル自体の寿命。ログインの持続は下の永続Cookieが担うので、
// ここを365日にしてディレクトリを肥大させる必要はない（実測で全体1.3万件/日）。
if (!defined('URL2AI_AUTH_SESSION_FILE_TTL')) {
    define('URL2AI_AUTH_SESSION_FILE_TTL', 60 * 60 * 24 * 7);
}

// セッションが消えてもログインを維持するための署名付きCookie。
// ログイン状態は $_SESSION にしか無く、ファイルが消えた瞬間にログアウトしていた。
if (!defined('URL2AI_AUTH_PERSIST_COOKIE')) {
    define('URL2AI_AUTH_PERSIST_COOKIE', 'EXBRIDGEAUTH');
}

if (!defined('URL2AI_AUTH_PERSIST_LIFETIME')) {
    define('URL2AI_AUTH_PERSIST_LIFETIME', 60 * 60 * 24 * 365);
}

// 永続Cookieの署名鍵。Web公開領域の外に置く（ドキュメントルート配下だと
// HTTPで読めてしまい、鍵が漏れると任意ユーザーへのなりすましが可能になる）。
if (!defined('URL2AI_AUTH_PERSIST_SECRET_FILE')) {
    define('URL2AI_AUTH_PERSIST_SECRET_FILE', dirname(dirname(__DIR__)) . '/exbridge_auth_secret');
}

function url2ai_auth_cookie_domain() {
    if (defined('AIGM_COOKIE_DOMAIN')) { return AIGM_COOKIE_DOMAIN; }
    $host = parse_url(url2ai_auth_site_base_url(), PHP_URL_HOST);
    return preg_match('/(^|\.)exbridge\.jp$/', (string)$host) ? '.exbridge.jp' : '';
}

function url2ai_auth_admin_user() {
    return defined('AIGM_ADMIN') ? AIGM_ADMIN : 'xb_bittensor';
}

function url2ai_auth_site_base_url() {
    if (defined('AIGM_BASE_URL')) { return AIGM_BASE_URL; }
    $host = isset($_SERVER['HTTP_HOST']) ? $_SERVER['HTTP_HOST'] : 'aiknowledgecms.exbridge.jp';
    return 'https://' . preg_replace('/[^A-Za-z0-9.-]/', '', $host);
}

function url2ai_auth_session_path() {
    // 専用ディレクトリを用意できたときだけ使う。失敗したら既定(/tmp)のまま動かす
    // ——ログインを止めるより、従来どおり短命でも動くほうがまし。
    $dir = URL2AI_AUTH_SESSION_PATH;
    if (!is_dir($dir) && !@mkdir($dir, 0700, true) && !is_dir($dir)) { return ''; }
    return is_writable($dir) ? $dir : '';
}

function url2ai_auth_start_session() {
    if (session_status() !== PHP_SESSION_NONE) { return; }
    $session_lifetime = URL2AI_AUTH_SESSION_LIFETIME;
    session_name(URL2AI_AUTH_SESSION_NAME);
    $session_path = url2ai_auth_session_path();
    if ($session_path !== '') {
        ini_set('session.save_path', $session_path);
        // 自分専用のディレクトリなので、GCは自分のTTLだけで回る。
        ini_set('session.gc_maxlifetime', URL2AI_AUTH_SESSION_FILE_TTL);
    } else {
        ini_set('session.gc_maxlifetime', $session_lifetime);
    }
    ini_set('session.cookie_lifetime', $session_lifetime);
    ini_set('session.cookie_path', '/');
    $cookie_domain = url2ai_auth_cookie_domain();
    if ($cookie_domain !== '') { ini_set('session.cookie_domain', $cookie_domain); }
    ini_set('session.cookie_secure', '1');
    ini_set('session.cookie_httponly', '1');
    ini_set('session.use_strict_mode', '1');
    ini_set('session.use_only_cookies', '1');
    session_cache_expire((int)($session_lifetime / 60));
    session_start();
    url2ai_auth_extend_session_cookie();
}

function url2ai_auth_delete_cookie($name) {
    setcookie($name, '', time() - 3600, '/', url2ai_auth_cookie_domain(), true, true);
    setcookie($name, '', time() - 3600, '/', '', true, true);
}

function url2ai_auth_delete_session_cookie_variants() {
    $name = URL2AI_AUTH_SESSION_NAME;
    $domains = array(
        url2ai_auth_cookie_domain(),
        '',
        'aiknowledgecms.exbridge.jp',
        'kurage.exbridge.jp',
        '.exbridge.jp',
    );
    $seen = array();
    foreach ($domains as $domain) {
        $domain = (string)$domain;
        if (isset($seen[$domain])) { continue; }
        $seen[$domain] = true;
        setcookie($name, '', time() - 3600, '/', $domain, true, true);
        setcookie('PHPSESSID', '', time() - 3600, '/', $domain, true, true);
    }
}

function url2ai_auth_extend_session_cookie() {
    if (session_status() !== PHP_SESSION_ACTIVE) { return; }
    $session_lifetime = URL2AI_AUTH_SESSION_LIFETIME;
    setcookie(session_name(), session_id(), time() + $session_lifetime, '/', url2ai_auth_cookie_domain(), true, true);
}

function url2ai_auth_persist_secret() {
    // 全ドメインで同一の鍵が必要。x_api_keys.sh は aiknowledgecms と kurage にしか
    // 無い（他ドメインはログインをaiknowledgecmsへ委譲していてX鍵を持たない）ので、
    // Web公開領域の外に置いた共有ファイルを正とし、無い場合だけX鍵から導出する。
    static $cache = null;
    if ($cache !== null) { return $cache; }
    $cache = '';
    $file = URL2AI_AUTH_PERSIST_SECRET_FILE;
    if (is_readable($file)) {
        $seed = trim((string)@file_get_contents($file));
        if ($seed !== '') { $cache = hash('sha256', 'exbridge-persist-auth-v1|' . $seed, true); }
    }
    if ($cache === '') {
        $keys = url2ai_auth_load_x_keys();
        $seed = isset($keys['X_API_SECRET']) ? (string)$keys['X_API_SECRET'] : '';
        if ($seed !== '') { $cache = hash('sha256', 'exbridge-persist-auth-v1|' . $seed, true); }
    }
    return $cache;
}

function url2ai_auth_issue_persist_cookie($username) {
    $username = trim((string)$username);
    $secret = url2ai_auth_persist_secret();
    if ($secret === '' || $username === '' || !preg_match('/^[A-Za-z0-9_]{1,50}$/', $username)) {
        return;
    }
    $expires = time() + URL2AI_AUTH_PERSIST_LIFETIME;
    $payload = url2ai_auth_base64url($username . '|' . $expires);
    $sig = url2ai_auth_base64url(hash_hmac('sha256', $payload, $secret, true));
    setcookie(URL2AI_AUTH_PERSIST_COOKIE, $payload . '.' . $sig, $expires, '/',
              url2ai_auth_cookie_domain(), true, true);
}

function url2ai_auth_read_persist_cookie() {
    // 戻り値: 検証済みのユーザー名。無効・期限切れ・改ざんは空文字。
    if (empty($_COOKIE[URL2AI_AUTH_PERSIST_COOKIE])) { return ''; }
    $secret = url2ai_auth_persist_secret();
    if ($secret === '') { return ''; }
    $parts = explode('.', (string)$_COOKIE[URL2AI_AUTH_PERSIST_COOKIE], 2);
    if (count($parts) !== 2) { return ''; }
    list($payload, $sig) = $parts;
    $expected = url2ai_auth_base64url(hash_hmac('sha256', $payload, $secret, true));
    // タイミング攻撃を避けるため hash_equals で比較する。
    if (!hash_equals($expected, $sig)) { return ''; }
    $decoded = url2ai_auth_base64url_decode($payload);
    $fields = explode('|', $decoded, 2);
    if (count($fields) !== 2) { return ''; }
    list($username, $expires) = $fields;
    if ((int)$expires <= time()) { return ''; }
    if (!preg_match('/^[A-Za-z0-9_]{1,50}$/', $username)) { return ''; }
    return $username;
}

function url2ai_auth_restore_from_persist_cookie() {
    // セッションファイルが消えていても、署名付きCookieがあればログインを復元する。
    // Xのアクセストークンまでは復元できないので、X APIを叩く画面は再ログインが要る。
    if (!empty($_SESSION['session_username'])) { return false; }
    $username = url2ai_auth_read_persist_cookie();
    if ($username === '') { return false; }
    $_SESSION['session_username'] = $username;
    $_SESSION['session_logged_in_until'] = time() + URL2AI_AUTH_SESSION_LIFETIME;
    $_SESSION['session_restored_from_cookie'] = true;
    return true;
}

function url2ai_auth_mark_logged_in($username = '') {
    $_SESSION['session_logged_in_until'] = time() + URL2AI_AUTH_SESSION_LIFETIME;
    if ($username !== '') { $_SESSION['session_username'] = $username; }
    url2ai_auth_extend_session_cookie();
    $current = isset($_SESSION['session_username']) ? $_SESSION['session_username'] : '';
    url2ai_auth_issue_persist_cookie($current);
}

function url2ai_auth_current_path() {
    $uri = isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '/';
    return preg_match('#^/[^\r\n]*$#', $uri) ? $uri : '/aiknowledgesns.php';
}

function url2ai_auth_safe_return($return) {
    $return = trim((string)$return);
    if ($return === '') { return '/aiknowledgesns.php'; }
    if (preg_match('#^https?://#i', $return)) {
        $host = parse_url($return, PHP_URL_HOST);
        $base_host = parse_url(url2ai_auth_site_base_url(), PHP_URL_HOST);
        $auth_host = parse_url(url2ai_auth_base_url(), PHP_URL_HOST);
        if ($host === $base_host || $host === $auth_host || preg_match('/(^|\.)exbridge\.jp$/', (string)$host)) {
            $path = parse_url($return, PHP_URL_PATH);
            $query = parse_url($return, PHP_URL_QUERY);
            $scheme = parse_url($return, PHP_URL_SCHEME) ?: 'https';
            return $scheme . '://' . $host . ($path ? $path : '/') . ($query ? '?' . $query : '');
        }
        return '/aiknowledgesns.php';
    }
    if (strpos($return, '/') === 0 && strpos($return, '//') !== 0) { return $return; }
    return '/aiknowledgesns.php';
}

function url2ai_auth_base_url() {
    if (defined('AIGM_AUTH_BASE_URL')) { return AIGM_AUTH_BASE_URL; }
    $site_host = parse_url(url2ai_auth_site_base_url(), PHP_URL_HOST);
    if ($site_host !== 'aiknowledgecms.exbridge.jp') { return 'https://aiknowledgecms.exbridge.jp'; }
    return url2ai_auth_site_base_url();
}

function url2ai_auth_redirect_url($return) {
    $return = url2ai_auth_safe_return($return);
    if (preg_match('#^https?://#i', $return)) { return $return; }
    return url2ai_auth_site_base_url() . $return;
}

function url2ai_auth_return_for_login($return) {
    $return = url2ai_auth_safe_return($return);
    if (!preg_match('#^https?://#i', $return) && parse_url(url2ai_auth_base_url(), PHP_URL_HOST) !== parse_url(url2ai_auth_site_base_url(), PHP_URL_HOST)) {
        return url2ai_auth_site_base_url() . $return;
    }
    return $return;
}

function url2ai_auth_login_url($return = '') {
    $return = $return !== '' ? $return : url2ai_auth_current_path();
    return url2ai_auth_base_url() . '/aiknowledgesns.php?aks_login=1&return=' . urlencode(url2ai_auth_return_for_login($return));
}

function url2ai_auth_logout_url($return = '') {
    $return = $return !== '' ? $return : url2ai_auth_current_path();
    return url2ai_auth_base_url() . '/aiknowledgesns.php?aks_logout=1&return=' . urlencode(url2ai_auth_return_for_login($return));
}

function url2ai_auth_base64url($data) {
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

function url2ai_auth_base64url_decode($data) {
    $data = strtr((string)$data, '-_', '+/');
    $pad = strlen($data) % 4;
    if ($pad) { $data .= str_repeat('=', 4 - $pad); }
    $decoded = base64_decode($data, true);
    return $decoded === false ? '' : $decoded;
}

function url2ai_auth_pack_state($nonce, $return_to) {
    return $nonce . '.' . url2ai_auth_base64url($return_to);
}

function url2ai_auth_state_nonce($state) {
    $parts = explode('.', (string)$state, 2);
    return $parts[0];
}

function url2ai_auth_state_return($state) {
    $parts = explode('.', (string)$state, 2);
    if (count($parts) < 2) { return ''; }
    return url2ai_auth_safe_return(url2ai_auth_base64url_decode($parts[1]));
}

function url2ai_auth_gen_verifier() {
    $bytes = '';
    for ($i = 0; $i < 32; $i++) { $bytes .= chr(mt_rand(0, 255)); }
    return url2ai_auth_base64url($bytes);
}

function url2ai_auth_gen_challenge($verifier) {
    return url2ai_auth_base64url(hash('sha256', $verifier, true));
}

function url2ai_auth_get_json($url, $token) {
    $opts = array('http' => array(
        'method' => 'GET',
        'header' => "Authorization: Bearer $token\r\nUser-Agent: AIKnowledgeSNS/1.0\r\n",
        'timeout' => 12,
        'ignore_errors' => true,
    ));
    $res = @file_get_contents($url, false, stream_context_create($opts));
    return json_decode($res ? $res : '{}', true);
}

function url2ai_auth_handle_login_flow($return_default = '/aiknowledgesns.php') {
    url2ai_auth_start_session();
    if (isset($_GET['aks_reset_session'])) {
        $return_to = isset($_GET['return']) ? url2ai_auth_safe_return($_GET['return']) : $return_default;
        session_unset();
        session_destroy();
        url2ai_auth_delete_session_cookie_variants();
        url2ai_auth_delete_cookie(URL2AI_AUTH_PERSIST_COOKIE);
        header('Location: ' . url2ai_auth_redirect_url($return_to));
        exit;
    }
    if (isset($_GET['aks_logout'])) {
        $return_to = isset($_GET['return']) ? url2ai_auth_safe_return($_GET['return']) : $return_default;
        session_destroy();
        url2ai_auth_delete_cookie(URL2AI_AUTH_SESSION_NAME);
        url2ai_auth_delete_cookie('PHPSESSID');
        // 永続Cookieを消さないとログアウトしても次のリクエストで復活してしまう。
        url2ai_auth_delete_cookie(URL2AI_AUTH_PERSIST_COOKIE);
        header('Location: ' . url2ai_auth_redirect_url($return_to));
        exit;
    }
    if (isset($_GET['aks_login'])) {
        $keys = url2ai_auth_load_x_keys();
        $client_id = isset($keys['X_API_KEY']) ? $keys['X_API_KEY'] : '';
        $verifier = url2ai_auth_gen_verifier();
        $challenge = url2ai_auth_gen_challenge($verifier);
        $state_nonce = md5(uniqid('', true));
        $return_to = isset($_GET['return']) ? url2ai_auth_safe_return($_GET['return']) : url2ai_auth_safe_return($return_default);
        $state = url2ai_auth_pack_state($state_nonce, $return_to);
        $_SESSION['aks_code_verifier'] = $verifier;
        $_SESSION['aks_oauth_state'] = $state_nonce;
        $_SESSION['aks_return_to'] = $return_to;
        $params = array(
            'response_type' => 'code',
            'client_id' => $client_id,
            'redirect_uri' => url2ai_auth_base_url() . '/aiknowledgesns.php',
            'scope' => 'tweet.read users.read offline.access',
            'state' => $state,
            'code_challenge' => $challenge,
            'code_challenge_method' => 'S256',
        );
        header('Location: https://x.com/i/oauth2/authorize?' . http_build_query($params));
        exit;
    }
    if (isset($_GET['code'], $_GET['state'])) {
        $state_return_to = url2ai_auth_state_return($_GET['state']);
        $state_nonce = url2ai_auth_state_nonce($_GET['state']);
        if (isset($_SESSION['aks_oauth_state']) && $state_nonce === $_SESSION['aks_oauth_state']) {
            $keys = url2ai_auth_load_x_keys();
            $client_id = isset($keys['X_API_KEY']) ? $keys['X_API_KEY'] : '';
            $client_secret = isset($keys['X_API_SECRET']) ? $keys['X_API_SECRET'] : '';
            $post = http_build_query(array(
                'grant_type' => 'authorization_code',
                'code' => $_GET['code'],
                'redirect_uri' => url2ai_auth_base_url() . '/aiknowledgesns.php',
                'code_verifier' => isset($_SESSION['aks_code_verifier']) ? $_SESSION['aks_code_verifier'] : '',
                'client_id' => $client_id,
            ));
            $cred = base64_encode($client_id . ':' . $client_secret);
            $data = url2ai_auth_post_form('https://api.twitter.com/2/oauth2/token', $post, array(
                'Content-Type: application/x-www-form-urlencoded',
                'Authorization: Basic ' . $cred,
            ));
            if (isset($data['access_token'])) {
                $_SESSION['session_access_token'] = $data['access_token'];
                $_SESSION['session_token_expires'] = time() + (isset($data['expires_in']) ? (int)$data['expires_in'] : 7200);
                if (!empty($data['refresh_token'])) { $_SESSION['session_refresh_token'] = $data['refresh_token']; }
                $me = url2ai_auth_get_json('https://api.twitter.com/2/users/me', $data['access_token']);
                if (isset($me['data']['username'])) { $_SESSION['session_username'] = $me['data']['username']; }
                url2ai_auth_mark_logged_in(isset($_SESSION['session_username']) ? $_SESSION['session_username'] : '');
            }
            unset($_SESSION['aks_oauth_state'], $_SESSION['aks_code_verifier']);
        }
        $return_to = isset($_SESSION['aks_return_to']) ? $_SESSION['aks_return_to'] : ($state_return_to !== '' ? $state_return_to : $return_default);
        unset($_SESSION['aks_return_to']);
        header('Location: ' . url2ai_auth_redirect_url($return_to));
        exit;
    }
}

function url2ai_auth_load_x_keys() {
    $keys = array();
    $file = __DIR__ . '/x_api_keys.sh';
    if (!file_exists($file)) { return $keys; }
    foreach (file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        if (preg_match('/(?:export\s+)?(\w+)=["\']?([^"\'#\r\n]*)["\']?/', $line, $m)) {
            $keys[trim($m[1])] = trim($m[2]);
        }
    }
    return $keys;
}

function url2ai_auth_post_form($url, $data, $headers) {
    $opts = array('http' => array(
        'method' => 'POST',
        'header' => implode("\r\n", $headers) . "\r\n",
        'content' => $data,
        'timeout' => 12,
        'ignore_errors' => true,
    ));
    $res = @file_get_contents($url, false, stream_context_create($opts));
    return json_decode($res ? $res : '{}', true);
}

function url2ai_auth_refresh_if_needed() {
    if (empty($_SESSION['session_refresh_token']) || empty($_SESSION['session_token_expires']) || time() <= $_SESSION['session_token_expires'] - 300) {
        return;
    }
    $logged_in_until = isset($_SESSION['session_logged_in_until']) ? (int)$_SESSION['session_logged_in_until'] : 0;
    $keys = url2ai_auth_load_x_keys();
    $client_id = isset($keys['X_API_KEY']) ? $keys['X_API_KEY'] : '';
    $client_secret = isset($keys['X_API_SECRET']) ? $keys['X_API_SECRET'] : '';
    if ($client_id === '' || $client_secret === '') { return; }
    $post = http_build_query(array(
        'grant_type' => 'refresh_token',
        'refresh_token' => $_SESSION['session_refresh_token'],
        'client_id' => $client_id,
    ));
    $cred = base64_encode($client_id . ':' . $client_secret);
    $ref = url2ai_auth_post_form('https://api.twitter.com/2/oauth2/token', $post, array(
        'Content-Type: application/x-www-form-urlencoded',
        'Authorization: Basic ' . $cred,
    ));
    if (!empty($ref['access_token'])) {
        $_SESSION['session_access_token'] = $ref['access_token'];
        $_SESSION['session_token_expires'] = time() + (isset($ref['expires_in']) ? (int)$ref['expires_in'] : 7200);
        if (!empty($ref['refresh_token'])) { $_SESSION['session_refresh_token'] = $ref['refresh_token']; }
        url2ai_auth_mark_logged_in(isset($_SESSION['session_username']) ? $_SESSION['session_username'] : '');
    } elseif ($logged_in_until <= time()) {
        unset($_SESSION['session_access_token'], $_SESSION['session_token_expires']);
    } else {
        unset($_SESSION['session_access_token'], $_SESSION['session_token_expires']);
        url2ai_auth_extend_session_cookie();
    }
}

function url2ai_auth_all_cookie_values($name) {
    // $_COOKIEは同名Cookieの最初の1つしか見えないため、生ヘッダから全値を取る。
    $raw = isset($_SERVER['HTTP_COOKIE']) ? $_SERVER['HTTP_COOKIE'] : '';
    $vals = array();
    foreach (explode(';', $raw) as $part) {
        $kv = explode('=', trim($part), 2);
        if (count($kv) === 2 && $kv[0] === $name) { $vals[] = urldecode($kv[1]); }
    }
    return array_values(array_unique($vals));
}

function url2ai_auth_recover_shadowed_session() {
    // 過去にhost-onlyで発行されたEXBRIDGESESSIDの残骸が.exbridge.jp共有Cookieを
    // 覆い隠すと、そのホストだけ未ログインに見える。同名Cookieが複数あるときは
    // 各セッションIDを試し、ログイン済みセッションが見つかればそれに乗り換える。
    $vals = url2ai_auth_all_cookie_values(URL2AI_AUTH_SESSION_NAME);
    if (count($vals) < 2) { return false; }
    $original = session_id();
    foreach ($vals as $sid) {
        if ($sid === $original || !preg_match('/^[A-Za-z0-9,-]{22,128}$/', $sid)) { continue; }
        session_write_close();
        session_id($sid);
        session_start();
        if (!empty($_SESSION['session_username'])) { return true; }
    }
    // どれもログイン済みでなければ元のセッションに戻す
    if (session_id() !== $original) {
        session_write_close();
        session_id($original);
        session_start();
    }
    return false;
}

function url2ai_auth_dedupe_session_cookie() {
    // 勝ったセッションIDを共有ドメインCookieに張り直し、host-only残骸を削除して
    // 次回以降のCookie競合を解消する。
    url2ai_auth_extend_session_cookie();
    setcookie(URL2AI_AUTH_SESSION_NAME, '', time() - 3600, '/', '', true, true);
}

function url2ai_auth_bootstrap() {
    url2ai_auth_start_session();
    url2ai_auth_refresh_if_needed();
    $session_user = isset($_SESSION['session_username']) ? $_SESSION['session_username'] : '';
    $logged_in_until = isset($_SESSION['session_logged_in_until']) ? (int)$_SESSION['session_logged_in_until'] : 0;
    $logged_in = $session_user !== '' && (!empty($_SESSION['session_access_token']) || $logged_in_until > time());
    // セッションファイルが消えていた場合の復旧は、Cookieの取り違え(shadow)より先に試す。
    // 保存先を分離してもファイル消失はゼロにならないため、ここが最後の砦になる。
    if (!$logged_in && url2ai_auth_restore_from_persist_cookie()) {
        $session_user = $_SESSION['session_username'];
        $logged_in_until = (int)$_SESSION['session_logged_in_until'];
        $logged_in = true;
    }
    if (!$logged_in && url2ai_auth_recover_shadowed_session()) {
        url2ai_auth_refresh_if_needed();
        $session_user = isset($_SESSION['session_username']) ? $_SESSION['session_username'] : '';
        $logged_in_until = isset($_SESSION['session_logged_in_until']) ? (int)$_SESSION['session_logged_in_until'] : 0;
        $logged_in = $session_user !== '' && (!empty($_SESSION['session_access_token']) || $logged_in_until > time());
        // shadowを実際に回復したときだけ残骸Cookieを削除する。正常にログイン中のユーザーの
        // Cookieには一切触れない(cookie2個時の無条件dedupeが頻繁ログアウトを誘発したため、
        // recover成功時のみに限定)。
        url2ai_auth_dedupe_session_cookie();
    }
    if ($logged_in) { url2ai_auth_mark_logged_in($session_user); }
    return array(
        'logged_in' => $logged_in,
        'session_user' => $session_user,
        'is_admin' => ($session_user !== '' && $session_user === url2ai_auth_admin_user()),
        'login_url' => url2ai_auth_login_url(),
        'logout_url' => url2ai_auth_logout_url(),
    );
}
