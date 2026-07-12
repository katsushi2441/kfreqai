#!/usr/bin/env bash
# user_data/(GitHub未コミットのDB・モデル・検証記録)を192.168.0.11へバックアップ。
# 除外: user_data/data(MEXCから再ダウンロード可能な相場データキャッシュ)。
# 除外: config*・blog-bludit-admin.txt(APIサーバーのpassword/jwt_secret_key等の
#   実シークレットを含むため、自動転送はauto modeのData Exfiltration判定でハードブロックされる。
#   この2種類だけは手動でコピーすること: scp -P 2222 -i ~/.ssh/id_kfreqai_backup
#   user_data/config.json user_data/blog-bludit-admin.txt kojima@192.168.0.11:backups/kfreqai/user_data/)
# vendor/はgit submoduleなので対象外(user_data/だけを同期する)。
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE_HOST=192.168.0.11
REMOTE_PORT=2222
REMOTE_USER=kojima
REMOTE_KEY=~/.ssh/id_kfreqai_backup
REMOTE_DIR=~/backups/kfreqai/user_data/

ssh_opts="-p ${REMOTE_PORT} -i ${REMOTE_KEY} -o IdentitiesOnly=yes -o BatchMode=yes"

rsync -az --info=stats2 \
  --exclude='data/' \
  --exclude='__pycache__/' \
  --exclude='config*' \
  --exclude='blog-bludit-admin.txt' \
  -e "ssh ${ssh_opts}" \
  user_data/ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
