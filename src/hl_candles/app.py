"""Application orchestration for Hyperliquid candle ingestion."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from hl_candles.config import Settings, load_settings
from hl_candles.hyperliquid.candles import INTERVAL_MS, Candle
from hl_candles.hyperliquid.client import HyperliquidClient
from hl_candles.hyperliquid.universe import select_symbols
from hl_candles.ingestion.backfill import build_initial_backfill_rows
from hl_candles.ingestion.incremental import WorkItem, build_incremental_work_items
from hl_candles.ingestion.windows import compute_initial_start_ms, last_closed_open_ms
from hl_candles.logging_setup import setup_logging
from hl_candles.ratelimit import TokenBucket
from hl_candles.scheduler import run_scheduler
from hl_candles.storage.clickhouse_client import wait_for_clickhouse
from hl_candles.storage.runs import (
    RunSummary,
    SymbolStatus,
    insert_run_summary,
    insert_symbol_statuses,
    ms_to_datetime,
    new_run_id,
    utc_now,
)
from hl_candles.storage.schema import create_schema
from hl_candles.storage.watermarks import query_watermarks_ms
from hl_candles.storage.writer import insert_candles

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CycleResult:
    """Summary returned by one ingestion cycle."""

    run_id: UUID
    symbols_total: int
    symbols_ok: int
    symbols_failed: int
    candles_inserted: int
    status: str


def run_once(settings: Settings | None = None) -> CycleResult:
    """Run one catch-up ingestion cycle.

    This function is the entrypoint for `hl-run-once`, cron, systemd timers, and
    the first catch-up cycle inside the long-running scheduler.
    """
    resolved_settings = settings or load_settings()
    log_path = setup_logging(resolved_settings.ingestion.log_level)
    LOGGER.info("Logging to %s", log_path)

    validated_clickhouse = wait_for_clickhouse(
        clickhouse_settings=resolved_settings.clickhouse,
        ingestion_settings=resolved_settings.ingestion,
    )
    client = validated_clickhouse.client
    create_schema(client=client, database=resolved_settings.clickhouse.database)

    rate_limiter = TokenBucket(
        tokens_per_minute=resolved_settings.ingestion.weight_budget_per_min,
    )
    hyperliquid = HyperliquidClient(
        timeout_sec=resolved_settings.ingestion.request_timeout_sec,
        max_retries=resolved_settings.ingestion.max_retries,
        rate_limiter=rate_limiter,
    )
    try:
        return run_ingestion_cycle(
            settings=resolved_settings,
            clickhouse_client=client,
            hyperliquid_client=hyperliquid,
        )
    finally:
        hyperliquid.close()


def run_ingestion_cycle(
    settings: Settings,
    clickhouse_client: object,
    hyperliquid_client: object,
) -> CycleResult:
    """Run initial backfill for new symbols plus incremental overlap for stored symbols."""
    run_id = new_run_id()
    started_at = utc_now()
    database = settings.clickhouse.database

    meta_response = hyperliquid_client.fetch_meta()
    symbols = select_symbols(
        meta_response=meta_response,
        symbols_mode=settings.ingestion.symbols_mode,
        symbols_allowlist=settings.ingestion.symbols_allowlist,
    )
    watermarks_ms = query_watermarks_ms(clickhouse_client, database=database)
    last_closed_ms = last_closed_open_ms()

    all_rows: list[Candle] = []
    symbol_statuses: list[SymbolStatus] = []
    symbols_failed = 0

    new_symbols = tuple(
        symbol for symbol in symbols if watermarks_ms.get(symbol) is None
    )
    for symbol in new_symbols:
        try:
            rows, status = _fetch_initial_symbol(
                run_id=run_id,
                symbol=symbol,
                last_closed_ms=last_closed_ms,
                settings=settings,
                hyperliquid_client=hyperliquid_client,
            )
            all_rows.extend(rows)
            symbol_statuses.append(status)
        except Exception as exc:
            symbols_failed += 1
            symbol_statuses.append(
                _failed_symbol_status(
                    run_id=run_id,
                    symbol=symbol,
                    mode="initial",
                    last_closed_ms=last_closed_ms,
                    error=exc,
                )
            )

    work_items = build_incremental_work_items(
        symbols=symbols,
        watermarks_ms=watermarks_ms,
        last_closed_ms=last_closed_ms,
        overlap_candles=settings.ingestion.overlap_candles,
        interval_ms=INTERVAL_MS,
        rest_horizon_min=settings.ingestion.rest_horizon_min,
    )
    incremental_rows, incremental_statuses, incremental_failures = (
        _fetch_incremental_symbols(
            run_id=run_id,
            work_items=work_items,
            hyperliquid_client=hyperliquid_client,
        )
    )
    all_rows.extend(incremental_rows)
    symbol_statuses.extend(incremental_statuses)
    symbols_failed += incremental_failures

    candles_inserted = 0
    final_status = "success" if symbols_failed == 0 else "partial"
    error = ""
    try:
        candles_inserted = insert_candles(
            clickhouse_client,
            database=database,
            candles=all_rows,
        )
        insert_symbol_statuses(
            clickhouse_client, database=database, statuses=symbol_statuses
        )
    except Exception as exc:
        final_status = "failed"
        error = str(exc)
        symbols_failed = len(symbols)
        LOGGER.exception("Insert failed; no external watermark has been advanced")

    symbols_ok = max(len(symbols) - symbols_failed, 0)
    insert_run_summary(
        clickhouse_client,
        database=database,
        summary=RunSummary(
            run_id=run_id,
            mode="incremental",
            started_at=started_at,
            finished_at=utc_now(),
            symbols_total=len(symbols),
            symbols_ok=symbols_ok,
            symbols_failed=symbols_failed,
            candles_inserted=candles_inserted,
            status=final_status,
            error=error,
        ),
    )

    LOGGER.info(
        "Ingestion cycle complete run_id=%s status=%s symbols=%s rows=%s",
        run_id,
        final_status,
        len(symbols),
        candles_inserted,
    )
    return CycleResult(
        run_id=run_id,
        symbols_total=len(symbols),
        symbols_ok=symbols_ok,
        symbols_failed=symbols_failed,
        candles_inserted=candles_inserted,
        status=final_status,
    )


def _fetch_initial_symbol(
    run_id: UUID,
    symbol: str,
    last_closed_ms: int,
    settings: Settings,
    hyperliquid_client: object,
) -> tuple[list[Candle], SymbolStatus]:
    """Fetch initial backfill rows and status for one symbol."""
    requested_ms, effective_ms, was_clamped = compute_initial_start_ms(
        last_closed_ms=last_closed_ms,
        rest_horizon_min=settings.ingestion.rest_horizon_min,
        requested_start_time_utc=settings.ingestion.initial_backfill_start_time_utc,
    )
    if was_clamped:
        LOGGER.warning("Initial backfill for %s clamped to REST horizon", symbol)

    rows = build_initial_backfill_rows(
        symbol=symbol,
        start_ms=effective_ms,
        end_ms=last_closed_ms,
        page_limit=settings.ingestion.rest_horizon_min,
        source=hyperliquid_client,
    )
    status = SymbolStatus(
        run_id=run_id,
        symbol=symbol,
        mode="initial",
        requested_start=ms_to_datetime(requested_ms),
        requested_end=ms_to_datetime(last_closed_ms),
        effective_start=ms_to_datetime(effective_ms),
        rows_fetched=len(rows),
        rows_inserted=len(rows),
        status="success",
    )
    return rows, status


def _fetch_incremental_symbols(
    run_id: UUID,
    work_items: Sequence[WorkItem],
    hyperliquid_client: object,
) -> tuple[list[Candle], list[SymbolStatus], int]:
    """Fetch incremental rows while preserving per-symbol failure visibility."""
    rows: list[Candle] = []
    statuses: list[SymbolStatus] = []
    failures = 0

    for item in work_items:
        try:
            symbol_rows = hyperliquid_client.fetch_candles(
                symbol=item.symbol,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
            )
            rows.extend(symbol_rows)
            statuses.append(
                SymbolStatus(
                    run_id=run_id,
                    symbol=item.symbol,
                    mode="incremental",
                    requested_start=ms_to_datetime(item.start_ms),
                    requested_end=ms_to_datetime(item.end_ms),
                    effective_start=ms_to_datetime(item.start_ms),
                    rows_fetched=len(symbol_rows),
                    rows_inserted=len(symbol_rows),
                    status="success",
                )
            )
        except Exception as exc:
            failures += 1
            statuses.append(
                SymbolStatus(
                    run_id=run_id,
                    symbol=item.symbol,
                    mode="incremental",
                    requested_start=ms_to_datetime(item.start_ms),
                    requested_end=ms_to_datetime(item.end_ms),
                    effective_start=ms_to_datetime(item.start_ms),
                    rows_fetched=0,
                    rows_inserted=0,
                    status="failed",
                    error=str(exc),
                )
            )

    return rows, statuses, failures


def _failed_symbol_status(
    run_id: UUID,
    symbol: str,
    mode: str,
    last_closed_ms: int,
    error: Exception,
) -> SymbolStatus:
    """Build a failure status row when a symbol fails before a request window exists."""
    last_closed = ms_to_datetime(last_closed_ms)
    return SymbolStatus(
        run_id=run_id,
        symbol=symbol,
        mode=mode,
        requested_start=last_closed,
        requested_end=last_closed,
        effective_start=last_closed,
        rows_fetched=0,
        rows_inserted=0,
        status="failed",
        error=str(error),
    )


def main() -> None:
    """Run the long-lived ingestion scheduler."""
    settings = load_settings()
    setup_logging(settings.ingestion.log_level)
    run_scheduler(settings=settings, cycle_callback=lambda: run_once(settings))
