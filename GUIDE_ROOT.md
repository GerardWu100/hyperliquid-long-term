# Part 1: Conceptual explanation

This repository is a small Python service for collecting Hyperliquid perpetual
1-minute candles into an existing ClickHouse database. Hyperliquid REST can only
serve a limited recent window, so the service is designed to run continuously:
each cycle derives state from stored ClickHouse rows, fetches the missing closed
minutes plus a small overlap, and inserts the result idempotently.

The root folder contains runtime configuration, packaging, Docker files, and
developer documentation. Product code lives under `src/hyperliquid_candles/`. Thin
operator scripts live under `scripts/`. The detailed project contract is
`docs/reference/IMPLEMENTATION_PLAN.md`.

```text
Hyperliquid REST -> hyperliquid-candles service -> ClickHouse tables/views
                         |
                         +-> process log: logs/YYYY-MM-DD_NNN.log
```

`config.toml` stores non-secret tunables. `.env` stores ClickHouse connection
values and is intentionally not committed. Docker runs only the ingestion
service; ClickHouse is assumed to already exist. The long-running scheduler
configures logging once when the process starts; one-shot commands configure
logging for their single cycle.

# Part 2: Code reference

- `README.md`: user-facing setup, run commands, and scope.
- `COMPRESSION_BENCHMARK.md`: measured ClickHouse codec choices for 1-minute
  market data, including the raw candle table's price, volume, timestamp, and
  trade-count compression strategy.
- `pyproject.toml`: package dependencies and console scripts.
- `config.toml`: commented ingestion, readiness, rate-limit, and alert settings.
- `.env.example`: ClickHouse environment variable template.
- `Dockerfile`: builds the service image with `uv`.
- `docker-compose.yml`: runs the service with restart policy and external
  ClickHouse networking notes.
- `main.py`: compatibility wrapper for the long-running scheduler.
- `notes.md`: short project notes for operational behavior and concerns.
- `src/hyperliquid_candles/`: importable service package.
- `scripts/run_once.py`: one-cycle wrapper for cron or `systemd`.
- `scripts/run_quality_report.py`: quality-report wrapper.
- `tests/unit/`: fast tests for pure logic and fake-source ingestion behavior.

Start with `README.md`, then `src/hyperliquid_candles/app.py` for orchestration.

# Part 3: Short journal

- 2026-06-21: Implemented the plan as a restart-safe REST-to-ClickHouse service with ClickHouse rows as the only ingestion state.
- 2026-06-22: Scheduler logging now initializes once per process instead of creating a new log file every ingestion cycle.
- 2026-06-24: Updated the raw candle DDL to follow the compression benchmark: Delta plus ZSTD(12) for prices, plain ZSTD(12) for lossless fractional volume, DoubleDelta for timestamps, and T64 for trades.
