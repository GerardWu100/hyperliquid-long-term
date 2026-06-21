"""ClickHouse queries for deriving per-symbol candle watermarks."""

from __future__ import annotations

from collections.abc import Protocol
from datetime import UTC, datetime


class QueryClient(Protocol):
    """Minimal query protocol used by watermark readers."""

    def query(self, query: str) -> object:
        """Run a ClickHouse query and return a result object."""


def query_watermarks_ms(client: QueryClient, database: str) -> dict[str, int | None]:
    """Return latest stored `open_time` per symbol in Unix epoch milliseconds."""
    result = client.query(
        f"""
        SELECT symbol, max(open_time) AS max_open_time
        FROM {database}.candles_1m
        GROUP BY symbol
        """
    )

    watermarks: dict[str, int | None] = {}
    for symbol, max_open_time in result.result_rows:
        if max_open_time is None:
            watermarks[str(symbol)] = None
        else:
            watermarks[str(symbol)] = _datetime_to_ms(max_open_time)
    return watermarks


def _datetime_to_ms(value: datetime) -> int:
    """Convert a ClickHouse datetime value to Unix epoch milliseconds.

    The `candles_1m` column is `DateTime64(3, 'UTC')`, which the driver normally
    returns as a timezone-aware UTC datetime. If a naive datetime is ever
    returned, it is interpreted as UTC rather than the host's local zone;
    otherwise `.timestamp()` would silently shift every watermark by the local
    UTC offset and corrupt the fetch window.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)
