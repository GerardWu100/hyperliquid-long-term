from hl_candles.hyperliquid.candles import Candle
from hl_candles.ingestion.backfill import build_initial_backfill_rows
from hl_candles.ingestion.incremental import build_incremental_work_items


class FakeCandleSource:
    """Small candle source used to test pagination without network calls."""

    def __init__(self, pages: list[list[Candle]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, int, int]] = []

    def fetch_candles(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        self.calls.append((symbol, start_ms, end_ms))
        if not self.pages:
            return []
        return self.pages.pop(0)


def test_build_initial_backfill_rows_pages_until_short_page() -> None:
    """Initial backfill should advance by the newest open time returned."""
    page_one = [
        Candle("BTC", 0, 59_999, 1.0, 1.0, 1.0, 1.0, 1.0, 1),
        Candle("BTC", 60_000, 119_999, 1.0, 1.0, 1.0, 1.0, 1.0, 1),
    ]
    page_two = [
        Candle("BTC", 120_000, 179_999, 1.0, 1.0, 1.0, 1.0, 1.0, 1),
    ]
    source = FakeCandleSource([page_one, page_two])

    rows = build_initial_backfill_rows(
        symbol="BTC",
        start_ms=0,
        end_ms=180_000,
        page_limit=2,
        source=source,
    )

    assert [row.open_time_ms for row in rows] == [0, 60_000, 120_000]
    assert source.calls == [("BTC", 0, 180_000), ("BTC", 120_000, 180_000)]


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
