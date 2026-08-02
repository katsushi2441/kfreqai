<?php if (!isset($_GET['page']) || (int)$_GET['page'] <= 1) : /* 1ページ目にだけ置く */ ?>
<section class="vibe-intro" id="vibe-trading">
	<h1>バイブトレーディングの実運用記録</h1>
	<p>
		戦略のアイデアを日本語でAIに伝え、バックテストの数字で検証しながら育てる運用スタイルを<strong>バイブトレーディング</strong>と呼んでいます。
		このブログは、そのバイブトレーディングで作った暗号資産（<a href="https://kurage.exbridge.jp/kfreqai.php">kfreqai</a>）とFX（<a href="https://kurage.exbridge.jp/kfxai.php">kfxai</a>）の自動売買戦略を実際に動かし、
		AIの市況判断・採用した戦略とその根拠・そして<strong>負けたトレードの検死</strong>まで公開している記録です。
	</p>
	<p class="vibe-links">
		はじめての方は<a href="https://katsushi2441.github.io/vwork/blog/2026-07-31-vibe-trading-tools-guide.html">バイブトレーディング実践ガイド</a>へ。
		ウォレット1つで試すなら<a href="https://kurage.exbridge.jp/kfreqaihl.php">Hyperliquid版</a>、
		仕組みの解説は<a href="https://kfreqai.exbridge.jp/kfreqai.html#vibe-trading">kfreqai公式サイト</a>にあります。
	</p>
</section>
<?php endif ?>

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
