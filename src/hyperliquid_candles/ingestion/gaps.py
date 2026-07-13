"""Gap detection SQL and parsing for 1-minute candle coverage.

Two consumers use this module:

- The quality report calls :func:`gap_query` to surface *all* internal gaps for
  human review, including unrecoverable historical ones.
- The ingestion cycle calls :func:`recoverable_gaps_query` plus
  :func:`parse_gap_rows` to find only the gaps that still fall inside
  Hyperliquid's REST horizon, so it can refetch and repair them.

A "gap" is one or more absent 1-minute slots between two stored candles for the
same symbol. Hyperliquid's public API documentation does not promise that a
candle exists for every no-trade minute. A detected gap is therefore a repair
candidate, not proof of an ingestion failure. Refetching its boundary window is
safe because writes are idempotent; a source-level empty minute may remain in
subsequent reports.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CandleGap:
    """One recoverable gap window for a single symbol.

    Attributes
    ----------
    symbol:
        Hyperliquid perpetual coin name.
    gap_after_ms:
        ``open_time`` of the stored candle immediately *before* the gap, in Unix
        epoch milliseconds. Used as the inclusive refetch window start; refetching
        this boundary candle is harmless because inserts are idempotent.
    gap_before_ms:
        ``open_time`` of the stored candle immediately *after* the gap, used as
        the inclusive refetch window end.
    missing_minutes:
        Count of absent 1-minute candles strictly between the two boundaries.
    """

    symbol: str
    gap_after_ms: int
    gap_before_ms: int
    missing_minutes: int


def gap_query(database: str) -> str:
    """Return SQL that finds all internal missing 1-minute candle runs."""
    return f"""
SELECT
    symbol,
    prev_open AS gap_after,
    open_time AS gap_before,
    (toUnixTimestamp64Milli(open_time)
      - toUnixTimestamp64Milli(prev_open)) / 60000 - 1 AS missing_minutes
FROM
(
    SELECT
        symbol,
        open_time,
        lagInFrame(open_time) OVER
            (PARTITION BY symbol ORDER BY open_time) AS prev_open
    FROM {database}.candles_1m FINAL
)
WHERE prev_open != toDateTime64(0, 3)
  AND (toUnixTimestamp64Milli(open_time)
       - toUnixTimestamp64Milli(prev_open)) > 60000
ORDER BY symbol, gap_after
"""


def recoverable_gaps_query(database: str, horizon_floor_ms: int) -> str:
    """Return SQL for gaps whose missing candles are still inside the REST horizon.

    The window function scan is bounded to ``open_time >= horizon_floor`` so the
    ``FINAL`` read only touches recent partitions instead of the whole table.
    Gaps older than the horizon are excluded because Hyperliquid REST can no
    longer serve those candles, so attempting to refetch them would waste request
    budget on data that is permanently unavailable.

    Parameters
    ----------
    database:
        Target database name.
    horizon_floor_ms:
        Oldest recoverable ``open_time`` in Unix epoch milliseconds, typically
        the first open in the configured recent-candle window.

    Returns
    -------
    str
        SQL returning ``(symbol, gap_after, gap_before, missing_minutes)`` rows,
        where the boundary timestamps are ClickHouse ``DateTime64`` values.
    """
    horizon_floor_seconds = horizon_floor_ms / 1000
    return f"""
SELECT
    symbol,
    prev_open AS gap_after,
    open_time AS gap_before,
    (toUnixTimestamp64Milli(open_time)
      - toUnixTimestamp64Milli(prev_open)) / 60000 - 1 AS missing_minutes
FROM
(
    SELECT
        symbol,
        open_time,
        lagInFrame(open_time) OVER
            (PARTITION BY symbol ORDER BY open_time) AS prev_open
    FROM {database}.candles_1m FINAL
    WHERE open_time >= toDateTime64({horizon_floor_seconds}, 3, 'UTC')
)
WHERE prev_open != toDateTime64(0, 3)
  AND (toUnixTimestamp64Milli(open_time)
       - toUnixTimestamp64Milli(prev_open)) > 60000
ORDER BY symbol, gap_after
"""


def parse_gap_rows(
    result_rows: list[tuple[object, ...]],
    datetime_to_ms: "object",
) -> list[CandleGap]:
    """Convert raw gap query rows into typed :class:`CandleGap` records.

    Parameters
    ----------
    result_rows:
        Rows as ``(symbol, gap_after, gap_before, missing_minutes)`` where the
        boundary columns are timezone-aware datetimes returned by the driver.
    datetime_to_ms:
        Callable converting a datetime to Unix epoch milliseconds. Injected to
        reuse the single conversion helper that already guards against naive
        datetimes elsewhere in the codebase.

    Returns
    -------
    list[CandleGap]
        One record per gap, with boundary timestamps normalised to milliseconds.
    """
    gaps: list[CandleGap] = []
    for symbol, gap_after, gap_before, missing_minutes in result_rows:
        gaps.append(
            CandleGap(
                symbol=str(symbol),
                gap_after_ms=datetime_to_ms(gap_after),
                gap_before_ms=datetime_to_ms(gap_before),
                missing_minutes=int(missing_minutes),
            )
        )
    return gaps
