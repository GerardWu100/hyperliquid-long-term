"""Hyperliquid 1-minute candle fetch, parse, and pagination helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

INTERVAL_1M = "1m"
INTERVAL_MS = 60_000


@dataclass(frozen=True)
class Candle:
    """Parsed 1-minute OHLCV candle.

    Time values are Unix epoch milliseconds in UTC. Prices and volume use
    `float` because the ClickHouse table stores them as `Float64`.
    """

    symbol: str
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int


class CandleSource(Protocol):
    """Protocol for objects that can fetch parsed candles for one symbol."""

    def fetch_candles(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        """Fetch parsed candles for the inclusive `[start_ms, end_ms]` window."""


def parse_candle(payload: dict[str, Any]) -> Candle:
    """Convert and validate one Hyperliquid 1-minute candle payload.

    The parser rejects interval, timestamp, price-range, and unsigned-value
    violations before they reach ClickHouse. This turns an upstream schema
    change into a visible per-symbol failure instead of silent table corruption.
    """
    if str(payload["i"]) != INTERVAL_1M:
        raise ValueError(f"Expected 1m candle interval, received {payload['i']!r}")

    candle = Candle(
        symbol=str(payload["s"]),
        open_time_ms=int(payload["t"]),
        close_time_ms=int(payload["T"]),
        open=float(payload["o"]),
        high=float(payload["h"]),
        low=float(payload["l"]),
        close=float(payload["c"]),
        volume=float(payload["v"]),
        trades=int(payload.get("n", 0)),
    )
    if candle.open_time_ms % INTERVAL_MS != 0:
        raise ValueError("Candle open time is not aligned to a 1-minute boundary")
    if candle.close_time_ms != candle.open_time_ms + INTERVAL_MS - 1:
        raise ValueError("Candle close time does not match a 1-minute interval")
    if candle.high < max(candle.open, candle.low, candle.close):
        raise ValueError("Candle high is below another OHLC price")
    if candle.low > min(candle.open, candle.high, candle.close):
        raise ValueError("Candle low is above another OHLC price")
    if candle.volume < 0:
        raise ValueError("Candle volume must be non-negative")
    if candle.trades < 0:
        raise ValueError("Candle trade count must be non-negative")
    return candle


def parse_candles(payloads: Iterable[dict[str, Any]]) -> list[Candle]:
    """Parse a sequence of raw Hyperliquid candle payloads."""
    candles = [parse_candle(payload) for payload in payloads]
    return sorted(candles, key=lambda candle: candle.open_time_ms)


def dedupe_by_symbol_open_time(candles: Iterable[Candle]) -> list[Candle]:
    """Keep the latest seen candle for each `(symbol, open_time_ms)` key."""
    latest_by_key: dict[tuple[str, int], Candle] = {}
    for candle in candles:
        latest_by_key[(candle.symbol, candle.open_time_ms)] = candle
    return sorted(
        latest_by_key.values(),
        key=lambda candle: (candle.symbol, candle.open_time_ms),
    )
