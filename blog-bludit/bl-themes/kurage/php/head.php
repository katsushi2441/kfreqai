<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
<meta name="generator" content="Bludit">
<meta name="robots" content="<?php echo (($WHERE_AM_I == 'page') && $page->noindex()) ? 'noindex, follow' : 'index, follow'; ?>">

<?php echo Theme::metaTags('title'); ?>

<?php
	$ogTitle = $site->title();
	$ogDescription = $site->description();
	$ogUrl = Theme::siteUrl();
	$ogImage = DOMAIN_THEME . 'img/ogp-default.png';
	$ogType = 'website';

	if ($WHERE_AM_I == 'page') {
		$ogTitle = $page->title() . ' | ' . $site->title();
		$desc = trim($page->description());
		if (empty($desc)) {
			$plain = strip_tags($page->content());
			$plain = preg_replace('/\s+/u', ' ', $plain);
			$desc = mb_substr($plain, 0, 110) . (mb_strlen($plain) > 110 ? '…' : '');
		}
		$ogDescription = $desc;
		$ogUrl = $page->permalink();
		$ogType = 'article';
		if ($page->coverImage()) {
			$ogImage = $page->coverImage();
		}
	}
?>
<meta name="description" content="<?php echo htmlspecialchars($ogDescription, ENT_QUOTES, 'UTF-8'); ?>">
<link rel="canonical" href="<?php echo htmlspecialchars($ogUrl, ENT_QUOTES, 'UTF-8'); ?>">

<!-- Open Graph -->
<meta property="og:site_name" content="Kurage 暗号資産 AI 自動取引日記">
<meta property="og:type" content="<?php echo $ogType; ?>">
<meta property="og:title" content="<?php echo htmlspecialchars($ogTitle, ENT_QUOTES, 'UTF-8'); ?>">
<meta property="og:description" content="<?php echo htmlspecialchars($ogDescription, ENT_QUOTES, 'UTF-8'); ?>">
<meta property="og:url" content="<?php echo htmlspecialchars($ogUrl, ENT_QUOTES, 'UTF-8'); ?>">
<meta property="og:image" content="<?php echo htmlspecialchars($ogImage, ENT_QUOTES, 'UTF-8'); ?>">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="ja_JP">

<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="<?php echo htmlspecialchars($ogTitle, ENT_QUOTES, 'UTF-8'); ?>">
<meta name="twitter:description" content="<?php echo htmlspecialchars($ogDescription, ENT_QUOTES, 'UTF-8'); ?>">
<meta name="twitter:image" content="<?php echo htmlspecialchars($ogImage, ENT_QUOTES, 'UTF-8'); ?>">

<?php if ($WHERE_AM_I == 'page' && !$page->isStatic()): ?>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BlogPosting",
  "headline": <?php echo json_encode($page->title(), JSON_UNESCAPED_UNICODE); ?>,
  "datePublished": "<?php echo date('c', strtotime($page->dateRaw())); ?>",
  "author": { "@type": "Person", "name": "Kurage" },
  "publisher": { "@type": "Organization", "name": "Kurage Project" },
  "mainEntityOfPage": <?php echo json_encode($ogUrl, JSON_UNESCAPED_UNICODE); ?>,
  "image": <?php echo json_encode($ogImage, JSON_UNESCAPED_UNICODE); ?>
}
</script>
<?php endif; ?>

<!-- RSS / Sitemap -->
<link rel="alternate" type="application/rss+xml" title="<?php echo $site->title(); ?>" href="<?php echo Theme::rssUrl(); ?>">
<link rel="sitemap" type="application/xml" href="<?php echo Theme::sitemapUrl(); ?>">

<?php echo Theme::favicon('img/favicon.png'); ?>

<?php echo Theme::css('css/kurage-avatar.css'); ?>
<?php echo Theme::css('css/style.css'); ?>

<?php Theme::plugins('siteHead'); ?>
