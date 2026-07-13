---
title: "Building Long-Term Hyperliquid Candle History from a Short REST Window"
description: "A restart-safe Python and ClickHouse pipeline for accumulating one-minute Hyperliquid perpetual candles, repairing recent gaps, and monitoring the point where REST recovery stops being possible."
date: 2026-07-13
image: images/cover.png
categories: ["Data Science", "Capital Markets", "Quantitative Research"]
---

# Building Long-Term Hyperliquid Candle History from a Short REST Window

Hyperliquid's REST application programming interface (API) exposes only a recent slice of one-minute candles. Its official documentation says the most recent 5,000 candles are available. This project treats 5,000 one-minute slots as a conservative request window. If every minute has a candle, those 5,000 inclusive opens span 4,999 minutes, or about 3 days and 11 hours. Once an outage passes the source's retained history, a REST collector cannot reconstruct it.

That constraint changes the engineering problem. This is not a one-off downloader. It is a small service that must keep running, know exactly where storage ends for every perpetual contract, tolerate repeated inserts, and repair misses while the source still remembers them.

The resulting pipeline is deliberately narrow: discover active perpetual markets, fetch closed one-minute candles, write them to ClickHouse, and record enough quality information to spot trouble early. It does not trade, stream WebSocket data, ingest a deep archive, or operate the database itself.

## The clock starts at the last closed minute

A candle still forming at request time can change. The collector therefore stops at the latest fully closed candle.

Let $t$ be the current Unix time in milliseconds, let $\Delta=60{,}000$ milliseconds be one minute, and let $t_{\mathrm{closed}}$ be the open time of the latest closed candle. Then:

$$
t_{\mathrm{closed}}
=
\left\lfloor\frac{t}{\Delta}\right\rfloor\Delta-\Delta.
$$

Let $H$ be the configured number of candle slots. Because $H$ inclusive candle opens contain only $H-1$ intervals, the earliest requested open is:

$$
t_{\mathrm{floor}}
=
t_{\mathrm{closed}}-(H-1)\Delta.
$$

For symbol $s$, let $w_s$ be the latest stored open time and let $k$ be the number of overlap candles. The incremental request begins at:

$$
t_{\mathrm{start},s}
=
\max\left(w_s-k\Delta,\ t_{\mathrm{floor}}\right).
$$

The first term deliberately re-fetches recent rows. The second bounds the request to the project's conservative recent window. With the checked-in configuration, $k=5$ and $H=5{,}000$. An earlier version subtracted $H\Delta$, which requested 5,001 inclusive slots. The implementation and tests now use $(H-1)\Delta$.

## ClickHouse rows are the watermark

There is no local progress file and no separate watermark table. At the start of each cycle, the service queries `max(open_time)` by symbol from ClickHouse. Stored candle rows are the single source of truth.

That choice removes an awkward failure case. If a process writes candles and crashes before updating a separate cursor, the two records disagree. Here, a restart simply reads the rows that actually landed and rebuilds the next request window from them.

The cycle has three passes:

1. New symbols receive an initial backfill, clamped to the recoverable REST window.
2. Existing symbols are fetched from their watermark minus five minutes through the latest closed minute.
3. Candidate missing slots inside the REST window are detected and fetched again.

One symbol can fail without cancelling inserts for every other symbol. Writes are also chunked by a configurable row limit, so a full-universe cold start does not become one unbounded in-memory batch.

The core window calculation is short:

```python
horizon_floor_ms = earliest_open_ms_for_candle_count(
    last_open_ms=last_closed_ms,
    candle_count=rest_horizon_candles,
    interval_ms=interval_ms,
)

for symbol in symbols:
    watermark_ms = watermarks_ms.get(symbol)
    if watermark_ms is None:
        continue

    overlapped_start_ms = watermark_ms - overlap_candles * interval_ms
    start_ms = max(overlapped_start_ms, horizon_floor_ms)
    if start_ms <= last_closed_ms:
        work_items.append(
            WorkItem(symbol=symbol, start_ms=start_ms, end_ms=last_closed_ms)
        )
```

No cursor is trusted merely because the previous process claimed success. Progress is inferred from durable data.

The storage contract is intentionally plain:

| Field group | ClickHouse type | Invariant before insert |
|---|---|---|
| `symbol` | `LowCardinality(String)` | Must equal the requested market |
| `open_time`, `close_time` | `DateTime64(3, 'UTC')` | Open aligns to 60,000 milliseconds; close equals open plus 59,999 milliseconds |
| open, high, low, close (OHLC) | `Float64` | High is no lower than every OHLC value; low is no higher than every OHLC value |
| `volume` | `Float64` | Non-negative |
| `trades` | `UInt32` | Non-negative integer |

Unix epoch milliseconds are timezone-independent. The writer converts them to timezone-aware Coordinated Universal Time (UTC) Python datetimes only at the ClickHouse boundary. A naive datetime returned by a driver is explicitly interpreted as UTC, avoiding a silent shift by the host's local offset.

## Why pagination walks backward

The least obvious problem sits in the source API. The official info-endpoint page gives general forward-pagination guidance for time-range responses. The repository's `candleSnapshot` probes found a different boundary behavior for oversized candle windows: the response retained candles near `endTime` and dropped older overflow. A probe repeated during this audit returned 5,198 recent BTC one-minute candles from a 6,001-slot request. That is an observation from 13 July 2026, not an API guarantee.

Forward pagination cannot recover that overflow. Repeating a broad request with the same end keeps returning the newest reachable slice. The fetcher instead moves `endTime` backward to one interval before the oldest candle just received:

```python
oldest_open_ms = min(candle.open_time_ms for candle in page)
if oldest_open_ms <= start_ms:
    break

next_cursor_end_ms = oldest_open_ms - interval_ms
if next_cursor_end_ms >= cursor_end_ms:
    break

cursor_end_ms = next_cursor_end_ms
```

The cursor-progress guard matters. If the API ever ignores `endTime`, the loop stops instead of spinning forever. Initial backfill, incremental catch-up, and gap repair all call this same fetch primitive, so pagination behavior cannot drift across paths. Returned rows must also match the requested symbol and inclusive time window; malformed interval, timestamp, price-range, volume, or trade-count fields fail the symbol before storage.

## Rate limits are reserved before the HTTP request

Hyperliquid assigns most `info` requests a base weight of 20 and adds one unit per 60 items returned by `candleSnapshot`. Let $M$ be the number of returned candles. The documented request weight is:

$$
W(M)=20+\left\lfloor\frac{M}{60}\right\rfloor.
$$

The response size is unknown before a request. Let $S$ be the number of requested one-minute slots. The client reserves $W(S)$ from its token bucket before every attempt, using $S$ as a conservative upper bound for $M$. A 5,000-slot initial request therefore reserves $20+\lfloor5{,}000/60\rfloor=103$ units before it reaches the server. The previous implementation charged the extra item weight after the response, which allowed a cold-start burst to run ahead of its local budget.

HTTP transport errors, status 429, and server errors are retried up to four total attempts with exponential backoff and random jitter. Client errors such as an invalid request are not retried. Re-running a failed window is safe because progress comes from stored rows and overlap inserts share the same logical key.

## Overlap is safe, but duplicates are temporarily real

The raw table uses ClickHouse's `ReplacingMergeTree(inserted_at)` engine and sorts by `(symbol, open_time)`. Re-fetching five candles on every cycle is intentional. It covers boundary mistakes, recently revised source values, and crashes near the end of an insert.

Idempotent does not mean physically unique at every instant. ClickHouse removes older versions during background merges, so duplicate keys may coexist before a merge completes. Research queries that need exactly one row per symbol-minute should collapse versions with `argMax(..., inserted_at)`, query with `FINAL`, or apply an equivalent deduplication step.

This is a useful trade: ingestion stays simple and restart-safe, while readers choose whether they need immediate logical uniqueness or maximum scan speed.

## Freshness is a recovery budget

Freshness monitoring is often treated as a dashboard nicety. Here it protects data that will otherwise become permanently unavailable from REST.

![Configured freshness thresholds and REST horizon](images/01_freshness_timeline.png)

The chart uses the checked-in `config.toml`, not observed production downtime. Warning, serious, urgent, and critical thresholds occur at 60, 720, 2,880, and 4,320 minutes. The first open in the configured 5,000-slot window lies 4,999 minutes behind the final open, leaving 679 minutes, or 11 hours and 19 minutes, after the critical threshold. This is a configured safety margin, not measured source retention.

The quality command checks more than lag. It reports active-symbol freshness, duplicate raw keys, candidate gaps, daily row counts, active ClickHouse parts, and recent ingestion runs. Freshness is scoped to the current Hyperliquid universe, which prevents a delisted contract from producing a permanent false alarm while preserving its historical rows.

Coverage needs careful wording. Let $t_{\min}$ and $t_{\max}$ be the first and last stored opens in a measured window, and let $U$ be the number of unique `(symbol, open_time)` keys. The expected number of one-minute slots and the observed slot ratio are:

$$
E=\left\lfloor\frac{t_{\max}-t_{\min}}{\Delta}\right\rfloor+1,
\qquad
Q=\frac{U}{E}.
$$

$Q<1$ identifies absent stored slots. It does not prove ingestion failure because the official API documentation does not promise a candle for every no-trade minute. Gap repair can safely refetch the boundary window, but a genuine source-level empty minute may remain in later reports.

## Compression was measured, not guessed

Minute bars contain strongly structured columns. Timestamps advance regularly, adjacent prices tend to be close, trade counts are bounded integers, and fractional volume is noisy. A single generic codec is unlikely to suit all four shapes.

Let $B_u$ be uncompressed bytes, $B_c$ be compressed bytes, and $N$ be the row count. Compression ratio $R$ and compressed bytes per row $b$ are:

$$
R=\frac{B_u}{B_c},
\qquad
b=\frac{B_c}{N}.
$$

The schema decision uses $b$, where lower is better. Ratio alone can look attractive simply because the uncompressed type is wider.

![Measured compression benchmark by codec](images/02_compression_benchmark.png)

These values come from the repository's separate 3,162,240-row crypto perpetual benchmark, not a production measurement of the Hyperliquid table. On that sample, the best lossless mix used Delta plus Zstandard (ZSTD) for open, high, low, and close prices, plain ZSTD for fractional volume, and T64 plus ZSTD for trade counts. It required 16.14 bytes per row versus 19.55 for the production-style baseline, a reduction of about 17.4%. Gorilla plus ZSTD used 27.72 bytes per row, while default LZ4 used 30.54.

The created Hyperliquid schema follows the measured column pattern while choosing ZSTD level 12: `DoubleDelta` for timestamps, `Delta` for prices, plain ZSTD for lossless `Float64` volume, and `T64` for `UInt32` trade counts. The benchmark supports the codec ranking. It does not establish the eventual storage footprint of this specific dataset.

## What the design guarantees, and what it cannot

The service is restart-safe within the source's recoverable window. It recomputes state from stored rows, re-fetches a bounded overlap, isolates per-symbol failures, and attempts to heal recent internal gaps. Those properties are covered by unit tests for time arithmetic, pagination, parsing, work-item construction, gap handling, and chunked writes.

The design cannot recover an outage older than REST history. A configured 5,000-candle window is not a service-level agreement, and availability is expressed in candles rather than elapsed wall-clock minutes. Deep history still requires another source, such as an archive, or continuous collection before the window expires.

There is one more boundary worth keeping explicit: the repository contains no frozen production quality report. I can explain the failure model, tested behavior, configured thresholds, and measured codec experiment. I cannot honestly claim production uptime, ingest throughput, accumulated row count, or the live table's compression ratio from the material checked into this project.

The useful dividing line is simple: unit tests support the time arithmetic, validation, pagination, retry accounting, deduplication path, and chunked writes. The repository's compression experiment supports codec selection. Uptime, throughput, live coverage, and production storage cost remain unmeasured.

## References

- [Hyperliquid info endpoint and `candleSnapshot`](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Hyperliquid rate limits and request weights](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)
- [ClickHouse `ReplacingMergeTree`](https://clickhouse.com/docs/engines/table-engines/mergetree-family/replacingmergetree)
- [ClickHouse column compression codecs](https://clickhouse.com/docs/data-compression/compression-in-clickhouse)
