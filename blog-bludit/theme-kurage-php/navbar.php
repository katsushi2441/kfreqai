<header>
	<a class="hbrand" href="<?php echo Theme::siteUrl(); ?>">
		<span class="kurage-avatar-stage kurage-avatar-mini" role="img" aria-label="Kurage">
			<span class="kurage-avatar-motion"><span class="kurage-avatar-breath">
				<img class="kurage-avatar-frame kurage-avatar-frame-0" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_0.png" alt="">
				<img class="kurage-avatar-frame kurage-avatar-frame-1" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_1.png" alt="">
				<img class="kurage-avatar-frame kurage-avatar-frame-2" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_2.png" alt="">
				<img class="kurage-avatar-frame kurage-avatar-frame-3" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_3.png" alt="">
				<img class="kurage-avatar-frame kurage-avatar-frame-4" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_4.png" alt="">
			</span></span>
		</span>
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
	</div>
</nav>
