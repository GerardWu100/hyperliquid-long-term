from datetime import UTC, datetime

import pytest

from hyperliquid_candles.hyperliquid.candles import (
    Candle,
    dedupe_by_symbol_open_time,
    parse_candle,
)
from hyperliquid_candles.ingestion.windows import (
    compute_initial_start_ms,
    earliest_open_ms_for_candle_count,
    last_closed_open_ms,
)


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
        rest_horizon_candles=5_000,
        requested_start_time_utc=requested_iso,
    )

    assert requested_ms == int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
    assert effective_ms == last_closed_ms - 4_999 * 60_000
    assert was_clamped is True


def test_earliest_open_uses_n_minus_one_intervals_for_inclusive_window() -> None:
    """Five candle opens span four intervals, not five."""
    assert earliest_open_ms_for_candle_count(300_000, 5) == 60_000


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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("i", "5m", "interval"),
        ("t", 1_772_545_200_001, "aligned"),
        ("T", 1_772_545_260_000, "close time"),
        ("h", "99.0", "high"),
        ("l", "106.0", "low"),
        ("v", "-1.0", "volume"),
        ("n", -1, "trade count"),
    ],
)
def test_parse_candle_rejects_invalid_source_invariants(
    field: str,
    value: object,
    message: str,
) -> None:
    """Malformed source rows should fail before reaching the storage schema."""
    payload: dict[str, object] = {
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
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        parse_candle(payload)
