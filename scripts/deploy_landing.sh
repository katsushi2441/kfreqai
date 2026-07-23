#!/usr/bin/env bash
# landing/ を heteml(kfreqai.exbridge.jp)へデプロイする。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . /home/kojima/work/aixec/.env; set +a

REMOTE="/web/kfreqai_exbridge_jp"
for f in index.html kfreqai.html assets/kurage_avatar.webp assets/kurage_avatar.png assets/ogp.png; do
  curl --fail --ftp-create-dirs -T "landing/$f" \
    "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}${REMOTE}/$f"
  echo "deployed landing/$f"
done
echo "-> https://kfreqai.exbridge.jp/"
