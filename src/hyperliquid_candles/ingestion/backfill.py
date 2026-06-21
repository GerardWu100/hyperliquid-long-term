"""Initial backfill workflow for symbols that have no stored candles."""

from __future__ import annotations

from hyperliquid_candles.hyperliquid.candles import INTERVAL_MS, Candle, CandleSource
from hyperliquid_candles.hyperliquid.candles import dedupe_by_symbol_open_time


def build_initial_backfill_rows(
    symbol: str,
    start_ms: int,
    end_ms: int,
    source: CandleSource,
) -> list[Candle]:
    """Fetch all reachable rows for one new symbol using cursor pagination.

    Termination relies only on cursor progress, not on a page-size heuristic.
    Hyperliquid's `candleSnapshot` page size is not contractually fixed (the docs
    mention 500-element blocks for time-range responses while the candle endpoint
    notes a 5000-candle horizon), so assuming "a short page means the last page"
    can stop early and silently miss data. Instead we page forward by the newest
    returned open time and stop when the cursor passes `end_ms`, when a page is
    empty, or when the newest open time stops advancing.
    """
    cursor_ms = start_ms
    fetched_rows: list[Candle] = []

    while cursor_ms <= end_ms:
        page = source.fetch_candles(symbol=symbol, start_ms=cursor_ms, end_ms=end_ms)
        if not page:
            break

        fetched_rows.extend(page)
        newest_open_ms = max(candle.open_time_ms for candle in page)
        if newest_open_ms <= cursor_ms:
            break

        cursor_ms = newest_open_ms + INTERVAL_MS

    return dedupe_by_symbol_open_time(fetched_rows)
