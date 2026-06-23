# Project Notes

## ClickHouse duplicate behavior

This project intentionally re-fetches a small overlap window on every ingestion cycle. The overlap protects the dataset from short restarts, late writes, and small off-by-one timing issues because the service can safely request candles it may have already stored.

The raw `candles_1m` table uses ClickHouse `ReplacingMergeTree(inserted_at)` with `(symbol, open_time)` as the sorting key. In this context, a duplicate means multiple raw rows share the same symbol and 1-minute candle open time. During background merges, ClickHouse keeps the row with the largest `inserted_at` value for that key.

Important nuance: this physical deduplication is eventual, not immediate. Until ClickHouse background merges run, duplicate raw rows can still exist in `candles_1m`.

For research queries, use `candles_1m_clean`. That view groups by `(symbol, open_time)` and uses `argMax(..., inserted_at)` so it returns the latest version of each candle immediately, even before ClickHouse has physically merged duplicate raw rows.

Operational concern: overlap refetching is correct, but duplicate raw rows and many small ClickHouse parts should be monitored. If duplicate counts or active parts grow persistently, consider manual ClickHouse maintenance such as `OPTIMIZE TABLE hyperliquid.candles_1m FINAL`. Do not run that automatically every cycle without measuring cost, because forced final optimization can be expensive.

Current recommendation:

- Keep overlap refetching enabled.
- Read research data from `candles_1m_clean`.
- Monitor duplicate raw keys and active parts in the quality report.
- Only add manual maintenance if duplicate or part buildup becomes a real operational problem.

