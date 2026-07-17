# kurageテーマ(Bludit)のphp控え

本番: heteml `/web/kurage_exbridge_jp/blog/bl-themes/kurage/php/`(FTPデプロイ)。
リポジトリ側は控え。編集したらFTPで反映すること。
2026-07-14 SEO監査: canonical重複削除(canonicalプラグインに一本化)、
sitemapリンクをblog_post.py生成の静的sitemap.xmlへ変更。

## コメント機能

`bl-plugins/kurage-comments/` は、外部サービスに依存しない記事コメント機能。
コメント本文は本番の `bl-content/workspaces/kurage-comments/comments/` に保存される。
コードのみGit管理し、投稿データや署名用secretはGitへ含めない。

有効化を含む限定デプロイ:

```bash
source /home/kojima/work/aixec/.env
python3 kurage-scripts/deploy_blog_comments.py
```
