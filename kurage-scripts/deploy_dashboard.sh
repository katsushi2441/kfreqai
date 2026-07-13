#!/usr/bin/env bash
# public/ 以下(kfreqai.php・config.php・auth_common.php・CSS・画像)を
# heteml(kurage.exbridge.jp)へデプロイする。public/ だけでダッシュボードが
# 完結するように、このリポジトリにkfreqai独自のコードと必要な共有ファイルの
# コピーを両方置いている(config.php/auth_common.phpは複数プロジェクト共通の
# ログイン・API接続基盤だが、clone一発で動かせるようこのリポジトリにも複製)。
# config.phpは実シークレットを含むためgitignore対象(config.php.exampleを参照)。
# デプロイ先には既に稼働中のconfig.phpがあるため、自動デプロイの対象からは
# 外している(値を変えたときだけ手動でアップロードする)。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . /home/kojima/work/aixec/.env; set +a

REMOTE="/web/kurage_exbridge_jp"
for f in kfreqai.php auth_common.php \
         assets/kurage-avatar.css images/kfreqai_ogp.png \
         avatar/lipsync/kurage_mouth_0.png avatar/lipsync/kurage_mouth_1.png \
         avatar/lipsync/kurage_mouth_2.png avatar/lipsync/kurage_mouth_3.png \
         avatar/lipsync/kurage_mouth_4.png; do
  curl --fail --ftp-create-dirs -T "public/$f" "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}${REMOTE}/$f"
  echo "deployed public/$f"
done
echo "-> https://kurage.exbridge.jp/kfreqai.php"
