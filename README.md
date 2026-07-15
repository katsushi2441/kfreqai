# ![kfreqai](docs/assets/kfreqai_banner.png)

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-paper%20trading%20(dry--run)-2582A0)](https://kurage.exbridge.jp/blog/)
[![Website](https://img.shields.io/badge/website-kfreqai.exbridge.jp-1f2a52)](https://kfreqai.exbridge.jp/)

**kfreqai** is an AI-driven crypto trading system built on top of [Freqtrade](https://github.com/freqtrade/freqtrade) and its [FreqAI](https://www.freqtrade.io/en/stable/freqai/) (LightGBM) module. It layers its own risk-management logic — overheat filters, per-pair bans, volatility-scaled position sizing, and regime-aware slot capping — on top of FreqAI's price prediction engine. See "Freqtrade attribution & license" below for exactly what that means license-wise.

**This system currently runs in paper trading (dry-run) only. No real funds are traded.**

- [kfreqai.exbridge.jp](https://kfreqai.exbridge.jp/) — project landing page
- [Trading blog](https://kurage.exbridge.jp/blog/) — progress notes, incidents, and validation results (Japanese)
- [Live dashboard](https://kurage.exbridge.jp/kfreqai.php) — `public/kfreqai.php` in this repo, deployed with `kurage-scripts/deploy_dashboard.sh`

## How it thinks — and how it grows

kfreqai isn't just "train a model once and run it forever." Four loops run continuously, each with a different job:

- **Prediction** — FreqAI (LightGBM) predicts short-term price movement per pair on every candle, using its own technical indicators plus correlation features against BTC/ETH for market-wide context.
- **Regime awareness** — an hourly LLM pass (local Ollama) classifies the current market regime and adjusts how many concurrent positions the strategy is allowed to hold.
- **Risk directive** — three times a day, an LLM reviews recent regime calls and price action and sets a coarse risk stance (risk-on / risk-off / neutral) that the live strategy reads before sizing new entries.
- **Postmortem & research** — every closed trade is reviewed hourly by an LLM against its real entry/exit context (pre-entry move, max adverse/favorable excursion, exit reason — all computed in code; the LLM only interprets, never invents numbers) and journaled as a one-line lesson. Once a day, a separate LLM research pass reads a week of those lessons plus real performance stats and proposes up to two testable hypotheses in a constrained DSL — not free-form code generation. Each hypothesis is auto-converted into a backtestable strategy variant and judged mechanically: backtested against the current baseline over the same window, across ~160 pairs. Only hypotheses that improve P&L without meaningfully worsening drawdown become adoption candidates — a human still reviews and merges them into the live strategy. Every attempt, accepted or rejected, is recorded in `user_data/lab/hypothesis_ledger.jsonl`.

In short: the system doesn't just execute trades — it accumulates evidence about its own mistakes, has an LLM propose fixes, and holds every proposal to the same mechanical backtest bar before it's allowed anywhere near live trading.

## Architecture

Upstream freqtrade lives under `vendor/freqtrade` as a git submodule (reference only). **At runtime, `docker-compose.yml` uses the official `freqtradeorg/freqtrade:stable_freqai` image**, so nothing under `vendor/freqtrade` is ever mounted into the container. Only `user_data/` is read and written.

```
user_data/
  strategies/
    kfreqai_default_strategy.py # published no-op placeholder (see "What's not here")
    advisory_state.py           # shared regime/directive state read/write (no trading logic)
    kurage_freqai_strategy.py   # real live strategy -- gitignored, not published
    kfreqai_variant_*.py        # real experimental variants -- gitignored, not published
  lab/
    hypothesis_ledger.jsonl     # ledger of tested hypotheses (decision + rationale)
    trade_journal.jsonl         # trade retrospective log
  config.json                  # live config (gitignored, contains secrets)
  config.json.sample           # template with placeholder values, committed
  config_experiment*.json      # backtest configs (30-day / 6-month holdout)

kurage-advisory/                # LLM retrospective / market-regime / news-monitoring loop (systemd timers)
  advisory_api.py                 # FastAPI exposing regime/directive for the dashboard (127.0.0.1:18320)
  judgment_api.py                 # FastAPI exposing all LLM judgment functions as REST (127.0.0.1:18321, see JUDGMENT_API.md)
kurage-growth/                  # pair-expansion and volume research scripts
kurage-scripts/                 # backtest / deploy helper scripts
kurage-systemd/                 # systemd unit/timer definitions for the above
blog-bludit/                    # trading blog (Bludit) publishing
landing/                        # kfreqai.exbridge.jp static promo page
public/                         # dashboard, fully self-contained (deployed to kurage.exbridge.jp/ via FTP)
  kfreqai.php                     # dashboard source
  config.php                      # real API/blog credentials (gitignored -- copy config.php.example)
  config.php.example               # template, committed
  auth_common.php                 # shared admin-login library (copy; no secrets in the file itself,
                                   # reads a separate x_api_keys.sh that stays only on the deploy target)
  assets/, avatar/, images/       # CSS + avatar images used by the dashboard
vendor/freqtrade/               # upstream freqtrade (submodule, reference only)
```

Cloning just this repo is enough to have every line of kfreqai's own code, including the dashboard: `git clone` this repo, copy `public/config.php.example` to `public/config.php` and fill in real values, then `bash kurage-scripts/deploy_dashboard.sh` to publish it (it's PHP on shared hosting, so it has to be deployed rather than run in place). The dashboard talks to two APIs kfreqai itself exposes: freqtrade's own REST API (18313, official/unmodified image) and `advisory_api.py`'s FastAPI (proxied under the same already-public 18314 nginx server as FreqUI — no extra external port).

## Running it

```bash
cp user_data/config.json.sample user_data/config.json   # fill in your exchange key/secret, API password, etc.
docker compose up -d          # start live (dry-run)
docker compose logs -f freqtrade
```

This repo does not publish its real trading strategy (see "What's not here" below). `docker-compose.yml` defaults to `KfreqaiDefaultStrategy` (`user_data/strategies/kfreqai_default_strategy.py`), a no-op placeholder that never opens a trade -- it exists purely so a fresh clone starts cleanly instead of failing with "strategy class not found". Point `--strategy` (and `--freqaimodel`, if using FreqAI) at your own strategy to actually trade with this. Switching either requires recreating the container (`docker compose up -d`; a `reload_config` alone does not pick this up).

To run your own strategy without editing the committed `docker-compose.yml`, copy `docker-compose.override.yml.example` to `docker-compose.override.yml` (gitignored) and set `command:` there -- Docker Compose merges it automatically. That's how this repo's own production instance stays on its real strategy while the committed default stays a harmless placeholder.

## What's not here

`user_data/strategies/*.py` (except `advisory_state.py`, which has no trading logic) is gitignored: entry/exit rules, risk parameters, and ROI/stoploss values are not published. Everything else -- the FreqAI/advisory infrastructure, the dashboard, the deploy tooling -- is.

## Backtesting

The standard validation is two windows — "30 days / 160 pairs" and "6-month holdout / 17 pairs" — and a change is only adopted if it doesn't regress either.

```bash
docker compose run --rm freqtrade backtesting \
  --config user_data/config_experiment160.json \
  --strategy <StrategyName> --freqaimodel <ModelName> \
  --enable-protections --timerange 20260610-20260709
```

Results are appended to `user_data/lab/hypothesis_ledger.jsonl`; only adopted changes are merged into the live strategy.

## Reading upstream freqtrade

```bash
git submodule update --init vendor/freqtrade
```

Use this when you need to search freqtrade's source or check API behavior. It tracks the `stable` branch as-is — kfreqai carries no local modifications to it.

## Freqtrade attribution & license scope

kfreqai is built on top of [Freqtrade](https://github.com/freqtrade/freqtrade), a free and open-source crypto trading bot, licensed under the [GNU General Public License v3.0](https://github.com/freqtrade/freqtrade/blob/develop/LICENSE). Freqtrade's own copyright and license terms apply to Freqtrade itself and are unmodified — see `vendor/freqtrade`'s own `LICENSE` file (its own separate git repository, referenced here as a submodule, not copied in).

- **Version in use**: the live instance runs `freqtradeorg/freqtrade:stable_freqai` (official Docker image), currently resolving to **Freqtrade 2026.6** (verified via `docker exec kfreqai freqtrade --version`). `stable_freqai` is a moving tag; the exact image actually running is pinned by digest below for reproducibility and to identify Freqtrade's "corresponding source" if this is ever redistributed:
  ```
  freqtradeorg/freqtrade:stable_freqai
  @sha256:59a449db63a3fc0edfe9279050e60901c7e1824a9e271f283ef0af702fca5985
  ```
  (`docker image inspect freqtradeorg/freqtrade:stable_freqai --format '{{.RepoDigests}}'` on the host as of 2026-07-13.) `vendor/freqtrade`'s submodule commit is a separate reference-only pin for reading source and is **not** necessarily the same build as this digest — the two can and do drift independently.
- **This repo's own license**: `LICENSE` here is the same GPLv3 text, with a copyright header identifying kfreqai's own original code as **Copyright (C) 2026 Exbridge Inc. (株式会社エクスブリッジ)**.
- **Scope of what's GPL-derived vs. independent**:
  - `public/kfreqai.php` and `kurage-advisory/*.py` (all published here) talk to Freqtrade only over its REST API or via `ccxt` (a separate, independently-licensed library) — network/IPC communication, not linking. This code is kfreqai's own and doesn't import Freqtrade internals (verified: no `import freqtrade` / `from freqtrade` in any published file).
  - The *private*, unpublished trading strategy (`user_data/strategies/*.py`, gitignored — see "What's not here") does subclass Freqtrade's `IStrategy` and is therefore a derivative work of Freqtrade under GPLv3. It is not currently distributed, so no source-disclosure obligation is triggered today; it would need to be GPLv3-licensed if ever distributed.
  - `kurage-advisory/judgment_logic.py` (private, the LLM judgment/prompt layer — see "How it thinks") has no Freqtrade code or imports at all (verified). It's an independent program that happens to read/write JSON state files Freqtrade also reads, and is not a derivative work of Freqtrade -- this is also why it's a viable candidate for separate licensing/monetization (see the `x402` judgment backend stub) independent of Freqtrade's GPL terms.
