"""Gap detection SQL for duplicate-safe 1-minute candle coverage checks."""

from __future__ import annotations


def gap_query(database: str) -> str:
    """Return SQL that finds missing internal 1-minute candle runs."""
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
