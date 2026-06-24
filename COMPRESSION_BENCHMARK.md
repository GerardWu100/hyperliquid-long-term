# ClickHouse Compression Decisions for Minute-Level Market Data

Benchmark date: 2026-06-24. Server: local ClickHouse 26.5.1.882 (port 50050).

This report records how column codecs were chosen for one-minute OHLCV
(open / high / low / close / volume) bars, backed by measurements on two real
datasets on the local server. It is a standalone storage study; it is not part of
the Hyperliquid ingestion service. All experiments ran in throwaway tables in the
`hyperliquid` database (prefix `zz_`), which were dropped afterwards. The existing
tables `candles_1m`, `ingestion_runs`, and `ingestion_symbol_status` were never
touched.

Two datasets were studied, and they are NOT the same shape, so they get different
recommended schemas:

| Dataset | Source table | Grain | Distinguishing trait |
|---|---|---|---|
| Crypto perpetual bars | `coinmetrics.perpetual` | 1-minute | wide price range, **fractional** volume, has `trades_count` |
| US equity bars | `firstrate.stocks` | 1-minute | low price magnitude, **integer** volume, dirty data |

## Definitions

- Let $B_u$ be uncompressed bytes and $B_c$ compressed bytes. Compression ratio
  is $R = B_u / B_c$ (higher is better).
- Let $N$ be row count. Bytes per row is $b = B_c / N$ (lower is better). This is
  the number that decides disk cost, and the metric used for all decisions below.
- A ClickHouse codec is the on-disk encoding attached to a column. Relevant ones:
  - `Delta` stores consecutive differences. Wins on values that trend (prices).
  - `DoubleDelta` stores second-order differences. Wins on regularly spaced
    sequences (timestamps every 60 s).
  - `T64` bit-packs a block of integers to the minimum bit width its range needs.
    Wins on bounded, non-sequential integers (volume, trade counts).
  - `Gorilla` / `FPC` are float-oriented codecs (XOR / predictor).
  - `ZSTD(level)` is a general-purpose entropy compressor applied last.

## Measurement method

The read-only user pulled representative samples in `Native` binary format
(exact types preserved). The `hyperliquid`-scoped write user loaded them and built
codec-variant tables that hold identical rows and differ only in `CODEC(...)`.
The write user lacks the `OPTIMIZE` privilege, so each variant was written as a
single part (single insert block, no partitioning) to keep byte counts stable and
directly comparable. Sizes were read from `system.columns` and `system.parts`.

## Production baselines (real, full tables)

Both source tables already ship a tuned schema: `ts CODEC(DoubleDelta, ZSTD(3))`,
all floats `CODEC(ZSTD(3))`. Current on-disk state:

| Table | Rows | Uncompressed | Compressed | Ratio | Bytes/row |
|---|---:|---|---|---:|---:|
| `firstrate.stocks` | 4,021,015,596 | 186.74 GiB | 39.07 GiB | 4.78x | 10.43 |
| `coinmetrics.perpetual` | 614,563,525 | 30.45 GiB | 9.90 GiB | 3.08x | 17.30 |

Sample-based bytes/row below run higher than these because full multi-GiB merged
parts amortize dictionaries and sorting better than the smaller single-part
samples. The codec RANKING transfers; the absolute sample numbers slightly
overstate production.

## Findings that apply to both datasets

1. `Delta` on the OHLC price columns is the largest universal win. Adjacent
   one-minute prices are close, so the byte-wise delta has many zero high-order
   bytes that ZSTD then crushes (about 25% per price column).
2. `Gorilla` and `FPC` are the wrong choice for OHLC, despite their float
   reputation. They made prices 40-80% larger; they emit high-entropy bit-packed
   output that defeats the downstream ZSTD pass.
3. `Delta` helps OHLC but slightly hurts `volume` (volume is noisy and
   non-monotonic, so differencing raises entropy). Volume should not use `Delta`.
4. `T64` wins on bounded integers (volume, `trades_count`) but loses on prices
   (prices trend, so `Delta` beats bit-packing absolute values).
5. `DoubleDelta` on timestamps is essential and already optimal (60 s spacing
   makes second-order differences constant).
6. Optimize bytes/row, not ratio. A wide type can show a flattering ratio while
   using more disk (see the `Decimal(38,6)` case below).
7. ZSTD level trades size for write CPU. `ZSTD(22)` was about 10x slower to write
   than `ZSTD(9)` for a few percent gain. Use `ZSTD(9)` for active ingestion,
   `ZSTD(12..22)` for a write-once archive.

---

# Dataset 1: CoinMetrics perpetual (crypto) minute bars

Source columns: `symbol LowCardinality(String)`, `ts DateTime64(0,'UTC')`,
`open/high/low/close/volume Float64`, `trades_count UInt32`.

## Codec sweep (sample: 6 symbols across price tiers, full-year 2024, 3,162,240 rows)

| Variant | Bytes/row | Ratio | vs production |
|---|---:|---:|---:|
| Delta+ZSTD9 OHLC, ZSTD9 volume, T64 trades_count (best mix) | 16.14 | 3.28 | -17% |
| Delta+ZSTD22 on all floats | 16.15 | 3.28 | -17% |
| Delta+ZSTD9 on all floats | 16.80 | 3.16 | -14% |
| Delta+ZSTD3 on all floats | 17.14 | 3.09 | -12% |
| production (ts DoubleDelta+ZSTD3, float ZSTD3) | 19.55 | 2.71 | 0% |
| Gorilla+ZSTD3 on floats | 27.72 | 1.91 | +42% |
| LZ4 on all columns (ClickHouse default) | 30.54 | 1.74 | +56% |

## Decimal vs Float for crypto prices (equal ZSTD(12))

| Price representation | Bytes/row |
|---|---:|
| Float64 + Delta | 16.03 |
| Decimal(18,8) + Delta | 16.67 |
| Float64 + ZSTD only | 18.18 |
| Decimal(18,8) + T64 | 20.89 |

Decimal does NOT help crypto. Crypto needs 8 decimals (for sub-dollar coins) and
spans to roughly 1e5, so scaled integers are about 44 bits wide and high-entropy.
`Float64 + Delta` is the best price encoding here.

## The volume column (the dominant cost)

`volume` is fractional (99.8% non-integer), spans 2.47 to 1.48e9, and is almost
entirely unique (3,161,350 distinct values in 3,162,240 rows). It is roughly 45%
of the table's bytes, so it dominates the storage budget.

| Volume representation | Bytes/row | Relative error |
|---|---:|---:|
| Float64 + ZSTD(12) (current, lossless) | 7.24 | 0 |
| Float64 + Gorilla + ZSTD(12) | 7.36 | 0 |
| Float64 + Delta + ZSTD(12) | 7.51 | 0 |
| Float32 + ZSTD(12) | 3.60 | 6e-8 (about 7 significant figures) |
| Float64 rounded to 6 significant figures + ZSTD(12) | 3.64 | 5e-6 |

Lossless conclusion: `Float64 + ZSTD` is already optimal; `Gorilla` and `Delta`
both make it worse. Lossy conclusion: `Float32` halves the column (about 7
significant figures of precision), which cuts the whole crypto table by roughly
20-23%. This is the single largest compression lever in this dataset, far larger
than any price-codec tuning. Volume is an aggregate estimate, so about 7
significant figures is normally acceptable; verify against the CoinMetrics
documented precision before committing, because the downcast is irreversible.

## Recommended schema (crypto)

```sql
symbol        LowCardinality(String)  CODEC(ZSTD(3)),
ts            DateTime64(0, 'UTC')    CODEC(DoubleDelta, ZSTD(9)),
open          Float64 CODEC(Delta, ZSTD(9)),
high          Float64 CODEC(Delta, ZSTD(9)),
low           Float64 CODEC(Delta, ZSTD(9)),
close         Float64 CODEC(Delta, ZSTD(9)),
-- volume: choose ONE
volume        Float32 CODEC(ZSTD(9)),   -- recommended: ~7 sig figs, halves the column
-- volume     Float64 CODEC(ZSTD(9)),   -- bit-exact alternative (already optimal lossless)
trades_count  UInt32  CODEC(T64, ZSTD(12))
```

Expected effect versus current production: prices and `trades_count` give about
-17% bytes/row on their own; switching `volume` to `Float32` adds roughly another
-20% on the whole table.

---

# Dataset 2: FirstRate stocks (US equity) minute bars

Source columns: `symbol LowCardinality(String)`,
`ts DateTime64(3,'America/New_York')`, `open/high/low/close/volume Float64`.

## Codec sweep (sample: 5 liquid names, 2022-2023, 1,777,493 rows)

| Variant | Bytes/row | Ratio | vs production |
|---|---:|---:|---:|
| Delta+ZSTD22 on all floats | 11.71 | 4.18 | -15% |
| Delta+ZSTD9 OHLC, T64 integer volume (best mix) | 12.02 | 4.08 | -12% |
| Delta+ZSTD3 on all floats | 13.04 | 3.76 | -5% |
| production (ts DoubleDelta+ZSTD3, float ZSTD3) | 13.73 | 3.57 | 0% |
| Gorilla+ZSTD3 on floats | 25.01 | 1.96 | +82% |
| LZ4 on all columns (ClickHouse default) | 25.12 | 1.95 | +83% |

## Integer volume (lossless win)

Stock volume is 100% integer-valued (share counts), max about 2.3e8. Stored as an
integer it compresses far better than as a float:

| Volume representation | Volume column size |
|---|---:|
| Float64 + ZSTD(3) | 5,227 KB |
| UInt32 + T64 + ZSTD(3) | 3,897 KB (-25%) |

Use `UInt32 CODEC(T64, ZSTD)`. (`UInt64` if any per-minute share volume could
exceed about 4.29e9; not the case in the data, max was 2.3e8.)

## Decimal for stock prices (the gerardwu.com method)

ClickHouse `Decimal(P, S)` stores a fixed-point value as a scaled integer
(`Decimal(18,4)` keeps `123.4567` as `1234567`), which lets integer codecs work.
This is lossless to the chosen scale. Tested at equal ZSTD(12):

On 5 clean large-cap names (scale 4):

| Price representation | Bytes/row |
|---|---:|
| Decimal(18,4) + Delta | 9.17 |
| Decimal(18,4) + ZSTD | 9.77 |
| Decimal(18,4) + T64 | 9.90 |
| Float64 + Delta | 11.22 |

That suggested about -18%. But on a realistic tiered universe (8 symbols from
1e-6 to 566,570 dollars, scale 6, garbage removed) the gain shrinks:

| Price representation | Bytes/row | Ratio |
|---|---:|---:|
| Decimal(18,6) + Delta | 10.24 | 4.40 |
| Decimal(18,6) + ZSTD | 11.04 | 4.08 |
| Float64 + Delta | 10.87 | 4.14 |
| Float64 + ZSTD (about production codec) | 12.00 | 3.75 |
| Decimal(18,6) + T64 | 12.44 | 3.62 |
| Decimal(38,6) + ZSTD | 11.29 | 6.82 |

On a realistic universe `Decimal(18,6) + Delta` beats `Float64 + Delta` by only
about 6%. Note the `Decimal(38,6)` line: it shows the best ratio (6.82) yet a
worse bytes/row (11.29), because a 16-byte type inflates uncompressed size. Ratio
is misleading; bytes/row is the truth.

## Operational blockers for the Decimal method on this table

1. True decimal precision across all symbols is 6 (sub-cent warrant/penny prices
   such as 0.000001), so the lossless scale is 6, not 4.
2. `firstrate.stocks` contains corrupt prints up to 580 trillion dollars (for
   example `ASTI` in 2007 and `SONG` in 2022) and sentinel values near 9.99e6.
   `Decimal64(18,6)` overflows and throws (error 407) on these, so a migration
   INSERT fails unless the data is cleaned or winsorized first.
3. `Decimal(38,6)` (Decimal128, 16 bytes) can hold the garbage, but `Delta`,
   `DoubleDelta`, and `T64` only work on types of 1, 2, 4, or 8 bytes. So the wide
   type that survives the garbage forfeits the very codec that makes Decimal win.

## Recommended schema (stocks)

If the table is cleaned of garbage prints first:

```sql
symbol  LowCardinality(String)            CODEC(ZSTD(12)),
ts      DateTime64(3, 'America/New_York') CODEC(DoubleDelta, ZSTD(12)),
open    Decimal(18,6) CODEC(Delta, ZSTD(12)),
high    Decimal(18,6) CODEC(Delta, ZSTD(12)),
low     Decimal(18,6) CODEC(Delta, ZSTD(12)),
close   Decimal(18,6) CODEC(Delta, ZSTD(12)),
volume  UInt32        CODEC(T64, ZSTD(12))
```

If the data is not cleaned, or the symbol universe is mixed and high-priced, keep
`Float64 CODEC(Delta, ZSTD)` for OHLC. It is robust, has no overflow risk, and
captures roughly 90% of the benefit (10.87 vs 10.24 bytes/row in the realistic
test). The Decimal step is a conditional refinement, not a default.

---

# Summary of decisions

| Column role | Crypto (coinmetrics) | Stocks (firstrate) |
|---|---|---|
| symbol | LowCardinality, ZSTD | LowCardinality, ZSTD |
| ts | DoubleDelta, ZSTD | DoubleDelta, ZSTD |
| OHLC prices | Float64, Delta, ZSTD | Decimal(18,6) Delta ZSTD if cleaned, else Float64 Delta ZSTD |
| volume | Float32, ZSTD (lossy ~7 sf) or Float64 ZSTD (lossless) | UInt32, T64, ZSTD (lossless, integer) |
| trades_count | UInt32, T64, ZSTD | not present |

Key cross-cutting rules: `Delta` for trending prices, `T64` for bounded integers,
`DoubleDelta` for timestamps, avoid `Gorilla`/`FPC` on OHLC, and target bytes/row
rather than compression ratio.

# Data-quality note (separate from compression)

`firstrate.stocks` contains physically impossible prints (up to 580 trillion
dollars) and sentinel values near 9.99e6, plus sub-0.0001 dollar values. About
10.5 million rows have a close above 100,000 dollars across 111 symbols, only
some of which are legitimately high-priced. These outliers will distort returns,
volatility, and drawdown calculations far more than they cost in storage, and they
block the Decimal storage path. A validation/winsorization pass is recommended
before using this table for research or migrating it.

# Reproducing

Scripts and raw outputs from this study live outside the repository under
`/tmp/ch_compression_benchmark/` (`run_benchmark.py`, `decimal_test.py`,
`REPORT.md`). They connect with the read-only user for source reads and the
`hyperliquid`-scoped write user for the throwaway `zz_` test tables, all of which
were dropped at the end.
