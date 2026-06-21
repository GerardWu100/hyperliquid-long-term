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
                         +-> logs/YYYY-MM-DD_NNN.log
```

`config.toml` stores non-secret tunables. `.env` stores ClickHouse connection
values and is intentionally not committed. Docker runs only the ingestion
service; ClickHouse is assumed to already exist.

# Part 2: Code reference

- `README.md`: user-facing setup, run commands, and scope.
- `pyproject.toml`: package dependencies and console scripts.
- `config.toml`: commented ingestion, readiness, rate-limit, and alert settings.
- `.env.example`: ClickHouse environment variable template.
- `Dockerfile`: builds the service image with `uv`.
- `docker-compose.yml`: runs the service with restart policy and external
  ClickHouse networking notes.
- `main.py`: compatibility wrapper for the long-running scheduler.
- `src/hyperliquid_candles/`: importable service package.
- `scripts/run_once.py`: one-cycle wrapper for cron or `systemd`.
- `scripts/run_quality_report.py`: quality-report wrapper.
- `tests/unit/`: fast tests for pure logic and fake-source ingestion behavior.

Start with `README.md`, then `src/hyperliquid_candles/app.py` for orchestration.

# Part 3: Short journal

- 2026-06-21: Implemented the plan as a restart-safe REST-to-ClickHouse service with ClickHouse rows as the only ingestion state.
