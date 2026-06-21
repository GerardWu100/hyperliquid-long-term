"""Initial backfill workflow for symbols that have no stored candles."""

from __future__ import annotations

from hl_candles.hyperliquid.candles import INTERVAL_MS, Candle, CandleSource
from hl_candles.hyperliquid.candles import dedupe_by_symbol_open_time


def build_initial_backfill_rows(
    symbol: str,
    start_ms: int,
    end_ms: int,
    page_limit: int,
    source: CandleSource,
) -> list[Candle]:
    """Fetch all reachable rows for one new symbol using cursor pagination."""
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
        if len(page) < page_limit:
            break

    return dedupe_by_symbol_open_time(fetched_rows)
