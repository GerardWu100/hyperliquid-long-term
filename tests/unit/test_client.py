"""Unit tests for Hyperliquid request-weight accounting."""

import pytest

from hyperliquid_candles.hyperliquid.client import candle_request_weight


@pytest.mark.parametrize(
    ("slots", "expected_weight"),
    [(1, 20), (59, 20), (60, 21), (5_000, 103)],
)
def test_candle_request_weight_reserves_response_weight_before_send(
    slots: int,
    expected_weight: int,
) -> None:
    """The limiter should reserve base and item weight for all requested slots."""
    start_ms = 1_000_020_000
    end_ms = start_ms + (slots - 1) * 60_000

    assert candle_request_weight(start_ms, end_ms) == expected_weight


def test_candle_request_weight_rejects_reversed_window() -> None:
    """A reversed request window has no meaningful response-size bound."""
    with pytest.raises(ValueError, match="end_ms"):
        candle_request_weight(120_000, 60_000)


def test_candle_request_weight_rejects_unaligned_bounds() -> None:
    """Hyperliquid rounds unaligned bounds, so callers must request candle opens."""
    with pytest.raises(ValueError, match="align"):
        candle_request_weight(60_001, 120_000)
