<?php if (empty($content)) : ?>
	<div class="empty"><?php $language->p('No pages found') ?></div>
<?php endif ?>

<?php foreach ($content as $page) : ?>
	<article class="post-card">
		<?php Theme::plugins('pageBegin'); ?>

		<div class="post-meta">
			<span><?php echo $page->date(); ?></span>
			<span>読了目安 <?php echo $page->readingTime(); ?></span>
		</div>

		<h2><a href="<?php echo $page->permalink(); ?>"><?php echo $page->title(); ?></a></h2>

		<?php
			$plain = trim(preg_replace('/\s+/u', ' ', strip_tags($page->content())));
			$excerpt = mb_substr($plain, 0, 160) . (mb_strlen($plain) > 160 ? '…' : '');
		?>
		<div class="excerpt"><?php echo htmlspecialchars($excerpt, ENT_QUOTES, 'UTF-8'); ?></div>

		<a class="read-more" href="<?php echo $page->permalink(); ?>">続きを読む →</a>

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
<?php endforeach ?>

<?php if (Paginator::numberOfPages() > 1) : ?>
	<nav class="paginator">
		<?php if (Paginator::showPrev()) : ?>
			<a href="<?php echo htmlspecialchars(Paginator::previousPageUrl(), ENT_QUOTES, 'UTF-8') ?>">← <?php echo $L->get('Previous'); ?></a>
		<?php endif; ?>
		<?php if (Paginator::showNext()) : ?>
			<a href="<?php echo htmlspecialchars(Paginator::nextPageUrl(), ENT_QUOTES, 'UTF-8') ?>"><?php echo $L->get('Next'); ?> →</a>
		<?php endif; ?>
	</nav>
<?php endif ?>
