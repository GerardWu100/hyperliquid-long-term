from datetime import UTC, datetime

from hl_candles.hyperliquid.candles import (
    Candle,
    dedupe_by_symbol_open_time,
    parse_candle,
)
from hl_candles.ingestion.windows import compute_initial_start_ms, last_closed_open_ms


def test_last_closed_open_ms_returns_previous_complete_minute() -> None:
    """The service must not ingest the minute that is still forming."""
    now_ms = 1_772_545_245_123

    assert last_closed_open_ms(now_ms) == 1_772_545_140_000


def test_compute_initial_start_clamps_old_requested_time_to_rest_horizon() -> None:
    """Initial backfill should warn and start at the earliest reachable REST candle."""
    last_closed_ms = 1_772_545_200_000
    requested_iso = "2025-01-01T00:00:00Z"

    requested_ms, effective_ms, was_clamped = compute_initial_start_ms(
        last_closed_ms=last_closed_ms,
        rest_horizon_min=5_000,
        requested_start_time_utc=requested_iso,
    )

    assert requested_ms == int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
    assert effective_ms == last_closed_ms - 5_000 * 60_000
    assert was_clamped is True


def test_parse_candle_converts_hyperliquid_payload_to_typed_row() -> None:
    """Hyperliquid returns numeric candle fields as strings; storage rows use numbers."""
    payload = {
        "t": 1_772_545_200_000,
        "T": 1_772_545_259_999,
        "s": "BTC",
        "i": "1m",
        "o": "100.0",
        "h": "110.0",
        "l": "95.0",
        "c": "105.5",
        "v": "12.25",
        "n": 42,
    }

    candle = parse_candle(payload)

    assert candle == Candle(
        symbol="BTC",
        open_time_ms=1_772_545_200_000,
        close_time_ms=1_772_545_259_999,
        open=100.0,
        high=110.0,
        low=95.0,
        close=105.5,
        volume=12.25,
        trades=42,
    )


def test_dedupe_by_symbol_open_time_keeps_latest_seen_row() -> None:
    """Page-boundary overlap should not create duplicate rows in a batch."""
    first = Candle("BTC", 60_000, 119_999, 1.0, 2.0, 0.5, 1.5, 10.0, 3)
    replacement = Candle("BTC", 60_000, 119_999, 1.0, 2.1, 0.5, 1.8, 11.0, 4)
    eth = Candle("ETH", 60_000, 119_999, 1.0, 2.0, 0.5, 1.5, 10.0, 3)

    rows = dedupe_by_symbol_open_time([first, replacement, eth])

    assert rows == [replacement, eth]
