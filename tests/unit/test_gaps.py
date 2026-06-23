from datetime import UTC, datetime

from hyperliquid_candles.ingestion.gaps import (
    CandleGap,
    parse_gap_rows,
    recoverable_gaps_query,
)
from hyperliquid_candles.storage.watermarks import datetime_to_ms


def test_recoverable_gaps_query_bounds_scan_to_horizon_floor() -> None:
    """The recoverable-gap query must filter open_time to the horizon floor.

    Bounding the FINAL scan to recent partitions keeps the per-cycle gap check
    cheap and excludes gaps that REST can no longer serve.
    """
    horizon_floor_ms = 1_700_000_000_000
    sql = recoverable_gaps_query("hyperliquid", horizon_floor_ms)

    assert "candles_1m FINAL" in sql
    assert "open_time >= toDateTime64(1700000000.0, 3, 'UTC')" in sql


def test_parse_gap_rows_converts_boundaries_to_ms() -> None:
    """Gap boundary datetimes should become millisecond windows for refetching."""
    gap_after = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    gap_before = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    rows = [("BTC", gap_after, gap_before, 4)]

    gaps = parse_gap_rows(rows, datetime_to_ms)

    assert gaps == [
        CandleGap(
            symbol="BTC",
            gap_after_ms=int(gap_after.timestamp() * 1000),
            gap_before_ms=int(gap_before.timestamp() * 1000),
            missing_minutes=4,
        )
    ]
