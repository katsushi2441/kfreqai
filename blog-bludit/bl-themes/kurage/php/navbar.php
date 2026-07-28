<header>
	<a class="hbrand" href="<?php echo Theme::siteUrl(); ?>">
		<span class="kurage-avatar-stage kurage-avatar-mini" role="img" aria-label="Kurage"><img class="kurage-avatar-still" src="<?php echo DOMAIN_THEME; ?>img/kurage_avatar_face.webp" alt=""></span>
		<span>Kurage<sub>暗号資産 AI 自動取引日記</sub></span>
	</a>
	<div style="display:flex;gap:10px">
		<a class="btn btn-ghost" href="https://kurage.exbridge.jp/kfreqai.php?view=summary">取引ダッシュボード</a>
		<a class="btn btn-primary" href="<?php echo Theme::siteUrl(); ?>">記事一覧</a>
	</div>
</header>
<?php
	$currentCategory = '';
	if ($WHERE_AM_I === 'category') {
		$currentCategory = $url->slug();
	} elseif ($WHERE_AM_I === 'page') {
		$currentCategory = $page->categoryKey();
	}
?>
<nav class="product-switcher" aria-label="記事カテゴリを選択">
	<div class="product-switcher-inner">
		<div class="product-family">
			<span class="family-label"><b>TRADE</b> 運用レポート</span>
			<div class="family-links">
				<a class="product-link<?php echo $currentCategory === 'kfreqai' ? ' is-active' : ''; ?>" href="<?php echo Theme::siteUrl(); ?>category/kfreqai">
					<span class="product-mark">KF</span><span><b>kfreqai</b><small>暗号資産 AI自動取引</small></span><i>→</i>
				</a>
				<a class="product-link<?php echo $currentCategory === 'kfxai' ? ' is-active' : ''; ?>" href="<?php echo Theme::siteUrl(); ?>category/kfxai">
					<span class="product-mark">KX</span><span><b>kfxai</b><small>FX AI自動取引</small></span><i>→</i>
				</a>
				<a class="product-link<?php echo $currentCategory === 'kfreqaihl' ? ' is-active' : ''; ?>" href="<?php echo Theme::siteUrl(); ?>category/kfreqaihl">
					<span class="product-mark">KH</span><span><b>kfreqaihl</b><small>Hyperliquid AI自動取引</small></span><i>→</i>
				</a>
			</div>
		</div>
		<div class="product-family">
			<span class="family-label"><b>AI BRAIN</b> 市場インテリジェンス</span>
			<div class="family-links">
				<a class="product-link<?php echo $currentCategory === 'kcbrain' ? ' is-active' : ''; ?>" href="<?php echo Theme::siteUrl(); ?>category/kcbrain">
					<span class="product-mark brain">CB</span><span><b>kcbrain</b><small>暗号資産 AI判断</small></span><i>→</i>
				</a>
				<a class="product-link<?php echo $currentCategory === 'kfxbrain' ? ' is-active' : ''; ?>" href="<?php echo Theme::siteUrl(); ?>category/kfxbrain">
					<span class="product-mark brain">FB</span><span><b>kfxbrain</b><small>FX AI判断</small></span><i>→</i>
				</a>
			</div>
		</div>
		<div class="product-family">
			<span class="family-label"><b>INTEL</b> 世界情勢・パブリッシング</span>
			<div class="family-links">
				<a class="product-link<?php echo $currentCategory === 'crucix' ? ' is-active' : ''; ?>" href="<?php echo Theme::siteUrl(); ?>category/crucix">
					<span class="product-mark brain">CX</span><span><b>crucix</b><small>OSINT世界情勢ブリーフ</small></span><i>→</i>
				</a>
				<a class="product-link<?php echo $currentCategory === 'url2pub' ? ' is-active' : ''; ?>" href="<?php echo Theme::siteUrl(); ?>category/url2pub">
					<span class="product-mark brain">UP</span><span><b>url2pub</b><small>URL2AI パブリッシャー</small></span><i>→</i>
				</a>
			</div>
		</div>
	</div>
</nav>
