"""Columnar ClickHouse insert helpers for parsed candles."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from hyperliquid_candles.hyperliquid.candles import Candle


class InsertClient(Protocol):
    """Minimal ClickHouse insert protocol used by the writer."""

    def insert(
        self,
        table: str,
        data: list[tuple[object, ...]],
        column_names: list[str],
    ) -> object:
        """Insert rows into ClickHouse."""


CANDLE_COLUMNS = [
    "symbol",
    "open_time",
    "close_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trades",
]


def insert_candles(
    client: InsertClient,
    database: str,
    candles: list[Candle],
    batch_max_rows: int,
) -> int:
    """Insert parsed candles in bounded chunks and return rows attempted.

    Rows are sent in slices of at most ``batch_max_rows`` so a single insert call
    never holds an unbounded number of rows in memory or in one HTTP request.
    This matters on a cold start with ``symbols_mode = "all"``, where a naive
    single insert would buffer hundreds of symbols times thousands of candles at
    once.

    Parameters
    ----------
    client:
        ClickHouse insert client.
    database:
        Target database name.
    candles:
        Parsed candles to insert. ClickHouse ``ReplacingMergeTree`` makes repeated
        inserts of the same ``(symbol, open_time)`` key idempotent.
    batch_max_rows:
        Maximum rows per insert call. Must be positive (validated in config).

    Returns
    -------
    int
        Total number of rows sent across all chunks.
    """
    if not candles:
        return 0

    total_inserted = 0
    for chunk_start in range(0, len(candles), batch_max_rows):
        chunk = candles[chunk_start : chunk_start + batch_max_rows]
        rows = [candle_to_insert_row(candle) for candle in chunk]
        client.insert(
            table=f"{database}.candles_1m",
            data=rows,
            column_names=CANDLE_COLUMNS,
        )
        total_inserted += len(rows)

    return total_inserted


def candle_to_insert_row(candle: Candle) -> tuple[object, ...]:
    """Convert a parsed candle to a ClickHouse insert tuple."""
    return (
        candle.symbol,
        _ms_to_datetime(candle.open_time_ms),
        _ms_to_datetime(candle.close_time_ms),
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.volume,
        candle.trades,
    )


def _ms_to_datetime(value_ms: int) -> datetime:
    """Convert Unix epoch milliseconds to a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(value_ms / 1000, tz=UTC)
