"""Incremental ingestion window construction and cycle orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from hyperliquid_candles.hyperliquid.candles import INTERVAL_MS, Candle, CandleSource
from hyperliquid_candles.hyperliquid.candles import dedupe_by_symbol_open_time

LOGGER = logging.getLogger(__name__)


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


def fetch_incremental_rows(
    work_items: list[WorkItem],
    source: CandleSource,
) -> tuple[list[Candle], dict[str, int]]:
    """Fetch and deduplicate all incremental rows for a cycle."""
    fetched_counts: dict[str, int] = {}
    batch: list[Candle] = []

    for item in work_items:
        candles = source.fetch_candles(
            symbol=item.symbol,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
        )
        fetched_counts[item.symbol] = len(candles)
        batch.extend(candles)

    return dedupe_by_symbol_open_time(batch), fetched_counts


def log_cycle_failure(run_id: UUID, error: Exception) -> None:
    """Log a failed cycle without advancing any external watermark."""
    LOGGER.exception(
        "Ingestion cycle %s failed; ClickHouse rows remain source of truth", run_id
    )
    LOGGER.debug("Failure detail: %r", error)
