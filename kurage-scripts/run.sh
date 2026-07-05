#!/usr/bin/env bash
# Kurage Freq AI Trade — docker composeでのフォアグラウンド起動(動作確認用)
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up
