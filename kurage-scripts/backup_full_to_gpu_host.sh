#!/usr/bin/env bash
# user_data/を丸ごと(config*・secret含む)192.168.0.11へバックアップ。
# 除外はuser_data/data(MEXCから再ダウンロード可能な相場データキャッシュ)のみ。
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
  -e "ssh ${ssh_opts}" \
  user_data/ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
