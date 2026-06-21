"""Incremental ingestion window construction.

Only the window-building logic lives here. The per-symbol fetch loop and run
bookkeeping are orchestrated in `app.py`, which owns the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from hyperliquid_candles.hyperliquid.candles import INTERVAL_MS


@dataclass(frozen=True)
class WorkItem:
    """Fetch window for one symbol in one incremental cycle."""

    symbol: str
    start_ms: int
    end_ms: int


def build_incremental_work_items(
    symbols: tuple[str, ...],
    watermarks_ms: dict[str, int | None],
    last_closed_ms: int,
    overlap_candles: int,
    interval_ms: int = INTERVAL_MS,
    rest_horizon_min: int = 5000,
) -> list[WorkItem]:
    """Build restart-safe per-symbol fetch windows from ClickHouse watermarks."""
    horizon_floor_ms = last_closed_ms - rest_horizon_min * interval_ms
    work_items: list[WorkItem] = []

    for symbol in symbols:
        watermark_ms = watermarks_ms.get(symbol)
        if watermark_ms is None:
            continue

        overlapped_start_ms = watermark_ms - overlap_candles * interval_ms
        start_ms = max(overlapped_start_ms, horizon_floor_ms)
        if start_ms > last_closed_ms:
            continue

        work_items.append(
            WorkItem(symbol=symbol, start_ms=start_ms, end_ms=last_closed_ms)
        )

    return work_items
