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
    """Convert one Hyperliquid `candleSnapshot` object into a typed candle."""
    return Candle(
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
