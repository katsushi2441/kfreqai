<?php

class pluginKurageComments extends Plugin
{
	const MAX_NAME_LENGTH = 40;
	const MAX_BODY_LENGTH = 1000;
	const MAX_LINKS = 2;
	const MIN_SUBMIT_SECONDS = 2;
	const TOKEN_LIFETIME_SECONDS = 7200;
	const RATE_WINDOW_SECONDS = 30;
	const DAILY_LIMIT = 30;

	public function init()
	{
		$this->dbFields = array(
			'enablePages' => true,
			'secret' => ''
		);
	}

	public function form()
	{
		return '<div class="alert alert-primary" role="alert">'
			. $this->description()
			. '</div><p>公開記事の末尾にコメント欄を表示します。</p>';
	}

	public function beforeSiteLoad()
	{
		if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
			return false;
		}
		if (($_POST['kurage_comment_action'] ?? '') !== 'submit') {
			return false;
		}

		global $page;
		global $WHERE_AM_I;
		if ($WHERE_AM_I !== 'page' || !$this->commentable($page)) {
			$this->redirect(DOMAIN_BASE, 'unavailable');
		}

		$pageId = (string) $page->uuid();
		$postedPageId = (string) ($_POST['page_id'] ?? '');
		$token = (string) ($_POST['comment_token'] ?? '');
		if (!hash_equals($pageId, $postedPageId) || !$this->validToken($pageId, $token)) {
			$this->redirect($page->permalink(), 'expired');
		}

		// Bots commonly fill fields hidden from human visitors.
		if (trim((string) ($_POST['website'] ?? '')) !== '') {
			$this->redirect($page->permalink(), 'ok');
		}

		$name = $this->plainText((string) ($_POST['comment_name'] ?? ''));
		$body = $this->plainText((string) ($_POST['comment_body'] ?? ''), true);
		if ($name === '') {
			$name = '匿名';
		}

		if ($this->textLength($name) > self::MAX_NAME_LENGTH) {
			$this->redirect($page->permalink(), 'name_too_long');
		}
		$bodyLength = $this->textLength($body);
		if ($bodyLength < 2 || $bodyLength > self::MAX_BODY_LENGTH) {
			$this->redirect($page->permalink(), 'body_length');
		}
		if ($this->linkCount($body) > self::MAX_LINKS) {
			$this->redirect($page->permalink(), 'links');
		}

		if (!$this->reserveRateSlot()) {
			$this->redirect($page->permalink(), 'rate');
		}

		$comment = array(
			'id' => bin2hex(random_bytes(8)),
			'name' => $name,
			'body' => $body,
			'created_at' => date('c')
		);
		if (!$this->appendComment($pageId, $comment)) {
			$this->redirect($page->permalink(), 'storage');
		}

		$this->redirect($page->permalink(), 'ok');
	}

	public function pageEnd()
	{
		global $page;
		if (!$this->commentable($page)) {
			return false;
		}

		$pageId = (string) $page->uuid();
		$comments = $this->readComments($pageId);
		$count = count($comments);
		$token = $this->createToken($pageId);
		if ($token === '') {
			return false;
		}

		$html = $this->styles();
		$html .= '<section class="kurage-comments" id="comments" aria-labelledby="comments-title">';
		$html .= '<div class="kurage-comments-head"><div><span>COMMENTS</span>';
		$html .= '<h2 id="comments-title">コメント</h2></div>';
		$html .= '<strong>' . $count . '件</strong></div>';
		$html .= $this->flashMessage((string) ($_GET['comment'] ?? ''));

		if ($count > 0) {
			$html .= '<div class="kurage-comment-list">';
			foreach ($comments as $comment) {
				$name = $this->escape((string) ($comment['name'] ?? '匿名'));
				$body = nl2br($this->escape((string) ($comment['body'] ?? '')), false);
				$date = $this->formatDate((string) ($comment['created_at'] ?? ''));
				$html .= '<article class="kurage-comment">';
				$html .= '<div><strong>' . $name . '</strong><time>' . $this->escape($date) . '</time></div>';
				$html .= '<p>' . $body . '</p></article>';
			}
			$html .= '</div>';
		} else {
			$html .= '<p class="kurage-comments-empty">最初のコメントを投稿できます。</p>';
		}

		$html .= '<form class="kurage-comment-form" method="post" action="' . $this->escape($page->permalink()) . '#comments">';
		$html .= '<input type="hidden" name="kurage_comment_action" value="submit">';
		$html .= '<input type="hidden" name="page_id" value="' . $this->escape($pageId) . '">';
		$html .= '<input type="hidden" name="comment_token" value="' . $this->escape($token) . '">';
		$html .= '<div class="kurage-comment-trap" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>';
		$html .= '<label>お名前 <small>任意</small><input name="comment_name" maxlength="' . self::MAX_NAME_LENGTH . '" autocomplete="name" placeholder="匿名"></label>';
		$html .= '<label>コメント<textarea name="comment_body" minlength="2" maxlength="' . self::MAX_BODY_LENGTH . '" rows="4" required placeholder="記事への感想や質問をお寄せください"></textarea></label>';
		$html .= '<div class="kurage-comment-submit"><small>HTMLは使用できません。</small><button type="submit">コメントを投稿</button></div>';
		$html .= '</form></section>';

		return $html;
	}

	private function commentable($page)
	{
		return $this->getValue('enablePages')
			&& is_object($page)
			&& $page->published()
			&& !$page->isStatic();
	}

	private function createToken($pageId)
	{
		$secret = (string) $this->getValue('secret', false);
		if ($secret === '') {
			return '';
		}
		$issued = time();
		$expires = $issued + self::TOKEN_LIFETIME_SECONDS;
		$signature = hash_hmac('sha256', $pageId . '|' . $issued . '|' . $expires, $secret);
		return $issued . '.' . $expires . '.' . $signature;
	}

	private function validToken($pageId, $token)
	{
		$parts = explode('.', $token);
		if (count($parts) !== 3 || !ctype_digit($parts[0]) || !ctype_digit($parts[1])) {
			return false;
		}
		$issued = (int) $parts[0];
		$expires = (int) $parts[1];
		$now = time();
		if ($issued > $now || ($now - $issued) < self::MIN_SUBMIT_SECONDS || $expires < $now) {
			return false;
		}
		$secret = (string) $this->getValue('secret', false);
		$expected = hash_hmac('sha256', $pageId . '|' . $issued . '|' . $expires, $secret);
		return hash_equals($expected, $parts[2]);
	}

	private function plainText($value, $multiline = false)
	{
		$value = html_entity_decode(strip_tags($value), ENT_QUOTES | ENT_HTML5, 'UTF-8');
		$value = str_replace(array("\r\n", "\r"), "\n", $value);
		$value = preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/u', '', $value);
		if (!$multiline) {
			$value = preg_replace('/\s+/u', ' ', $value);
		} else {
			$value = preg_replace('/[ \t]+/u', ' ', $value);
			$value = preg_replace('/\n{3,}/', "\n\n", $value);
		}
		return trim($value);
	}

	private function textLength($value)
	{
		return function_exists('mb_strlen') ? mb_strlen($value, 'UTF-8') : strlen($value);
	}

	private function linkCount($value)
	{
		preg_match_all('~(?:https?://|www\.)~iu', $value, $matches);
		return count($matches[0]);
	}

	private function reserveRateSlot()
	{
		$directory = $this->workspace() . 'rate' . DS;
		if (!$this->ensureDirectory($directory)) {
			return false;
		}
		$secret = (string) $this->getValue('secret', false);
		$address = (string) ($_SERVER['REMOTE_ADDR'] ?? 'unknown');
		$key = hash_hmac('sha256', $address, $secret);
		$filename = $directory . $key . '.json';
		$now = time();
		$dayAgo = $now - 86400;

		$handle = fopen($filename, 'c+');
		if ($handle === false || !flock($handle, LOCK_EX)) {
			if (is_resource($handle)) {
				fclose($handle);
			}
			return false;
		}
		$raw = stream_get_contents($handle);
		$entries = json_decode($raw ?: '[]', true);
		if (!is_array($entries)) {
			$entries = array();
		}
		$entries = array_values(array_filter($entries, function ($timestamp) use ($dayAgo) {
			return is_int($timestamp) && $timestamp >= $dayAgo;
		}));
		$last = empty($entries) ? 0 : max($entries);
		$allowed = ($now - $last) >= self::RATE_WINDOW_SECONDS && count($entries) < self::DAILY_LIMIT;
		if ($allowed) {
			$entries[] = $now;
			rewind($handle);
			ftruncate($handle, 0);
			fwrite($handle, json_encode($entries));
			fflush($handle);
		}
		flock($handle, LOCK_UN);
		fclose($handle);
		return $allowed;
	}

	private function appendComment($pageId, $comment)
	{
		$directory = $this->workspace() . 'comments' . DS;
		if (!$this->ensureDirectory($directory)) {
			return false;
		}
		$filename = $directory . hash('sha256', $pageId) . '.json';
		$handle = fopen($filename, 'c+');
		if ($handle === false || !flock($handle, LOCK_EX)) {
			if (is_resource($handle)) {
				fclose($handle);
			}
			return false;
		}
		$raw = stream_get_contents($handle);
		$comments = json_decode($raw ?: '[]', true);
		if (!is_array($comments)) {
			$comments = array();
		}
		$comments[] = $comment;
		rewind($handle);
		ftruncate($handle, 0);
		$result = fwrite($handle, json_encode($comments, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
		fflush($handle);
		flock($handle, LOCK_UN);
		fclose($handle);
		return $result !== false;
	}

	private function readComments($pageId)
	{
		$filename = $this->workspace() . 'comments' . DS . hash('sha256', $pageId) . '.json';
		if (!is_file($filename)) {
			return array();
		}
		$raw = file_get_contents($filename);
		$comments = json_decode($raw ?: '[]', true);
		return is_array($comments) ? array_values($comments) : array();
	}

	private function ensureDirectory($directory)
	{
		return is_dir($directory) || mkdir($directory, DIR_PERMISSIONS, true);
	}

	private function redirect($url, $status)
	{
		$separator = strpos($url, '?') === false ? '?' : '&';
		header('Location: ' . $url . $separator . 'comment=' . rawurlencode($status) . '#comments', true, 303);
		exit;
	}

	private function flashMessage($status)
	{
		$messages = array(
			'ok' => array('success', 'コメントを投稿しました。'),
			'expired' => array('error', '投稿画面の有効期限が切れました。ページを再読み込みしてください。'),
			'name_too_long' => array('error', 'お名前は40文字以内で入力してください。'),
			'body_length' => array('error', 'コメントは2文字以上1,000文字以内で入力してください。'),
			'links' => array('error', 'コメントに含められるURLは2件までです。'),
			'rate' => array('error', '連続投稿を防ぐため、少し時間をおいてから投稿してください。'),
			'storage' => array('error', 'コメントを保存できませんでした。時間をおいて再度お試しください。'),
			'unavailable' => array('error', 'このページにはコメントを投稿できません。')
		);
		if (!isset($messages[$status])) {
			return '';
		}
		$message = $messages[$status];
		return '<p class="kurage-comment-flash ' . $message[0] . '" role="status">' . $message[1] . '</p>';
	}

	private function formatDate($value)
	{
		$timestamp = strtotime($value);
		return $timestamp === false ? '' : date('Y年n月j日 H:i', $timestamp);
	}

	private function escape($value)
	{
		return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
	}

	private function styles()
	{
		return <<<'CSS'
<style>
.kurage-comments{margin-top:42px;padding-top:30px;border-top:1px solid #dce8ed;color:#17333d}.kurage-comments-head{display:flex;align-items:end;justify-content:space-between;margin-bottom:18px}.kurage-comments-head span{color:#0d8a98;font-size:11px;font-weight:800;letter-spacing:.16em}.kurage-comments-head h2{margin:2px 0 0;font-size:24px}.kurage-comments-head>strong{color:#58727b;font-size:13px}.kurage-comment-list{display:grid;gap:12px;margin-bottom:22px}.kurage-comment{padding:16px 18px;border:1px solid #dce8ed;border-radius:12px;background:#f8fbfc}.kurage-comment>div{display:flex;gap:12px;align-items:baseline}.kurage-comment strong{font-size:14px}.kurage-comment time{color:#71878e;font-size:11px}.kurage-comment p{margin:8px 0 0;font-size:14px;line-height:1.75;overflow-wrap:anywhere}.kurage-comments-empty{margin:0 0 18px;color:#71878e;font-size:13px}.kurage-comment-form{display:grid;gap:13px;padding:18px;border:1px solid #cfe3e7;border-radius:14px;background:#fff}.kurage-comment-form label{display:grid;gap:6px;font-size:13px;font-weight:700}.kurage-comment-form label small{display:inline;color:#71878e;font-weight:400}.kurage-comment-form input,.kurage-comment-form textarea{box-sizing:border-box;width:100%;border:1px solid #bfd5da;border-radius:9px;background:#fbfdfe;padding:10px 12px;color:#17333d;font:inherit;font-weight:400;outline:none}.kurage-comment-form input:focus,.kurage-comment-form textarea:focus{border-color:#0d8a98;box-shadow:0 0 0 3px rgba(13,138,152,.12)}.kurage-comment-form textarea{resize:vertical;line-height:1.65}.kurage-comment-submit{display:flex;align-items:center;justify-content:space-between;gap:12px}.kurage-comment-submit small{color:#71878e;font-size:11px}.kurage-comment-submit button{border:0;border-radius:9px;background:#0d8a98;padding:10px 18px;color:#fff;font-size:13px;font-weight:800;cursor:pointer}.kurage-comment-submit button:hover{background:#08717d}.kurage-comment-flash{padding:11px 13px;border-radius:9px;font-size:13px}.kurage-comment-flash.success{background:#e8f7f2;color:#176a50}.kurage-comment-flash.error{background:#fff1ef;color:#9b3b31}.kurage-comment-trap{position:absolute!important;left:-10000px!important;width:1px!important;height:1px!important;overflow:hidden!important}@media(max-width:600px){.kurage-comments{margin-top:32px}.kurage-comment-submit{align-items:stretch;flex-direction:column}.kurage-comment-submit button{width:100%}}
</style>
CSS;
	}
}
