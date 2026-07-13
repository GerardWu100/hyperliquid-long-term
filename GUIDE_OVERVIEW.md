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
small overlap, fetches that range from Hyperliquid, and inserts the rows per
symbol in bounded chunks. Symbols with no stored rows use initial backfill,
clamped to the recent REST window. After catch-up, a gap-backfill pass refetches
candidate missing periods inside that window. An absent minute is evidence of a
storage gap, but the API does not promise that every no-trade minute has a
source candle, so a candidate can remain after a successful refetch.

All fetching shares one backward-paginating primitive. Hyperliquid's
`candleSnapshot` is newest-anchored: a window wider than the available horizon
keeps the candles nearest the end and drops the oldest, so the fetcher walks
`endTime` backward instead of advancing `startTime`.

```text
ClickHouse watermarks
        |
        v
window calculation -> Hyperliquid REST -> parsed candles -> chunked insert
        |                                                    |
   gap detection (within horizon) -> refetch -> chunked insert
        |                                                    |
        +---------------- ingestion metadata <---------------+
```

The raw table uses `ReplacingMergeTree(inserted_at)` with `(symbol, open_time)`
as the sorting key. This means re-fetching the same candle is safe: duplicate raw
keys can exist briefly before ClickHouse merges. Downstream research code should
deduplicate at extract time with `argMax(..., inserted_at)`, `FINAL`, or an
equivalent collapse on `(symbol, open_time)`.

## Important Assumptions

Hyperliquid documents availability of the most recent 5,000 candles. For a
continuous one-minute series, 5,000 inclusive candle opens span 4,999 minutes,
or about 3.47 days. The service cannot recover arbitrary old outages from REST.
Long-term history is accumulated by keeping the process running continuously
and alerting before downtime approaches the configured window.

All ingestion timestamps are UTC. Internal ingestion math uses Unix epoch
milliseconds; the storage layer converts to timezone-aware Python datetime
objects when inserting into ClickHouse `DateTime64(3, 'UTC')` columns.

## Runtime Modes

`hyperliquid-candles` runs the scheduler. It configures logging once, performs an
immediate catch-up cycle on startup, then sleeps for `poll_interval_sec` plus jitter.

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

For newly created raw candle tables, the schema favors lossless compression over
lossy type changes. Price columns use first-difference encoding plus ZSTD(12),
volume remains Float64 with plain ZSTD(12), timestamps use DoubleDelta plus
ZSTD(12), and trade counts use T64 plus ZSTD(12). This follows the local
benchmark in `COMPRESSION_BENCHMARK.md`.

The REST limiter reserves the full estimated request weight before each HTTP
attempt. For candle requests, the estimate is the base 20 weight units plus one
unit per 60 requested minute slots. Reserving before sending prevents cold-start
requests from bursting past the local budget and accounting for response weight
only after the server has processed them.
