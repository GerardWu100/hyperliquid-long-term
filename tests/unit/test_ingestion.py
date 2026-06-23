from hyperliquid_candles.hyperliquid.candles import Candle
from hyperliquid_candles.ingestion.fetch import fetch_candle_window
from hyperliquid_candles.ingestion.incremental import build_incremental_work_items


class FakeCandleSource:
    """Candle source that returns scripted pages and records request windows."""

    def __init__(self, pages: list[list[Candle]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, int, int]] = []

    def fetch_candles(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        self.calls.append((symbol, start_ms, end_ms))
        if not self.pages:
            return []
        return self.pages.pop(0)


def _candle(open_ms: int) -> Candle:
    """Build a minimal candle at a given open time for pagination tests."""
    return Candle("BTC", open_ms, open_ms + 59_999, 1.0, 1.0, 1.0, 1.0, 1.0, 1)


def test_fetch_candle_window_pages_backward_by_end_time() -> None:
    """A window wider than one page must be assembled by walking endTime backward.

    Hyperliquid is newest-anchored: the first request for [0, 180_000] returns the
    newest chunk near the end, so the fetcher must lower endTime to reach the
    older candles rather than advancing startTime.
    """
    newest_page = [_candle(120_000), _candle(180_000)]
    older_page = [_candle(0), _candle(60_000)]
    source = FakeCandleSource([newest_page, older_page])

    rows = fetch_candle_window(symbol="BTC", start_ms=0, end_ms=180_000, source=source)

    assert [row.open_time_ms for row in rows] == [0, 60_000, 120_000, 180_000]
    # Second request lowered endTime to just before the oldest row already seen.
    assert source.calls == [("BTC", 0, 180_000), ("BTC", 0, 60_000)]


def test_fetch_candle_window_stops_after_single_page_within_horizon() -> None:
    """When the first page already reaches the requested start, stop immediately."""
    single_page = [_candle(0), _candle(60_000), _candle(120_000)]
    source = FakeCandleSource([single_page])

    rows = fetch_candle_window(symbol="BTC", start_ms=0, end_ms=120_000, source=source)

    assert [row.open_time_ms for row in rows] == [0, 60_000, 120_000]
    assert source.calls == [("BTC", 0, 120_000)]


def test_fetch_candle_window_stops_on_empty_page() -> None:
    """An empty response (window outside the REST horizon) yields no rows."""
    source = FakeCandleSource([[]])

    rows = fetch_candle_window(symbol="BTC", start_ms=0, end_ms=120_000, source=source)

    assert rows == []
    assert source.calls == [("BTC", 0, 120_000)]


def test_build_incremental_work_items_overlap_and_clamp_to_horizon() -> None:
    """Incremental windows should overlap stored data but never request unreachable history."""
    work_items = build_incremental_work_items(
        symbols=("BTC", "ETH", "SOL"),
        watermarks_ms={"BTC": 1_000_000_000, "ETH": 100_000},
        last_closed_ms=1_000_300_000,
        overlap_candles=5,
        interval_ms=60_000,
        rest_horizon_min=5,
    )

    assert [(item.symbol, item.start_ms, item.end_ms) for item in work_items] == [
        ("BTC", 1_000_000_000, 1_000_300_000),
        ("ETH", 1_000_000_000, 1_000_300_000),
    ]
