# Outline proposal

## Project scan summary

- Project archetype candidate: `data-pipeline`.
- Supporting evidence from files: `app.py` orchestrates initial backfill, incremental overlap, and recoverable-gap repair; `storage/schema.py` defines idempotent ClickHouse tables; `quality/checks.py` measures freshness, duplicates, gaps, daily coverage, parts, and run status; `COMPRESSION_BENCHMARK.md` records measured codec decisions.

## Blueprint selection

- Selected blueprint: data pipeline.
- Why this blueprint fits this project: the central problem is retaining reliable one-minute market history under a short REST recovery horizon, not pricing or trading.
- Planned section order:
  1. A short API horizon turns collection into an operational problem.
  2. ClickHouse rows are the watermark.
  3. The three-phase ingestion cycle.
  4. Why fetching walks backward.
  5. Idempotency, overlap, and read-time deduplication.
  6. Quality thresholds before the history becomes unrecoverable.
  7. Compression evidence and schema choices.
  8. What this design does and does not guarantee.

## Planned equations

1. Last closed candle open time:
   - Purpose: exclude the still-forming minute.
   - Symbols: $t$ is current Unix time in milliseconds, $\Delta=60{,}000$ ms is one minute, and $t_{\mathrm{closed}}$ is the latest closed candle's open time.
   - Delimiter: display.
2. Inclusive horizon floor and incremental start time:
   - Purpose: explain why $H$ candle opens span $H-1$ intervals, then apply the bounded overlap and REST floor.
   - Symbols: $w_s$ is the stored watermark for symbol $s$, $k$ is the overlap in candles, $H$ is the configured candle-slot count, and $t_{\mathrm{start},s}$ is the next request start.
   - Delimiter: display.
3. Compression metrics:
   - Purpose: distinguish compression ratio from the decision metric, bytes per row.
   - Symbols: $B_u$ is uncompressed bytes, $B_c$ is compressed bytes, $N$ is rows, $R$ is compression ratio, and $b$ is compressed bytes per row.
   - Delimiter: display.
4. Request weight and observed slot coverage:
   - Purpose: explain pre-request rate-limit reservation and distinguish absent stored slots from proven source failures.
   - Symbols: $M$ is returned candles, $S$ is requested slots, $W$ is request weight, $U$ is unique stored keys, $E$ is expected minute slots, and $Q=U/E$ is the observed slot ratio.
   - Delimiter: display.

## Planned code excerpts

1. File: `src/hyperliquid_candles/ingestion/incremental.py`
   - Function/block: incremental window construction.
   - Why include this excerpt: it captures the restart-safe overlap and horizon clamp in a few lines.
2. File: `src/hyperliquid_candles/ingestion/fetch.py`
   - Function/block: backward movement of `endTime`.
   - Why include this excerpt: newest-anchored pagination is the least obvious API constraint in the project.

## Planned technical graphs

1. Graph type: threshold timeline.
   - Source: generated from `config.toml` (`60`, `720`, `2880`, `4320` alert minutes and the 4,999-minute span of 5,000 inclusive slots).
   - Expected takeaway: the critical alert fires 679 minutes before the configured 5,000-slot floor, leaving a narrow repair margin that is not a source-retention guarantee.
2. Graph type: horizontal bar comparison.
   - Source: frozen values from the measured crypto benchmark in `COMPRESSION_BENCHMARK.md`.
   - Expected takeaway: the chosen lossless codec mix reduced sample bytes per row from 19.55 to 16.14, while Gorilla and LZ4 were substantially larger; these are benchmark data, not live Hyperliquid table results.

## Risks, gaps, and assumptions

- Data gaps: the repository contains no frozen production ingestion run or live ClickHouse quality report, so the article will not claim collected row counts, uptime, throughput, or production compression.
- Assumptions: the documented roughly 5,000-candle REST horizon is treated as an operational bound, not a service-level guarantee; the compression benchmark uses a representative perpetual-futures dataset rather than the target table itself.
- Validation checks to run before final draft: regenerate both charts; run the blog validator on both languages; verify every referenced image exists; compare frontmatter and protected blocks; run the project unit tests and Ruff.
- Deployment note: canonical files remain under `hyperliquid-long-term/blog/`. The user explicitly prohibited copying to or touching `~/projects/website`, so this task stops after committing and pushing the project-local package.
