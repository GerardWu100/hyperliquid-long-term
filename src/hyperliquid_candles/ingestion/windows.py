"""Time-window calculations for 1-minute candle ingestion."""

from __future__ import annotations

from datetime import UTC, datetime

from hyperliquid_candles.hyperliquid.candles import INTERVAL_MS


def last_closed_open_ms(now_ms: int | None = None) -> int:
    """Return the open time of the latest fully closed 1-minute candle."""
    effective_now_ms = now_ms
    if effective_now_ms is None:
        effective_now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    current_minute_open_ms = effective_now_ms // INTERVAL_MS * INTERVAL_MS
    return current_minute_open_ms - INTERVAL_MS


def compute_initial_start_ms(
    last_closed_ms: int,
    rest_horizon_min: int,
    requested_start_time_utc: str,
) -> tuple[int, int, bool]:
    """Compute requested and REST-horizon-clamped initial backfill starts."""
    rest_horizon_floor_ms = last_closed_ms - rest_horizon_min * INTERVAL_MS
    if requested_start_time_utc:
        requested_ms = parse_utc_iso_to_ms(requested_start_time_utc)
    else:
        requested_ms = rest_horizon_floor_ms

    effective_ms = max(requested_ms, rest_horizon_floor_ms)
    was_clamped = requested_ms < rest_horizon_floor_ms
    return requested_ms, effective_ms, was_clamped


def parse_utc_iso_to_ms(value: str) -> int:
    """Parse an ISO-8601 UTC timestamp into Unix epoch milliseconds."""
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp must include a timezone, for example Z")
    utc_value = parsed.astimezone(UTC)
    return int(utc_value.timestamp() * 1000)
