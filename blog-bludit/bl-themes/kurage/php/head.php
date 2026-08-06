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

	// トップ/一覧は、このブログが何の記録なのか（バイブトレーディングの実運用）を説明に含める。
	if ($WHERE_AM_I != 'page') {
		$ogDescription = trim($ogDescription) . ' 戦略を日本語でAIに相談しバックテストで検証しながら育てる「バイブトレーディング」の実運用記録です。';
	}

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
<meta name="keywords" content="バイブトレーディング,バイブコーディング,AI自動売買,暗号資産 自動取引,FX 自動売買,AIトレード,kfreqai,kfxai,Kurage">
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

<?php if ($WHERE_AM_I != 'page'): /* 用語の定義はトップ/一覧にだけ置く（記事ごとの重複を避ける） */ ?>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "DefinedTerm",
      "@id": "<?php echo htmlspecialchars(Theme::siteUrl(), ENT_QUOTES, 'UTF-8'); ?>#vibe-trading",
      "name": "バイブトレーディング",
      "alternateName": ["Vibe Trading"],
      "description": "取引戦略のアイデアを日本語でAIに伝えてコードに落とし、バックテストの数字で検証しながら戦略を育てていく運用スタイル。コードをAIに書かせる「バイブコーディング」をトレードに応用した言葉。"
    },
    {
      "@type": "Blog",
      "name": <?php echo json_encode($site->title(), JSON_UNESCAPED_UNICODE); ?>,
      "url": <?php echo json_encode(Theme::siteUrl(), JSON_UNESCAPED_UNICODE); ?>,
      "inLanguage": "ja",
      "description": "バイブトレーディングで作った暗号資産・FXの自動売買戦略を実際に動かし、勝ちも負けも数字で公開している実運用記録。",
      "keywords": "バイブトレーディング, AI自動売買, 暗号資産, FX",
      "publisher": { "@type": "Organization", "name": "株式会社エクスブリッジ", "url": "https://exbridge.jp/" }
    },
    {
      "@type": "FAQPage",
      "mainEntity": [
        {
          "@type": "Question",
          "name": "バイブトレーディングとは何ですか？",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "取引戦略のアイデアを日本語でAIに伝えてコードに落とし、バックテストの数字で検証しながら戦略を育てていく運用スタイルです。仮説を出すのは人間、コードにするのはAI、正しいかを決めるのはバックテストの数字、という分業が特徴です。"
          }
        },
        {
          "@type": "Question",
          "name": "このブログでは何が読めますか？",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "バイブトレーディングで作った暗号資産(kfreqai)とFX(kfxai)の自動売買戦略を実際に動かした記録です。AIの市況判断、採用した戦略とその根拠、そして負けたトレードの検死まで公開しています。すべてdry-run(紙上取引)のシミュレーションで、実際の資金は動いていません。"
          }
        }
      ]
    }
  ]
}
</script>
<?php endif; ?>

<!-- RSS / Sitemap -->
<link rel="alternate" type="application/rss+xml" title="<?php echo $site->title(); ?>" href="<?php echo Theme::rssUrl(); ?>">
<link rel="sitemap" type="application/xml" href="<?php echo Theme::sitemapUrl(); ?>">

<?php echo Theme::favicon('img/favicon.png'); ?>

<?php echo Theme::css('css/kurage-avatar.css'); ?>
<link rel="stylesheet" type="text/css" href="<?php echo DOMAIN_THEME; ?>css/style.css?v=20260719-2">

<?php Theme::plugins('siteHead'); ?>

<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-BP0650KDFR"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-BP0650KDFR');
</script>
<script>
(function () {
    var s = document.createElement('script');
    s.src = 'https://kurage.exbridge.jp/simpletrack.php'
        + '?url=' + encodeURIComponent(location.href)
        + '&ref=' + encodeURIComponent(document.referrer);
    document.head.appendChild(s);
})();
</script>
