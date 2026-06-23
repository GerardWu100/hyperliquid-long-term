# Project Notes

## Rate-limit pacing and per-IP budget

Hyperliquid enforces an aggregated REST weight limit of 1200 per minute per IP
address (not per wallet; we only call public `info` endpoints). "Weight" is a
cost unit, not a request count: each minute the sum of weights across all REST
calls from the IP must stay under 1200.

`candleSnapshot` weight = base 20 + 1 per 60 candles returned. Our client models
this exactly (`client.py`: `BASE_INFO_WEIGHT = 20`, plus `len(candles) // 60`).

Worked example (500 tickers, 35-minute incremental window each):

- Per ticker, one call returns ~35 candles, fewer than one 60-item block, so the
  per-item surcharge is 0. Cost = base 20 only.
- One full sweep = 500 * 20 = 10,000 weight.
- The 10,000 weight is the cost of a sweep, not a per-minute rate. How it lands
  against the 1200/min ceiling depends entirely on pacing.

Will the app break? No, because it never fires all calls at once. The token
bucket in `ratelimit.py` releases work at `weight_budget_per_min` and blocks
(sleeps) otherwise, so steady-state outflow is capped below 1200/min. At the
current budget of 1000/min, one 10,000-weight sweep takes about
10,000 / 1000 = 10 minutes, which fits inside the 30-minute `poll_interval_sec`
with ~20 minutes idle to spare. Firing the same 500 calls unpaced inside one
minute would be ~10,000 weight/min (about 8x over) and would trigger HTTP 429s;
the bucket is what prevents that.

When it would actually break:

- Sweep time exceeds the poll interval. At 1000/min a 500-ticker sweep is ~10
  min, well under 30 min. Risk appears only if `poll_interval_sec` is shortened
  below sweep time, or the ticker count grows enough that
  count * 20 / 1000 minutes per sweep exceeds the poll interval (~1500 tickers at
  the current budget and 30-minute cycle).
- Shared IP. The 1200/min ceiling is aggregated across everything leaving the
  IP. Other tools on the same IP draw from the same budget; our budget assumes
  this collector is the sole consumer.
- Cold-start burst. The bucket's `capacity` defaults to `tokens_per_minute`, so
  it starts full. Max spend in any rolling minute is `capacity + refill`, i.e.
  1000 + 1000 = 2000, which can exceed 1200 during the first minute of a fresh
  backfill. The 429 retry in `_post_info_with_retry` (exponential backoff)
  absorbs this in practice. For a hard guarantee, cap `capacity` so that
  `capacity + budget <= 1200`.

Current recommendation:

- Keep `weight_budget_per_min` below 1200 (currently 1000) so steady-state
  pacing stays under the per-IP ceiling.
- Keep `poll_interval_sec` comfortably above the sweep time for the active
  ticker count.
- Ensure no other tool shares the IP's 1200/min budget, or lower the budget to
  leave room.

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

