# Part 1: Conceptual explanation

`src/` contains the importable Python package. The package follows a domain
split: Hyperliquid REST concerns are separate from ingestion-window logic,
ClickHouse storage, scheduling, and quality checks. Scripts and Docker entry
points call into this package instead of owning product logic.

The key invariant is restart safety. No local file or process variable records
progress. Every cycle queries ClickHouse for `max(open_time)` per symbol, uses
that as the watermark, re-fetches a small overlap, and inserts rows into a
deduplicating table.

```text
config -> app -> universe -> windows -> REST fetch -> batch insert -> metadata
```

Time values inside ingestion logic use Unix epoch milliseconds in UTC. Storage
conversion to timezone-aware Python `datetime` values happens only at the
ClickHouse boundary.

# Part 2: Code reference

- `hl_candles/config.py`: loads `.env` and `config.toml` into typed settings.
- `hl_candles/app.py`: validates ClickHouse, creates schema, discovers symbols,
  runs initial backfill and incremental fetches, writes metadata.
- `hl_candles/scheduler.py`: immediate catch-up cycle plus repeated sleeping
  with jitter and signal handling.
- `hl_candles/logging_setup.py`: creates console, main log, and error log
  handlers.
- `hl_candles/ratelimit.py`: synchronous token-bucket limiter for request
  weights.
- `hl_candles/hyperliquid/`: REST client, `meta` symbol parsing, and candle
  parsing.
- `hl_candles/ingestion/`: time-window arithmetic, initial backfill pagination,
  incremental work-item construction, and gap SQL.
- `hl_candles/storage/`: ClickHouse client readiness, schema DDL, candle insert,
  watermarks, and metadata writers.
- `hl_candles/quality/`: ad-hoc quality report queries and text rendering.

Read `hl_candles/app.py` first when debugging runtime behavior.

# Part 3: Short journal

- 2026-06-21: Chose explicit package boundaries so REST, ingestion math, and ClickHouse behavior can be tested independently.
