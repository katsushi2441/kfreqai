<article class="post-single">
	<?php Theme::plugins('pageBegin'); ?>

	<h1><?php echo $page->title(); ?></h1>

	<?php if (!$page->isStatic() && !$url->notFound()): ?>
	<div class="post-meta">
		<span><?php echo $page->date(); ?></span>
		<span>読了目安 <?php echo $page->readingTime(); ?></span>
	</div>

	<div class="presenter-card">
		<span class="kurage-avatar-stage" role="img" aria-label="Kurage"><img class="kurage-avatar-still" src="<?php echo DOMAIN_THEME; ?>img/kurage_avatar_face.webp" alt=""></span>
		<div class="ptxt">
			<b>Kurageちゃんのレポート</b>
			<span>AI自動取引bot「kfreqai」の市況判断・取引結果をお届けします（dry-run運用）</span>
		</div>
	</div>
	<?php endif ?>

	<div class="content">
		<?php
			// ペイウォール: 本文に <!--paywall--> があれば、そこから先は購入者のみ表示。
			// 判定・記録は /blog/paywall/ (lib.php + paywall.php)。マーカー無し記事は従来通り全文表示。
			$pwParts = preg_split('/<!--\s*paywall\s*-->/i', $page->content(), 2);
			if (count($pwParts) === 2) {
				$pwLib = dirname(dirname(dirname(dirname(__FILE__)))) . '/paywall/lib.php';
				$pwUnlocked = false;
				if (file_exists($pwLib)) { require_once $pwLib; $pwUnlocked = pw_is_unlocked($page->key()); }
				if ($pwUnlocked) {
					echo $page->content();
				} else {
					echo $pwParts[0];
					include dirname(__FILE__) . '/paywall-box.php';
				}
			} else {
				echo $page->content();
			}
		?>
	</div>

	<?php $tagsList = $page->tags(true); $categoryKey = $page->categoryKey(); ?>
	<?php if (!empty($tagsList) || $categoryKey) : ?>
	<div class="taxonomy">
		<?php if ($categoryKey) : ?>
			<a class="category" href="<?php echo $page->categoryPermalink(); ?>"><?php echo $page->category(); ?></a>
		<?php endif ?>
		<?php foreach ($tagsList as $tagKey => $tagName) : ?>
			<a href="<?php echo DOMAIN_TAGS . $tagKey; ?>">#<?php echo $tagName; ?></a>
		<?php endforeach ?>
	</div>
	<?php endif ?>

	<?php Theme::plugins('pageEnd'); ?>
</article>
