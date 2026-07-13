"""HTTP client for Hyperliquid public `info` endpoints."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from hyperliquid_candles.hyperliquid.candles import (
    INTERVAL_1M,
    INTERVAL_MS,
    Candle,
    parse_candles,
)
from hyperliquid_candles.ratelimit import TokenBucket

LOGGER = logging.getLogger(__name__)
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
BASE_INFO_WEIGHT = 20
ITEM_WEIGHT_BLOCK = 60


def candle_request_weight(start_ms: int, end_ms: int) -> int:
    """Return a conservative request weight for an inclusive 1-minute window.

    Hyperliquid charges the normal 20-unit ``info`` weight plus additional
    weight per 60 response items. The response size is unknown before sending,
    so the requested number of aligned minute slots is used as an upper bound.
    """
    if end_ms < start_ms:
        raise ValueError("end_ms must be greater than or equal to start_ms")
    if start_ms % INTERVAL_MS != 0 or end_ms % INTERVAL_MS != 0:
        raise ValueError("Candle request bounds must align to 1-minute opens")
    requested_slots = (end_ms - start_ms) // INTERVAL_MS + 1
    return BASE_INFO_WEIGHT + requested_slots // ITEM_WEIGHT_BLOCK


class HyperliquidClient:
    """Small REST client for Hyperliquid `meta` and `candleSnapshot` requests."""

    def __init__(
        self,
        timeout_sec: int,
        max_retries: int,
        rate_limiter: TokenBucket,
        base_url: str = HYPERLIQUID_INFO_URL,
    ) -> None:
        """Create a client with timeout, retry, and rate-limit settings."""
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.rate_limiter = rate_limiter
        self.base_url = base_url
        self._client = httpx.Client(timeout=timeout_sec)

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def fetch_meta(self) -> dict[str, Any]:
        """Fetch Hyperliquid perpetual metadata."""
        response = self._post_info_with_retry({"type": "meta"})
        if not isinstance(response, dict):
            raise ValueError("Hyperliquid meta response was not a JSON object")
        return response

    def fetch_candles(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        """Fetch parsed candles for an inclusive, minute-aligned open-time window."""
        request = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": INTERVAL_1M,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        }
        response = self._post_info_with_retry(
            request,
            request_weight=candle_request_weight(start_ms=start_ms, end_ms=end_ms),
        )
        if not isinstance(response, list):
            raise ValueError(f"candleSnapshot for {symbol} did not return a list")

        candles = parse_candles(response)
        for candle in candles:
            if candle.symbol != symbol:
                raise ValueError(
                    f"candleSnapshot for {symbol} returned symbol {candle.symbol}"
                )
            if not start_ms <= candle.open_time_ms <= end_ms:
                raise ValueError(
                    f"candleSnapshot for {symbol} returned an out-of-window candle"
                )
        return candles

    def _post_info_with_retry(
        self,
        payload: dict[str, Any],
        request_weight: int = BASE_INFO_WEIGHT,
    ) -> Any:
        """POST to `/info` with retry behavior configured at runtime."""

        @retry(
            retry=retry_if_exception_type((httpx.HTTPError, RuntimeError)),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential_jitter(initial=1, max=30),
            reraise=True,
        )
        def _send() -> Any:
            # Reserve the full estimated weight before each attempt. Charging
            # response-item weight afterwards permits an initial burst that can
            # exceed the configured per-minute budget.
            self.rate_limiter.wait_for(request_weight)
            response = self._client.post(self.base_url, json=payload)
            if response.status_code == 429 or response.status_code >= 500:
                raise RuntimeError(
                    f"Transient Hyperliquid status {response.status_code}: {response.text[:200]}"
                )
            response.raise_for_status()
            return response.json()

        LOGGER.debug("Hyperliquid info request type=%s", payload.get("type"))
        return _send()
