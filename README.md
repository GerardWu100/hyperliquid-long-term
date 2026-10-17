# Hyperliquid Long-Term Candles

This service continuously copies one-minute candles for Hyperliquid perpetual
futures into ClickHouse. Hyperliquid's REST API exposes only recent candles, so
leaving the service running builds a longer local history for research.

## What it does

- Finds active perpetual markets and downloads their closed one-minute candles
  from Hyperliquid's `candleSnapshot` endpoint.
- Resumes from the latest candle in ClickHouse, fetches missing data with a
  small overlap, and writes it in bounded batches.
- Checks the recent API window after catch-up and fills any gaps.
- Stores candles in `candles_1m`, keyed by `(symbol, open_time)`, and records
  each collection cycle in `ingestion_runs` and `ingestion_symbol_status`.
- Creates these tables when needed. It does not install, configure, or back up
  ClickHouse, so a reachable server must already exist.

Deep backfill from Hyperliquid's S3 archive, trading, and WebSocket ingestion
are outside the project's scope. Hyperliquid provides about 5,000 recent
one-minute candles—roughly 3.47 days—so long-term history comes from running
this service continuously.

See `GUIDE_OVERVIEW.md` for the data flow and design trade-offs. The detailed
project contract is in `docs/reference/IMPLEMENTATION_PLAN.md`.

## Install

You need a computer that stays on, with [Docker](https://docs.docker.com/engine/install/),
and a ClickHouse server it can reach. No clone and no build: the published
image works on ordinary PCs and servers (amd64) and on Raspberry Pi or ARM
servers (arm64).

First create the settings file. The collector reads ClickHouse credentials
from it; they never go into the image:

```bash
mkdir -p ~/.containers/hyperliquid-long-term
curl -fsSL https://raw.githubusercontent.com/GerardWu100/hyperliquid-long-term/master/.env.example \
  -o ~/.containers/hyperliquid-long-term/.env
```

Edit `~/.containers/hyperliquid-long-term/.env` and fill in the ClickHouse
values. The table below says what the host and port must be.

### Option A: Docker Compose (recommended)

Save this as `docker-compose.yml` in an empty folder:

```yaml
services:
  hyperliquid-candles:
    image: ghcr.io/gerardwu100/hyperliquid-long-term:latest
    container_name: hyperliquid-candles
    restart: unless-stopped
    environment:
      - TZ=America/Toronto
    volumes:
      - ~/.containers/hyperliquid-long-term:/data                # logs, optional config.toml
      - ~/.containers/hyperliquid-long-term/.env:/app/.env:ro    # ClickHouse settings
    networks:
      - single

networks:
  single:
    name: single    # the Docker network ClickHouse is on
```

Then start it from that folder:

```bash
docker compose up -d
```

A [fully commented version](docker-compose.example.yml) of this file is in the
repository too.

### Option B: one `docker run` command

```bash
docker run -d --name hyperliquid-candles --restart unless-stopped \
  --network single \
  -e TZ=America/Toronto \
  -v ~/.containers/hyperliquid-long-term:/data \
  -v ~/.containers/hyperliquid-long-term/.env:/app/.env:ro \
  ghcr.io/gerardwu100/hyperliquid-long-term:latest
```

### Before you start: three things to check

| Check | Why | What to change |
|---|---|---|
| `IVYDB_CLICKHOUSE_HOST` and `IVYDB_CLICKHOUSE_PORT` in `.env` | Inside a container, `localhost` is the container itself | The ClickHouse container name from `docker ps`, and its internal HTTP port (`8123`), not the host-published port |
| `docker network inspect single` lists the ClickHouse container | The collector finds ClickHouse by name only on a shared network | Rename `single` in both places to the network ClickHouse is on, or add ClickHouse to `single` |
| The database exists and the user can write to it | The collector creates tables but not the database | Run the `CREATE DATABASE` command in [Setup](#setup), and grant the user `SELECT`, `INSERT`, `CREATE TABLE`, and `ALTER TABLE` on it |

Then run `docker logs -f hyperliquid-candles`. A working start shows the first
collection cycle; `ClickHouse not ready` lines mean the host, port, or network
is wrong.

### Update

| Installed with | Update with |
|---|---|
| Compose | `docker compose pull && docker compose up -d` |
| `docker run` | `docker pull ghcr.io/gerardwu100/hyperliquid-long-term:latest`, then `docker rm -f hyperliquid-candles`, then the same `docker run` command again |

The candles live in ClickHouse and the logs in the folder, not in the
container, so updating never loses them. After an update the collector resumes
from the latest stored candle. Nothing updates on its own.

**Pin a version** if you would rather update on your own schedule: replace
`latest` with `sha-<commit>` (an exact build), or with a release such as `1`,
`1.0`, or `1.0.0` once one is tagged.

## Requirements

To run from a checkout instead of the published image:

- Python 3.13
- A ClickHouse server reachable through its HTTP interface
- The environment variables listed below. See `.env.example`; copy it to
  `.env`, which is gitignored:
  - `IVYDB_CLICKHOUSE_HOST`, `IVYDB_CLICKHOUSE_PORT`: ClickHouse host and port
  - `IVYDB_CLICKHOUSE_USERNAME`, `IVYDB_CLICKHOUSE_PASSWORD`: credentials for
    a user with `SELECT`, `INSERT`, `CREATE TABLE`, and `ALTER TABLE` access to
    `IVYDB_CLICKHOUSE_DATABASE`
  - `IVYDB_CLICKHOUSE_SECURE`: set to `true` only when the HTTP interface uses
    TLS
  - `IVYDB_CLICKHOUSE_DATABASE`: database for the Hyperliquid tables
- Docker, if you want to run the service in a container

## Setup

```bash
uv sync
cp .env.example .env
```

`uv sync` also installs the `dev` group (pytest, ruff, matplotlib for
`blog/generate_charts.py`). The Docker image installs only runtime packages.

Add your ClickHouse connection details to `.env`. Use `config.toml` to change
the polling schedule, symbols, request limits, batch size, and alert thresholds.

If the database does not exist, create it in ClickHouse:

```sql
CREATE DATABASE IF NOT EXISTS hyperliquid
```

## Usage

```bash
uv run hyperliquid-candles              # keep collecting; catch up immediately
uv run hyperliquid-candles-run-once     # collect one cycle, for cron/systemd
uv run hyperliquid-candles-quality      # report freshness, gaps, and duplicates
```

To build and run the service from this checkout with Docker instead of the
published image:

```bash
cp .env.example .env       # edit ClickHouse settings in .env
docker compose up --build
```

To update a deployment, run `./update.sh`. It pulls committed code and rebuilds
only when the pull found something new. Use `--force` to rebuild anyway—for
example, after editing `.env`, when a base image changed under an unchanged
Dockerfile, or when you want to recreate a container. If nothing is running,
the script starts the deployment either way. It works from any directory and
stops at the first failure, so a failed pull cannot silently redeploy old code.

Docker mounts the root `.env` inside the container and writes logs to
`~/.containers/hyperliquid-candles`. Inside the container,
`IVYDB_CLICKHOUSE_HOST` must be the ClickHouse service or container name on the
shared Docker network `single`. Do not use `localhost` or the host-published
port there.

## Configuration

### Notifications

Set `APPRISE_URL=apprise://apprise-api:8000/YOUR_KEY` in the ignored root `.env`.
The Apprise library calls `http://apprise-api:8000/notify/YOUR_KEY`; do not insert
`/notify/` in the `apprise://` URL. An empty value disables notifications.
The mounted `.env` value takes precedence over the process environment.

The container service sends a startup notice before loading collection settings,
then forwards operational warnings and errors: ClickHouse readiness failures,
failed cycles or symbols, rejected candle data, gap detection failures, and
metadata write failures. Fatal initialization failures also send an alert.
Warning and error notices each have a fifteen-minute cooldown per process to
limit floods; restart notices are always attempted. Notifications refer to the
container logs and omit formatted error details to protect credentials.

Delivery runs in a daemon worker with a bounded queue and five-second connection
and read timeouts, so an unavailable notification server cannot block ingestion.
Delivery failures are logged without secrets. The existing standalone quality
report remains a manual command; this does not add a freshness watchdog.
Process alerts cannot report host power loss or a forced container kill.

Both this collector and `apprise-api` must join the external Docker network
`single`, and the API must listen on internal port `8000`. The collector already
joins that network. Recreate the collector with `./update.sh --force` (published image:
`docker compose up -d --force-recreate`) after changing `.env` so startup
delivery and the new URL take effect.

`config.toml` controls the polling interval, overlap window, API time horizon
and request budget, symbol selection (`symbols_mode`: `all` or `allowlist`),
batch size, log level, and freshness alerts
(`alert_warn_min`, `alert_serious_min`, `alert_urgent_min`, and
`alert_critical_min`). Every option is documented in the file.

To change settings in Docker, put a `config.toml` in the folder mounted at
`/data` with only the keys you want to change. Its keys are merged over the
built-in file, so every key you leave out keeps its built-in value. An unknown
key (for example a typo) stops startup with an error.

In `allowlist` mode, a symbol that Hyperliquid does not list as an active perp
(delisted or misspelled) is skipped with a warning alert. The other symbols are
still collected, the cycle is marked `partial`, and the skipped symbol gets a
`skipped` row in `ingestion_symbol_status`.

## Layout

```text
src/hyperliquid_candles/   service code: API client, ingestion, storage, and quality checks
scripts/                   small wrappers for cron, systemd, and deployment updates
tests/unit/                fast tests for core logic and fake-source ingestion
docs/reference/            IMPLEMENTATION_PLAN.md, the detailed project contract
```

## Output

Candles are stored in the ClickHouse table `candles_1m`. Collection-cycle data
is stored in `ingestion_runs` and `ingestion_symbol_status`. In
`ingestion_runs`, `error` lists failed and skipped symbols, or the exception
when a whole cycle fails (for example the Hyperliquid `meta` request); it is
empty for a fully successful cycle.

Logs go to `logs/`, or to the folder mounted at `/data/logs` when running with
Docker. Each log file rolls over at 10 MB and keeps 5 old copies, and only the
30 most recent process runs stay in the folder. httpx request lines are hidden
unless `log_level = "DEBUG"`.

See `COMPRESSION_BENCHMARK.md` for the compression choices used by the candle
table.
