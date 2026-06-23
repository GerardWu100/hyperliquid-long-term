"""ClickHouse DDL for the Hyperliquid candle ingestion schema."""

from __future__ import annotations

import logging
from typing import Protocol

LOGGER = logging.getLogger(__name__)

# ClickHouse ACCESS_DENIED when a database-scoped user lacks CREATE DATABASE.
_CREATE_DATABASE_ACCESS_DENIED_CODE = 497


class QueryExecutor(Protocol):
    """Minimal protocol for ClickHouse clients that execute SQL statements."""

    def command(self, query: str) -> object:
        """Execute a ClickHouse command statement."""


def create_schema(client: QueryExecutor, database: str) -> None:
    """Create database, tables, and clean read view if they are missing.

    Database-scoped ClickHouse users often lack ``CREATE DATABASE`` even when the
    target database already exists. In that case we skip database creation and
    continue with table/view DDL, which only needs grants on ``{database}.*``.
    """
    _ensure_database(client=client, database=database)
    for ddl in schema_statements(database):
        client.command(ddl)


def _ensure_database(client: QueryExecutor, database: str) -> None:
    """Create the target database when permitted; otherwise assume it exists."""
    try:
        client.command(f"CREATE DATABASE IF NOT EXISTS {database}")
    except Exception as exc:
        if not _is_create_database_access_denied(exc):
            raise
        LOGGER.info(
            "Skipping CREATE DATABASE for %s; user lacks CREATE DATABASE grant "
            "(database must already exist)",
            database,
        )


def _is_create_database_access_denied(exc: Exception) -> bool:
    """Return True when ClickHouse rejected CREATE DATABASE for missing grants."""
    code = getattr(exc, "code", None)
    if code == _CREATE_DATABASE_ACCESS_DENIED_CODE:
        return True

    message = str(exc).lower()
    return "create database" in message and (
        "access_denied" in message or "not enough privileges" in message
    )


def schema_statements(database: str) -> list[str]:
    """Return table/view DDL statements in dependency order."""
    return [
        _candles_ddl(database),
        _ingestion_runs_ddl(database),
        _ingestion_symbol_status_ddl(database),
        _clean_view_ddl(database),
    ]


def _candles_ddl(database: str) -> str:
    """Return DDL for raw 1-minute candle rows."""
    return f"""
CREATE TABLE IF NOT EXISTS {database}.candles_1m
(
    symbol      LowCardinality(String),
    open_time   DateTime64(3, 'UTC')  CODEC(DoubleDelta, ZSTD(12)),
    close_time  DateTime64(3, 'UTC')  CODEC(DoubleDelta, ZSTD(12)),
    open        Float64               CODEC(Gorilla, ZSTD(12)),
    high        Float64               CODEC(Gorilla, ZSTD(12)),
    low         Float64               CODEC(Gorilla, ZSTD(12)),
    close       Float64               CODEC(Gorilla, ZSTD(12)),
    volume      Float64               CODEC(Gorilla, ZSTD(12)),
    trades      UInt32                CODEC(T64, ZSTD(12)),
    inserted_at DateTime64(3, 'UTC')  DEFAULT now64(3) CODEC(DoubleDelta, ZSTD(12))
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(open_time)
ORDER BY (symbol, open_time)
SETTINGS index_granularity = 8192
"""


def _ingestion_runs_ddl(database: str) -> str:
    """Return DDL for per-cycle summary rows."""
    return f"""
CREATE TABLE IF NOT EXISTS {database}.ingestion_runs
(
    run_id            UUID,
    mode              Enum8('initial' = 1, 'incremental' = 2),
    started_at        DateTime64(3, 'UTC'),
    finished_at       Nullable(DateTime64(3, 'UTC')),
    symbols_total     UInt32,
    symbols_ok        UInt32,
    symbols_failed    UInt32,
    candles_inserted  UInt64,
    status            Enum8('success' = 1, 'partial' = 2, 'failed' = 3),
    error             String DEFAULT ''
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(started_at)
ORDER BY (started_at)
"""


def _ingestion_symbol_status_ddl(database: str) -> str:
    """Return DDL for per-symbol outcome rows."""
    return f"""
CREATE TABLE IF NOT EXISTS {database}.ingestion_symbol_status
(
    run_id          UUID,
    symbol          LowCardinality(String),
    mode            Enum8('initial' = 1, 'incremental' = 2),
    requested_start DateTime64(3, 'UTC'),
    requested_end   DateTime64(3, 'UTC'),
    effective_start DateTime64(3, 'UTC'),
    rows_fetched    UInt32,
    rows_inserted   UInt32,
    status          Enum8('success' = 1, 'skipped' = 2, 'failed' = 3),
    error           String DEFAULT '',
    recorded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(recorded_at)
ORDER BY (run_id, symbol)
"""


def _clean_view_ddl(database: str) -> str:
    """Return DDL for the duplicate-safe research read view."""
    return f"""
CREATE VIEW IF NOT EXISTS {database}.candles_1m_clean AS
SELECT
    symbol,
    open_time,
    argMax(open, inserted_at) AS open,
    argMax(high, inserted_at) AS high,
    argMax(low, inserted_at) AS low,
    argMax(close, inserted_at) AS close,
    argMax(volume, inserted_at) AS volume,
    argMax(trades, inserted_at) AS trades,
    max(close_time) AS close_time
FROM {database}.candles_1m
GROUP BY symbol, open_time
"""
