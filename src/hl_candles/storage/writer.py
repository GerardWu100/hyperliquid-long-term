"""Columnar ClickHouse insert helpers for parsed candles."""

from __future__ import annotations

from collections.abc import Protocol
from datetime import UTC, datetime

from hl_candles.hyperliquid.candles import Candle


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


def insert_candles(client: InsertClient, database: str, candles: list[Candle]) -> int:
    """Insert parsed candles and return the number of rows attempted."""
    if not candles:
        return 0

    rows = [candle_to_insert_row(candle) for candle in candles]
    client.insert(
        table=f"{database}.candles_1m",
        data=rows,
        column_names=CANDLE_COLUMNS,
    )
    return len(rows)


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
