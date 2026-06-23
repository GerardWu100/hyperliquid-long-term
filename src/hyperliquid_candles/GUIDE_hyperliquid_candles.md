# Part 1: Conceptual explanation

`hyperliquid_candles` is the service package. It coordinates three external surfaces:
Hyperliquid REST, ClickHouse HTTP, and the process runtime used by Docker,
cron, or `systemd`.

The package is organized by responsibility:

```text
config/logging/scheduler
          |
          v
app orchestration
   |            |
   v            v
Hyperliquid   ClickHouse
REST          storage
   \            /
    v          v
    ingestion decisions
```

The important data invariant is idempotency. For each symbol and 1-minute open
time, the table may temporarily contain multiple raw rows because the service
re-fetches overlap windows. ClickHouse eventually collapses those rows by
`inserted_at`, and the clean read view collapses them immediately for research
queries.

Internal code uses plain dataclasses and protocol-style interfaces where tests
need fakes. There is no local state file, no local database, and no watermark
cache.

# Part 2: Code reference

- `app.py`: top-level ingestion cycle. It loads symbols, reads watermarks,
  computes initial/incremental work, inserts candle rows, and writes run
  metadata. Standalone runs configure logging inside `run_once`; scheduler
  mode configures logging once in `main` and disables per-cycle logging setup.
- `config.py`: turns `.env` plus `config.toml` into immutable settings objects.
- `logging_setup.py`: creates log files and console logging.
- `scheduler.py`: repeats a callback with configured cadence and shutdown
  signal handling.
- `ratelimit.py`: token-bucket pacing for weighted Hyperliquid REST requests.
- `scripts_run_once.py`: console-script entrypoint for one cycle.
- `hyperliquid/client.py`: HTTP POST client for `meta` and `candleSnapshot`.
- `hyperliquid/universe.py`: active symbol selection from `meta`.
- `hyperliquid/candles.py`: candle dataclass and parser.
- `ingestion/windows.py`: last-closed-minute and initial-start calculations.
- `ingestion/backfill.py`: initial backfill pagination.
- `ingestion/incremental.py`: restart-safe incremental work windows.
- `ingestion/gaps.py`: gap-detection SQL.
- `storage/clickhouse_client.py`: readiness-aware ClickHouse connection.
- `storage/schema.py`: database, tables, and clean view DDL.
- `storage/writer.py`: columnar candle inserts.
- `storage/watermarks.py`: `max(open_time)` query.
- `storage/runs.py`: run and per-symbol metadata inserts.
- `quality/checks.py`: quality-report SQL and text rendering. Freshness checks
  resolve the current active Hyperliquid universe before querying ClickHouse, so
  historical delisted symbols do not create live staleness alerts.

Start in `app.py` for runtime behavior and in `tests/unit/` for examples of the
pure ingestion logic.

# Part 3: Short journal

- 2026-06-21: Kept ClickHouse as the only source of progress so crash recovery and reboot recovery use the same path.
- 2026-06-21: Scoped freshness monitoring to currently active symbols while keeping historical candle rows intact.
- 2026-06-22: Scheduler mode keeps one process log for repeated cycles; one-shot mode still creates a log for its single run.
