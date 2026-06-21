# Part 1: Conceptual explanation

`scripts/` contains thin command wrappers for operators who prefer file paths
over installed console scripts. The scripts do not implement ingestion logic;
they import package entrypoints from `src/hyperliquid_candles/`.

Use `scripts/run_once.py` for cron or `systemd` timers and
`scripts/run_quality_report.py` for ad-hoc ClickHouse data checks.

# Part 2: Code reference

- `run_once.py`: calls `hyperliquid_candles.scripts_run_once.main`, which runs exactly one
  ingestion cycle.
- `run_quality_report.py`: calls `hyperliquid_candles.quality.checks.main`, which prints
  a plain-text quality report whose freshness section covers active symbols.

Prefer the console scripts `uv run hyperliquid-candles-run-once` and `uv run hyperliquid-candles-quality` unless a
file path is required by an external scheduler.

# Part 3: Short journal

- 2026-06-21: Kept scripts as wrappers so reusable behavior stays in the package.
