<!DOCTYPE html>
<html lang="<?php echo Theme::lang() ?>">
<head>
<?php include(THEME_DIR_PHP.'head.php'); ?>
</head>
<body>

	<?php Theme::plugins('siteBodyBegin'); ?>

	<?php include(THEME_DIR_PHP.'navbar.php'); ?>

	<?php if ($WHERE_AM_I == 'home'): ?>
	<section class="hero">
		<div>
			<div class="eyebrow"><span class="dot"></span>AI VTuber Kurageの自動取引レポート</div>
			<h1 class="site-title">暗号資産の<br><em>AI自動取引</em>を、毎日記録。</h1>
			<p class="lead">
				AI VTuberのKurageちゃんが、暗号資産自動取引bot「kfreqai」の市況判断・取引結果を
				1日3回（5時・13時・21時）お届けします。すべてdry-run（紙上取引）による
				シミュレーションで、実際の資金は動いていません。
			</p>
		</div>
		<div class="hero-avatar">
			<span class="kurage-avatar-stage kurage-avatar-editor" role="img" aria-label="Kurage">
				<span class="kurage-avatar-motion"><span class="kurage-avatar-breath">
					<img class="kurage-avatar-frame kurage-avatar-frame-0" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_0.png" alt="">
					<img class="kurage-avatar-frame kurage-avatar-frame-1" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_1.png" alt="">
					<img class="kurage-avatar-frame kurage-avatar-frame-2" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_2.png" alt="">
					<img class="kurage-avatar-frame kurage-avatar-frame-3" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_3.png" alt="">
					<img class="kurage-avatar-frame kurage-avatar-frame-4" src="<?php echo DOMAIN_THEME; ?>img/lipsync/kurage_mouth_4.png" alt="">
				</span></span>
			</span>
			<h2>Kurageが報告します</h2>
			<p>地合い判定・リスク方針・取引結果を、Kurageちゃん目線でわかりやすくまとめます。</p>
		</div>
	</section>
	<?php endif; ?>

	<main class="container<?php echo ($WHERE_AM_I == 'page') ? ' single' : ''; ?>">
		<div class="post-list">
		<?php
			if ($url->notFound()) {
				include(THEME_DIR_PHP.'404.php');
			} elseif ($WHERE_AM_I == 'page') {
				include(THEME_DIR_PHP.'page.php');
			} else {
				include(THEME_DIR_PHP.'home.php');
			}
		?>
		</div>

		<?php if ($WHERE_AM_I != 'page'): ?>
		<aside class="sidebar">
			<?php include(THEME_DIR_PHP.'sidebar.php'); ?>
		</aside>
		<?php endif; ?>
	</main>

	<?php include(THEME_DIR_PHP.'footer.php'); ?>

	<?php Theme::plugins('siteBodyEnd'); ?>

</body>
</html>
