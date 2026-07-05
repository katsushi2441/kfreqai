#!/usr/bin/env bash
# バックテスト用の過去OHLCVデータをダウンロード
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm freqtrade download-data \
  --config user_data/config.json \
  --timerange "${1:-20250101-}" \
  --timeframe 5m 1h 1d
