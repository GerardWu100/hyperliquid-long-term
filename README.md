# Hyperliquid Long-Term Candles

This project ingests Hyperliquid perpetual 1-minute candles into an existing
ClickHouse database. It is built for long-running research storage: the service
polls all active perpetual symbols, stores closed candles only, re-fetches a
small overlap window, and relies on ClickHouse `ReplacingMergeTree` keys for
idempotent inserts.

ClickHouse is external to this repository. The project creates logical database
objects if they are missing, but it does not install, configure, containerize, or
back up ClickHouse.

## What It Builds

- `hyperliquid-candles`: long-running scheduler with immediate catch-up on startup.
- `hyperliquid-candles-run-once`: one ingestion cycle for cron or `systemd` timers.
- `hyperliquid-candles-quality`: data quality report for active-symbol freshness, gaps,
  duplicates, parts, and recent run status.
- `candles_1m`: raw idempotent ClickHouse candle table.
- `candles_1m_clean`: duplicate-safe research view using `argMax`.
- `ingestion_runs` and `ingestion_symbol_status`: cycle metadata tables.

## Setup

```bash
uv sync
cp .env.example .env
```

Edit `.env` with the reachable ClickHouse HTTP host, port, user, password, TLS
flag, and database. Edit `config.toml` for cadence, symbol allowlists, rate
budget, and alert thresholds.

## Run

One cycle:

```bash
uv run hyperliquid-candles-run-once
```

Long-running service:

```bash
uv run hyperliquid-candles
```

Quality report:

```bash
uv run hyperliquid-candles-quality
```

Docker service:

```bash
docker compose up --build
```

Inside a bridged Docker container, `localhost` means the ingestion container
itself. Use the ClickHouse service name on a shared external Docker network, or
use `host.docker.internal` with the `extra_hosts` entry in `docker-compose.yml`.

## Verification

```bash
uv run pytest
uv run ruff check .
```

The unit tests cover configuration parsing, 1-minute window arithmetic, candle
payload parsing, initial backfill pagination, and incremental overlap windows.

## Scope

The service uses Hyperliquid REST `candleSnapshot` only. REST can only recover
roughly the most recent 5000 one-minute candles per symbol, so true long-term
history is accumulated by keeping this service running. Deep historical S3
archive ingestion, trading, WebSocket ingestion, and ClickHouse operations are
out of scope.
