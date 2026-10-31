# Trail notes

## Decisions

- 2026-06-21: Stored ClickHouse rows (`max(open_time)` per symbol) are the only ingestion state. No watermark table or local state file, so crash and reboot recovery use the same path. Do not add a second source of truth.
- 2026-06-21: Each cycle re-fetches a small overlap into `ReplacingMergeTree(inserted_at)`. Duplicate raw rows are expected until merges; readers dedupe with `argMax(..., inserted_at)` or `FINAL`. Do not run `OPTIMIZE ... FINAL` every cycle without measuring its cost.
- 2026-06-21: Freshness alerts cover only currently active symbols. Delisted symbols keep their history but raise no staleness alerts.
- 2026-06-24: Candle storage is lossless: Float64 prices with Delta + ZSTD(12), Float64 volume with plain ZSTD(12) (see COMPRESSION_BENCHMARK.md). Never downcast or round prices or volume. The schema uses `CREATE TABLE IF NOT EXISTS` and never alters existing tables.
- 2026-07-13: The limiter reserves the full estimated candle weight (20 + 1 per 60 requested minutes) before each HTTP attempt, not after the response, to stop cold-start bursts.
- 2026-10-17: An allowlisted symbol that is not an active perp is skipped (status `skipped`, cycle `partial`) instead of failing the whole cycle.

## Assumptions

- 2026-10-17: The rate budget assumes this collector is the only user of its IP's 1200 weight/min limit. Check what else runs on the host's IP before raising `weight_budget_per_min` (now 1000).

## Gotchas

- 2026-06-22: `candleSnapshot` is newest-anchored: a window wider than the horizon (about 5,186 candles back from now) silently drops the oldest candles. Page backward by `endTime`. Downtime longer than about 3.47 days (5,000 candles span 4,999 minutes) loses data for good.
- 2026-07-13: A missing minute is only a repair candidate. Hyperliquid does not promise a candle for every no-trade minute, so a gap can remain after a successful refetch. Zero-trade candles with a carried price are valid source data.
- 2026-10-17: Sweep time is about symbols x 20 / budget minutes and must stay under `poll_interval_sec` (limit near 1,500 symbols at 1000/min and 30 minutes).
- 2026-10-17: The `paths` filter in `.github/workflows/publish-image.yml` lists only files that enter the image. Keep it in sync with the Dockerfile, or image changes will not publish.

## Failed approaches

- 2026-06-22: Forward pagination by `startTime`. *Disproved* by live API probes: the endpoint is newest-anchored, so it never reaches older candles.
- 2026-06-24: Gorilla codec for prices and volume. *Disproved* by the compression benchmark: Delta wins for prices, plain ZSTD for fractional volume.

## Open blockers

- 2026-09-21: Live Apprise notification delivery from the container is unverified (the agent had no Docker socket access). Next check: from a shell with Docker access, recreate the collector, confirm `apprise-api` is reachable on network `single`, and confirm the startup notice arrives.

## Untried ideas

- 2026-10-17: Cap the limiter `capacity` so capacity + budget <= 1200. It gives a hard per-IP guarantee in the first minute of a cold start, where a full bucket can spend up to 2000; cost is a one-line config change plus a test.
