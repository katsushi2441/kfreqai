#!/usr/bin/env bash
# public/ の PHP を heteml(kurage.exbridge.jp)へデプロイする。
#
# 注意: /web/kurage_exbridge_jp/config.php は kfreqai / kfreqaihl / rqdb4ai の共有ファイル。
# ここからは絶対にアップロードしない(片方のrepo版で上書きすると他がFatal errorになる)。
# kfreqaihl_allowlist.php も招待リストの実体がサーバ側にあるため既定では送らない。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
. /home/kojima/work/aixec/.env
set +a

remote="/web/kurage_exbridge_jp"
upload() {
  local source_file="$1"
  local remote_file="$2"
  curl --fail --silent --show-error --ftp-create-dirs -T "$source_file" \
    "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}${remote}/${remote_file}"
  echo "deployed: ${remote_file}"
}

targets=("${@:-kfreqaihl.php}")
for t in "${targets[@]}"; do
  case "$t" in
    config.php) echo "refused: config.php は共有ファイルなので送らない" >&2; exit 1 ;;
  esac
  upload "public/$t" "$t"
done

echo "-> https://kurage.exbridge.jp/kfreqaihl.php"
