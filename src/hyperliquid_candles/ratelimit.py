"""Synchronous token-bucket rate limiter for Hyperliquid request weights."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Pace weighted requests under a per-minute budget.

    Parameters
    ----------
    tokens_per_minute:
        Maximum request weight budget replenished per minute.
    capacity:
        Maximum burst size. When omitted, one minute of budget is allowed.
    """

    tokens_per_minute: float
    capacity: float | None = None
    _tokens: float = field(init=False)
    _last_refill_monotonic: float = field(init=False)

    def __post_init__(self) -> None:
        """Initialize bucket state after dataclass construction."""
        if self.tokens_per_minute <= 0:
            raise ValueError("tokens_per_minute must be positive")
        if self.capacity is None:
            self.capacity = self.tokens_per_minute
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        self._tokens = self.capacity
        self._last_refill_monotonic = time.monotonic()

    def wait_for(self, weight: float) -> None:
        """Block until at least `weight` request tokens are available."""
        if weight <= 0:
            raise ValueError("weight must be positive")

        while True:
            self._refill()
            if self._tokens >= weight:
                self._tokens -= weight
                return

            missing_tokens = weight - self._tokens
            tokens_per_second = self.tokens_per_minute / 60.0
            time.sleep(missing_tokens / tokens_per_second)

    def _refill(self) -> None:
        """Refill tokens according to elapsed monotonic time."""
        now = time.monotonic()
        elapsed_seconds = now - self._last_refill_monotonic
        tokens_per_second = self.tokens_per_minute / 60.0
        self._tokens = min(
            self.capacity, self._tokens + elapsed_seconds * tokens_per_second
        )
        self._last_refill_monotonic = now
