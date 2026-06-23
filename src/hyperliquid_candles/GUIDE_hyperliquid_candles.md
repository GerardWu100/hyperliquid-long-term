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
  computes initial/incremental work, inserts candle rows per symbol in bounded
  chunks, runs a gap-backfill pass, and writes run metadata. A failure isolated
  to one symbol marks only that symbol failed and the cycle continues.
  Standalone runs configure logging inside `run_once`; scheduler mode configures
  logging once in `main` and disables per-cycle logging setup.
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
- `ingestion/fetch.py`: the single `fetch_candle_window` primitive. It pages
  backward by `endTime` because Hyperliquid's `candleSnapshot` is newest-anchored
  (a too-wide window keeps the candles nearest the end and drops the oldest).
  Backfill, incremental catch-up, and gap repair all use it, so no path can
  silently truncate.
- `ingestion/incremental.py`: restart-safe incremental work windows.
- `ingestion/gaps.py`: gap-detection SQL (`gap_query` for the full report,
  `recoverable_gaps_query` bounded to the REST horizon for cycle repair) plus
  `parse_gap_rows`/`CandleGap`.
- `storage/clickhouse_client.py`: readiness-aware ClickHouse connection.
- `storage/schema.py`: database, tables, and clean view DDL.
- `storage/writer.py`: columnar candle inserts, chunked by `batch_insert_max_rows`
  so a single insert never buffers an unbounded number of rows.
- `storage/watermarks.py`: `max(open_time)` query plus the shared
  `datetime_to_ms` converter.
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
- 2026-06-22: Unified all fetching on a backward-paginating `fetch_candle_window` after probing the live API: `candleSnapshot` is newest-anchored with a ~5186-candle horizon tied to now, so forward-by-start pagination could not reach older data. Incremental no longer issues a single unchecked request.
- 2026-06-22: Inserts now run per symbol in `batch_insert_max_rows` chunks (previously one unbounded all-symbols insert), bounding memory on full-universe cold starts and isolating per-symbol failures.
- 2026-06-22: Added a gap-backfill phase. Verified against the live API that Hyperliquid emits a candle every minute (continuous even for illiquid coins), so internal gaps are real ingestion misses and safe to refetch; repair is bounded to the REST horizon.
