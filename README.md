# Hyperliquid Long-Term Candles

This service continuously copies Hyperliquid perpetual-futures one-minute
candles into ClickHouse. Hyperliquid's REST API only provides recent candles,
so keeping the service running gradually builds a long-term local history for
research.

## What it does

- Finds active perpetual markets and downloads their closed one-minute candles
  from Hyperliquid's `candleSnapshot` endpoint.
- Uses the latest candle already in ClickHouse to find where each market should
  resume. It fetches the missing range with a small overlap and writes the data
  in bounded batches.
- Scans the recent API window after catch-up and fills gaps it finds.
- Stores candles in `candles_1m`, keyed by `(symbol, open_time)`, together with
  cycle information in `ingestion_runs` and `ingestion_symbol_status`.
- Creates these tables when needed, but does not install, configure, or back up
  ClickHouse. A reachable ClickHouse server must already exist.

Deep historical backfill from Hyperliquid's S3 archive, trading, and WebSocket
ingestion are outside this project's scope. Hyperliquid provides about 5,000
recent one-minute candles—roughly 3.47 days—so long-term history comes from
running the service continuously.

See `GUIDE_OVERVIEW.md` for the data flow and design trade-offs. The detailed
project contract is in `docs/reference/IMPLEMENTATION_PLAN.md`.

## Requirements

- Python 3.13
- A ClickHouse server reachable through its HTTP interface
- The following environment variables (see `.env.example`; copy it to `.env`,
  which is gitignored):
  - `IVYDB_CLICKHOUSE_HOST`, `IVYDB_CLICKHOUSE_PORT`: ClickHouse host and port
  - `IVYDB_CLICKHOUSE_USERNAME`, `IVYDB_CLICKHOUSE_PASSWORD`: credentials for
    a user with `SELECT`, `INSERT`, `CREATE TABLE`, and `ALTER TABLE` access to
    `IVYDB_CLICKHOUSE_DATABASE`
  - `IVYDB_CLICKHOUSE_SECURE`: set to `true` only when the HTTP interface uses
    TLS
  - `IVYDB_CLICKHOUSE_DATABASE`: database for the Hyperliquid tables
- Docker is optional and only needed if you want to run the service in a
  container.

## Setup

```bash
uv sync
cp .env.example .env
```

Add your ClickHouse connection details to `.env`. Use `config.toml` to change
the polling schedule, symbols, request limits, batch size, and alert
thresholds.

If the database does not exist yet, create it in ClickHouse:

```sql
CREATE DATABASE IF NOT EXISTS hyperliquid
```

## Usage

```bash
uv run hyperliquid-candles              # keep collecting; catch up immediately
uv run hyperliquid-candles-run-once     # collect one cycle, for cron/systemd
uv run hyperliquid-candles-quality      # report freshness, gaps, and duplicates
```

To run it with Docker:

```bash
cp .env.example .env       # edit ClickHouse settings in .env
docker compose up --build
```

To update a running deployment, run `./scripts/update.sh`. It pulls the
committed code, then stops, rebuilds, and restarts the containers. It works
from any directory and stops at the first failure, so a failed pull cannot
silently redeploy the old code.

Docker mounts the repo-root `.env` inside the container and writes logs to
`~/.containers/hyperliquid-candles`. Inside the container,
`IVYDB_CLICKHOUSE_HOST` must be the ClickHouse service or container name on the
shared Docker network `single`. Do not use `localhost` or the host-published
port there.

## Configuration

`config.toml` controls the polling interval, overlap window, API time horizon
and request budget, symbol selection (`symbols_mode`: `all` or `allowlist`),
batch size, log level, and freshness alerts
(`alert_warn_min`, `alert_serious_min`, `alert_urgent_min`, and
`alert_critical_min`). Every option is documented in the file.

## Layout

```text
src/hyperliquid_candles/   service code: API client, ingestion, storage, and quality checks
scripts/                   small wrappers for cron, systemd, and deployment updates
tests/unit/                fast tests for core logic and fake-source ingestion
docs/reference/            IMPLEMENTATION_PLAN.md, the detailed project contract
```

## Output

Candles are stored in ClickHouse table `candles_1m`. Cycle information is stored
in `ingestion_runs` and `ingestion_symbol_status`. Logs go to `logs/`, or to
`~/.containers/hyperliquid-candles` when running with Docker.

See `COMPRESSION_BENCHMARK.md` for the ClickHouse compression choices used by
the candle table.

## License

All rights reserved. See [LICENSE](LICENSE).
