# Hyperliquid Long-Term Candle Ingestion Overview

```text
hyperliquid-candles/
├── README.md
├── config.toml
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── src/
│   └── hyperliquid_candles/
│       ├── app.py
│       ├── hyperliquid/
│       ├── ingestion/
│       ├── storage/
│       └── quality/
├── scripts/
├── tests/
└── docs/reference/IMPLEMENTATION_PLAN.md
```

This project stores Hyperliquid perpetual 1-minute candles in an existing
ClickHouse database. It is not a trading system and it does not manage
ClickHouse itself. Its job is narrower: discover active perpetual symbols, fetch
closed 1-minute candles from Hyperliquid REST, insert them into ClickHouse in
batches, and record enough metadata to understand each ingestion cycle.

## Main Flow

Each cycle starts from ClickHouse, not from local memory. The service queries the
latest stored candle open time for each symbol, computes the missing range plus a
small overlap, fetches that range from Hyperliquid, and inserts all rows in a
single batch. Symbols with no stored rows use initial backfill, clamped to the
REST horizon.

```text
ClickHouse watermarks
        |
        v
window calculation -> Hyperliquid REST -> parsed candles -> batch insert
        |                                                    |
        +---------------- ingestion metadata <---------------+
```

The raw table uses `ReplacingMergeTree(inserted_at)` with `(symbol, open_time)`
as the sorting key. This means re-fetching the same candle is safe: duplicate raw
keys can exist briefly before ClickHouse merges, but the `candles_1m_clean` view
uses `argMax` so research queries can read a duplicate-safe series.

## Important Assumptions

Hyperliquid REST exposes only roughly the most recent 5000 one-minute candles,
which is about 3.47 days. The service therefore cannot recover arbitrary old
outages from REST. Long-term history is accumulated by keeping the process
running continuously and alerting before downtime approaches that horizon.

All ingestion timestamps are UTC. Internal ingestion math uses Unix epoch
milliseconds; the storage layer converts to timezone-aware Python datetime
objects when inserting into ClickHouse `DateTime64(3, 'UTC')` columns.

## Runtime Modes

`hyperliquid-candles` runs the scheduler. It performs an immediate catch-up cycle on
startup, then sleeps for `poll_interval_sec` plus jitter.

`hyperliquid-candles-run-once` runs exactly one cycle. This is useful for cron, `systemd` timers,
manual checks, and debugging.

`hyperliquid-candles-quality` prints a plain-text report covering latest candle
freshness for currently selected active symbols, duplicates, gaps, daily counts,
ClickHouse parts, and recent ingestion runs.

## Tradeoffs

The implementation favors a small number of explicit modules over a framework.
There is no separate watermark table because that would create a second source
of truth. ClickHouse rows are the source of truth, and failures between fetch and
insert are recovered by the next cycle re-querying actual stored rows.
