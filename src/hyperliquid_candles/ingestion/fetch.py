"""Paginated candle-window fetching for Hyperliquid's newest-anchored REST API.

This module owns the single fetch primitive used by every ingestion path:
initial backfill of new symbols, incremental catch-up of stored symbols, and
gap repair. Centralising it removes the previous contradiction where backfill
paginated but incremental issued one unchecked request.

Empirical Hyperliquid `candleSnapshot` behaviour (probed against the live API)
-----------------------------------------------------------------------------
- The REST horizon is anchored to *now*: only roughly the most recent ~5186
  one-minute candles are available. A request whose window reaches further back
  than that simply returns the slice that still falls inside the horizon.
- Truncation is *newest-anchored*: when the requested window is wider than what
  is available, the response keeps the candles nearest ``endTime`` and silently
  drops the oldest overflow.

The second point is why paging *forward* by start time cannot work: requesting
``[start, end]`` for a too-wide window returns the newest chunk near ``end``,
never the older candles near ``start``. The correct strategy is to page
*backward* by ``endTime`` so each request peels off the next-oldest chunk.
"""

from __future__ import annotations

from hyperliquid_candles.hyperliquid.candles import (
    INTERVAL_MS,
    Candle,
    CandleSource,
    dedupe_by_symbol_open_time,
)


def fetch_candle_window(
    symbol: str,
    start_ms: int,
    end_ms: int,
    source: CandleSource,
    interval_ms: int = INTERVAL_MS,
) -> list[Candle]:
    """Fetch every reachable candle in the inclusive ``[start_ms, end_ms]`` window.

    Pages backward by ``endTime`` to match Hyperliquid's newest-anchored
    truncation. For any window that fits inside the REST horizon (the common
    case, since callers clamp to it) this terminates after a single request,
    because the first page already reaches ``start_ms``.

    Parameters
    ----------
    symbol:
        Hyperliquid perpetual coin name, for example ``"BTC"``.
    start_ms, end_ms:
        Inclusive window bounds in Unix epoch milliseconds (UTC).
    source:
        Any object implementing ``fetch_candles(symbol, start_ms, end_ms)``.
    interval_ms:
        Candle width in milliseconds. Defaults to one minute and is used only to
        step the cursor one candle past the oldest row already retrieved.

    Returns
    -------
    list[Candle]
        Deduplicated candles sorted by ``(symbol, open_time_ms)``. May be empty
        when the window lies entirely outside the REST horizon.

    Notes
    -----
    Termination relies on cursor progress, never on page size. The loop stops
    when a page is empty, when the oldest returned candle reaches ``start_ms``,
    or when the cursor fails to move (a defensive guard against an API that
    ignores ``endTime`` and would otherwise spin forever).
    """
    fetched_rows: list[Candle] = []
    cursor_end_ms = end_ms

    while cursor_end_ms >= start_ms:
        page = source.fetch_candles(
            symbol=symbol, start_ms=start_ms, end_ms=cursor_end_ms
        )
        if not page:
            break

        fetched_rows.extend(page)
        oldest_open_ms = min(candle.open_time_ms for candle in page)

        # The page already reaches the requested start, so the window is covered.
        if oldest_open_ms <= start_ms:
            break

        # Step the cursor to just before the oldest candle we just received so
        # the next request peels off the next-older chunk.
        next_cursor_end_ms = oldest_open_ms - interval_ms

        # Defensive guard: if the cursor did not move backward, the API is not
        # honouring endTime as expected. Stop rather than loop forever.
        if next_cursor_end_ms >= cursor_end_ms:
            break

        cursor_end_ms = next_cursor_end_ms

    return dedupe_by_symbol_open_time(fetched_rows)
