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

from hl_candles.hyperliquid.candles import INTERVAL_1M, Candle, parse_candles
from hl_candles.ratelimit import TokenBucket

LOGGER = logging.getLogger(__name__)
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
BASE_INFO_WEIGHT = 20
ITEM_WEIGHT_BLOCK = 60


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
        """Fetch parsed 1-minute candles for one symbol and time window."""
        request = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": INTERVAL_1M,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        }
        response = self._post_info_with_retry(request)
        if not isinstance(response, list):
            raise ValueError(f"candleSnapshot for {symbol} did not return a list")

        candles = parse_candles(response)
        extra_weight = len(candles) // ITEM_WEIGHT_BLOCK
        if extra_weight > 0:
            self.rate_limiter.wait_for(extra_weight)
        return candles

    def _post_info_with_retry(self, payload: dict[str, Any]) -> Any:
        """POST to `/info` with retry behavior configured at runtime."""

        @retry(
            retry=retry_if_exception_type((httpx.HTTPError, RuntimeError)),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential_jitter(initial=1, max=30),
            reraise=True,
        )
        def _send() -> Any:
            self.rate_limiter.wait_for(BASE_INFO_WEIGHT)
            response = self._client.post(self.base_url, json=payload)
            if response.status_code == 429 or response.status_code >= 500:
                raise RuntimeError(
                    f"Transient Hyperliquid status {response.status_code}: {response.text[:200]}"
                )
            response.raise_for_status()
            return response.json()

        LOGGER.debug("Hyperliquid info request type=%s", payload.get("type"))
        return _send()
