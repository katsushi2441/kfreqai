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
		<?php echo $page->content(); ?>
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
