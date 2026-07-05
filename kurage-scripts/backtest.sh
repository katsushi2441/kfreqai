#!/usr/bin/env bash
# バックテスト実行(過去データが必要: download-data.shを先に実行)
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm freqtrade backtesting \
  --config user_data/config.json \
  --strategy "${1:-SampleStrategy}" \
  --timerange "${2:-20260101-}"
