<?php /* ペイウォールUI: 未購入者に価格と解錠手段(PayPal 200円 / URLAI 20,000)を表示する。
         page.php から include。lib.php 読込済み前提。 */ ?>
<div class="paywall-box" id="paywall-box" data-page="<?php echo htmlspecialchars($page->key(), ENT_QUOTES); ?>">
	<div class="pw-fade"></div>
	<div class="pw-inner">
		<p class="pw-lead"><b>ここから先は有料パートです</b><br>続きは、どちらかの方法でお読みいただけます（買い切り・この記事をずっと読めます）。</p>

		<div class="pw-methods">
			<div class="pw-method">
				<h4>💳 200円（PayPal）</h4>
				<div id="paypal-button-container"></div>
				<p class="pw-note">決済完了で自動的に続きが表示されます。</p>
			</div>
			<div class="pw-method">
				<h4>🪙 20,000 URLAI（トークン）</h4>
				<p class="pw-note">Baseチェーンで下記アドレスへ <b>20,000 URLAI</b> を送金し、送金元ウォレットを入力して「支払い確認」を押してください。</p>
				<p class="pw-addr"><code><?php echo PW_URLAI_RECEIVER; ?></code></p>
				<input type="text" id="pw-wallet" placeholder="送金元ウォレット (0x…)" autocomplete="off">
				<button class="pw-btn" id="pw-verify-urlai">支払い確認</button>
			</div>
		</div>

		<details class="pw-restore">
			<summary>購入済みの方はこちら（別端末・Cookie消去後の再表示）</summary>
			<p class="pw-note">PayPalで購入した方: 決済時のメールアドレスと、領収メールに記載のPayPal注文IDを入力。<br>URLAIで購入した方: 送金元ウォレットアドレスのみでOK。</p>
			<input type="text" id="pw-restore-id" placeholder="メールアドレス または 0x…ウォレット" autocomplete="off">
			<input type="text" id="pw-restore-order" placeholder="PayPal注文ID（メール購入時のみ）" autocomplete="off">
			<button class="pw-btn" id="pw-restore-btn">再表示する</button>
		</details>
		<div class="pw-msg" id="pw-msg"></div>
	</div>
</div>

<style>
.paywall-box { position: relative; margin-top: -40px; }
.pw-fade { height: 90px; background: linear-gradient(rgba(255,255,255,0), #fff); }
.pw-inner { border: 2px solid #19484e22; border-radius: 14px; padding: 22px; background: #f8fbfb; }
.pw-lead { margin: 0 0 16px; }
.pw-methods { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 640px) { .pw-methods { grid-template-columns: 1fr; } }
.pw-method { background: #fff; border: 1px solid #dde7e9; border-radius: 10px; padding: 14px; }
.pw-method h4 { margin: 0 0 10px; }
.pw-note { font-size: 12px; color: #5b6b70; line-height: 1.7; }
.pw-addr code { font-size: 11px; word-break: break-all; background: #eef4f5; padding: 4px 6px; border-radius: 6px; display: block; }
.paywall-box input { width: 100%; box-sizing: border-box; padding: 8px 10px; margin: 6px 0; border: 1px solid #cdd9dc; border-radius: 8px; font-size: 13px; }
.pw-btn { padding: 8px 16px; border: 0; border-radius: 8px; background: #19484e; color: #fff; font-weight: 700; cursor: pointer; }
.pw-btn:hover { opacity: .9; }
.pw-restore { margin-top: 16px; font-size: 13px; }
.pw-restore summary { cursor: pointer; color: #19484e; }
.pw-msg { margin-top: 10px; font-size: 13px; font-weight: 700; }
.pw-msg.ok { color: #0a7f4b; } .pw-msg.ng { color: #c0392b; }
</style>

<script src="https://www.paypal.com/sdk/js?client-id=<?php echo PW_PAYPAL_CLIENT_ID; ?>&currency=JPY"></script>
<script>
(function () {
	var page = document.getElementById('paywall-box').getAttribute('data-page');
	var msg = document.getElementById('pw-msg');
	function say(t, ok) { msg.textContent = t; msg.className = 'pw-msg ' + (ok ? 'ok' : 'ng'); }
	function api(body) {
		return fetch('/blog/paywall/paywall.php', {
			method: 'POST', headers: {'Content-Type': 'application/json'},
			body: JSON.stringify(Object.assign({page: page}, body))
		}).then(function (r) { return r.json(); });
	}
	function unlocked() { say('ありがとうございます！続きを表示します…', true); setTimeout(function () { location.reload(); }, 800); }

	if (window.paypal && paypal.Buttons) {
		paypal.Buttons({
			style: {layout: 'horizontal', height: 40, tagline: false},
			createOrder: function (data, actions) {
				return actions.order.create({purchase_units: [{
					description: 'Kurageブログ有料記事: ' + page,
					amount: {currency_code: 'JPY', value: '<?php echo PW_PRICE_JPY; ?>'}
				}]});
			},
			onApprove: function (data, actions) {
				return actions.order.capture().then(function (d) {
					var email = (d.payer && d.payer.email_address) || '';
					return api({action: 'record_paypal', order_id: d.id, email: email}).then(function (r) {
						if (r.ok) { unlocked(); } else { say(r.error || '記録に失敗しました。お問い合わせください', false); }
					});
				});
			},
			onError: function () { say('PayPal決済でエラーが発生しました。時間をおいて再試行してください', false); }
		}).render('#paypal-button-container');
	}

	document.getElementById('pw-verify-urlai').onclick = function () {
		var w = document.getElementById('pw-wallet').value.trim();
		if (!w) { say('送金元ウォレットアドレスを入力してください', false); return; }
		say('オンチェーンで確認中…（数秒かかります）', true);
		api({action: 'verify_urlai', wallet: w}).then(function (r) {
			if (r.ok) { unlocked(); } else { say(r.error || '確認できませんでした', false); }
		});
	};

	document.getElementById('pw-restore-btn').onclick = function () {
		var id = document.getElementById('pw-restore-id').value.trim();
		var order = document.getElementById('pw-restore-order').value.trim();
		if (!id) { say('メールアドレスまたはウォレットを入力してください', false); return; }
		api({action: 'restore', identifier: id, order_id: order}).then(function (r) {
			if (r.ok) { unlocked(); } else { say(r.error || '再表示できませんでした', false); }
		});
	};
})();
</script>
