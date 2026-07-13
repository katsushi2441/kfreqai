#!/usr/bin/env bash
# public/kfreqai.php をheteml(kurage.exbridge.jp)へデプロイする。
# 認証・config.php・auth_common.phpはkurage_web側の共有基盤(複数プロジェクト
# 共通のログイン・API接続先設定)なのでそちらに残る。kfreqai固有のダッシュボード
# コード(public/kfreqai.php)だけがこのリポジトリで完結する。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . /home/kojima/work/aixec/.env; set +a

curl --fail --ftp-create-dirs -T public/kfreqai.php \
  "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}/web/kurage_exbridge_jp/kfreqai.php"
echo "deployed public/kfreqai.php -> https://kurage.exbridge.jp/kfreqai.php"
