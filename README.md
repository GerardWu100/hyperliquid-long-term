# Hyperliquid Long-Term Candles

Ingests Hyperliquid perpetual-futures 1-minute candles into an existing
ClickHouse database, continuously, so long-term history is available for
research even though Hyperliquid's REST API only serves a limited recent
window.

## What it does

- Discovers active perpetual symbols and fetches closed 1-minute candles from
  Hyperliquid's REST `candleSnapshot` endpoint.
- Each cycle derives its state from ClickHouse itself (no separate watermark
  table): it reads each symbol's latest stored candle, computes the missing
  range plus a small overlap, fetches it, and inserts it in bounded chunks.
- Runs a gap-backfill pass after catch-up, refetching candidate missing
  minutes inside the available REST window.
- Writes to `candles_1m` (raw candle table, `ReplacingMergeTree`, keyed on
  `(symbol, open_time)`, safe to re-insert) plus `ingestion_runs` and
  `ingestion_symbol_status` (cycle metadata).
- Does not manage ClickHouse itself — it creates missing logical tables but
  does not install, configure, or back up the ClickHouse server. ClickHouse
  is assumed to already be reachable.
- Out of scope: deep historical backfill from Hyperliquid's S3 archive,
  trading, and WebSocket ingestion. Hyperliquid documents the most recent
  5,000 one-minute candles as available (about 3.47 days); long-term history
  is built up by keeping the service running continuously, not by backfill.

See `GUIDE_OVERVIEW.md` for the full data-flow diagram and design tradeoffs,
and `docs/reference/IMPLEMENTATION_PLAN.md` for the detailed project
contract.

## Requirements

- Python 3.13
- A reachable ClickHouse server (HTTP interface). This project does not run
  or manage ClickHouse.
- Environment variables (see `.env.example`, copy to `.env`, which is
  gitignored):
  - `IVYDB_CLICKHOUSE_HOST`, `IVYDB_CLICKHOUSE_PORT` — ClickHouse HTTP host
    and port.
  - `IVYDB_CLICKHOUSE_USERNAME`, `IVYDB_CLICKHOUSE_PASSWORD` — credentials
    for a user scoped to `IVYDB_CLICKHOUSE_DATABASE` with `SELECT`,
    `INSERT`, `CREATE TABLE`, `ALTER TABLE` grants.
  - `IVYDB_CLICKHOUSE_SECURE` — `true` only when the ClickHouse HTTP
    interface uses TLS.
  - `IVYDB_CLICKHOUSE_DATABASE` — target database for the hyperliquid
    tables.
- Optional: Docker, to run the service as a container instead of directly
  with `uv run`.

## Setup

```bash
uv sync
cp .env.example .env
```

Edit `.env` with the ClickHouse connection details, and `config.toml` for
poll cadence, symbol selection, rate budget, and alert thresholds.

To create the database, run against the ClickHouse server:

```sql
CREATE DATABASE IF NOT EXISTS hyperliquid
```

## Usage

```bash
uv run hyperliquid-candles              # long-running scheduler with immediate catch-up
uv run hyperliquid-candles-run-once     # one ingestion cycle, for cron/systemd timers
uv run hyperliquid-candles-quality      # data-quality report: freshness, gaps, duplicates
```

Docker:

```bash
cp .env.example .env       # edit ClickHouse settings in the repo-root .env
docker compose up --build
```

`docker-compose.yml` bind-mounts the repo-root `.env` into the container and
stores logs under `~/.containers/hyperliquid-candles` (created
automatically). Inside the container, `IVYDB_CLICKHOUSE_HOST` must be the
ClickHouse service/container name on the shared Docker network `single`
(not `localhost`, and not the host-published port).

## Configuration

`config.toml` controls poll interval, overlap window, REST horizon and rate
budget, symbol selection (`symbols_mode`: `all` or `allowlist`), batch
insert size, log level, and freshness alert thresholds
(`alert_warn_min` / `alert_serious_min` / `alert_urgent_min` /
`alert_critical_min`). Every key is commented in the file.

## Layout

```text
src/hyperliquid_candles/   importable service package (app, hyperliquid client, ingestion, storage, quality)
scripts/                   thin run-once and quality-report wrappers, for cron/systemd
tests/unit/                fast tests for pure logic and fake-source ingestion behavior
docs/reference/            IMPLEMENTATION_PLAN.md, the detailed project contract
```

## Output

Candles land in ClickHouse table `candles_1m`; cycle metadata lands in
`ingestion_runs` and `ingestion_symbol_status`. Process logs land under
`logs/` (or `~/.containers/hyperliquid-candles` under Docker). See
`COMPRESSION_BENCHMARK.md` for the measured ClickHouse codec choices behind
the raw table's schema.

## License

All rights reserved. See [LICENSE](LICENSE).
