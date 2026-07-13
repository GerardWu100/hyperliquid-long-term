"""Application orchestration for Hyperliquid candle ingestion.

One ingestion cycle runs three phases against the active perpetual universe:

1. Initial backfill for symbols with no stored candles yet.
2. Incremental catch-up for stored symbols, starting from their ClickHouse
   watermark minus a small overlap and clamped to the REST horizon.
3. Gap backfill that refetches any internal missing periods still inside the
   REST horizon, so the dataset self-heals after downtime or partial failures.

Every phase shares one backward-paginating fetch primitive and inserts per
symbol in bounded chunks, so memory stays flat even with the full universe and a
single failing symbol cannot sink the whole cycle's writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from hyperliquid_candles.config import Settings, load_settings
from hyperliquid_candles.hyperliquid.candles import INTERVAL_MS
from hyperliquid_candles.hyperliquid.client import HyperliquidClient
from hyperliquid_candles.hyperliquid.universe import select_symbols
from hyperliquid_candles.ingestion.fetch import fetch_candle_window
from hyperliquid_candles.ingestion.gaps import (
    parse_gap_rows,
    recoverable_gaps_query,
)
from hyperliquid_candles.ingestion.incremental import build_incremental_work_items
from hyperliquid_candles.ingestion.windows import (
    compute_initial_start_ms,
    earliest_open_ms_for_candle_count,
    last_closed_open_ms,
)
from hyperliquid_candles.logging_setup import setup_logging
from hyperliquid_candles.ratelimit import TokenBucket
from hyperliquid_candles.scheduler import run_scheduler
from hyperliquid_candles.storage.clickhouse_client import wait_for_clickhouse
from hyperliquid_candles.storage.runs import (
    RunSummary,
    SymbolStatus,
    insert_run_summary,
    insert_symbol_statuses,
    ms_to_datetime,
    new_run_id,
    utc_now,
)
from hyperliquid_candles.storage.schema import create_schema
from hyperliquid_candles.storage.watermarks import datetime_to_ms, query_watermarks_ms
from hyperliquid_candles.storage.writer import insert_candles

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


def run_once(
    settings: Settings | None = None,
    configure_logging: bool = True,
) -> CycleResult:
    """Run one catch-up ingestion cycle.

    This function is the entrypoint for `hyperliquid-candles-run-once`, cron, systemd timers, and
    each catch-up cycle inside the long-running scheduler. Standalone callers keep
    the default logging setup behavior. The scheduler disables per-cycle logging
    setup because it configures process logging once before entering the loop.
    """
    resolved_settings = settings or load_settings()
    if configure_logging:
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
    """Run initial backfill, incremental overlap, and gap repair for the universe."""
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
    horizon_floor_ms = earliest_open_ms_for_candle_count(
        last_open_ms=last_closed_ms,
        candle_count=settings.ingestion.rest_horizon_candles,
        interval_ms=INTERVAL_MS,
    )

    symbol_statuses: list[SymbolStatus] = []
    failed_symbols: set[str] = set()
    candles_inserted = 0

    # Phase 1: brand-new symbols that have never been stored.
    new_symbols = tuple(
        symbol for symbol in symbols if watermarks_ms.get(symbol) is None
    )
    for symbol in new_symbols:
        inserted, status = _ingest_initial_symbol(
            run_id=run_id,
            symbol=symbol,
            last_closed_ms=last_closed_ms,
            settings=settings,
            clickhouse_client=clickhouse_client,
            hyperliquid_client=hyperliquid_client,
        )
        candles_inserted += inserted
        symbol_statuses.append(status)
        if status.status == "failed":
            failed_symbols.add(symbol)

    # Phase 2: incremental overlap for already-stored symbols.
    work_items = build_incremental_work_items(
        symbols=symbols,
        watermarks_ms=watermarks_ms,
        last_closed_ms=last_closed_ms,
        overlap_candles=settings.ingestion.overlap_candles,
        interval_ms=INTERVAL_MS,
        rest_horizon_candles=settings.ingestion.rest_horizon_candles,
    )
    for item in work_items:
        inserted, status = _ingest_window(
            run_id=run_id,
            symbol=item.symbol,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            settings=settings,
            clickhouse_client=clickhouse_client,
            hyperliquid_client=hyperliquid_client,
        )
        candles_inserted += inserted
        symbol_statuses.append(status)
        if status.status == "failed":
            failed_symbols.add(item.symbol)

    # Phase 3: repair internal gaps that are still inside the REST horizon.
    gap_inserted, gap_statuses, gap_failures = _backfill_recoverable_gaps(
        run_id=run_id,
        tracked_symbols=set(symbols),
        horizon_floor_ms=horizon_floor_ms,
        settings=settings,
        clickhouse_client=clickhouse_client,
        hyperliquid_client=hyperliquid_client,
    )
    candles_inserted += gap_inserted
    symbol_statuses.extend(gap_statuses)
    failed_symbols.update(gap_failures)

    symbols_total = len(symbols)
    symbols_failed = len(failed_symbols)
    symbols_ok = max(symbols_total - symbols_failed, 0)
    final_status = _cycle_status(
        symbols_total=symbols_total, symbols_failed=symbols_failed
    )

    # Metadata writes are best-effort: a failure here must not crash the loop,
    # because the candle rows (the real product) are already committed and the
    # next cycle recovers any missed window from watermarks.
    try:
        insert_symbol_statuses(
            clickhouse_client, database=database, statuses=symbol_statuses
        )
    except Exception:
        LOGGER.exception("Failed to write per-symbol status rows")

    run_mode = "initial" if new_symbols else "incremental"
    try:
        insert_run_summary(
            clickhouse_client,
            database=database,
            summary=RunSummary(
                run_id=run_id,
                mode=run_mode,
                started_at=started_at,
                finished_at=utc_now(),
                symbols_total=symbols_total,
                symbols_ok=symbols_ok,
                symbols_failed=symbols_failed,
                candles_inserted=candles_inserted,
                status=final_status,
                error="",
            ),
        )
    except Exception:
        LOGGER.exception("Failed to write run summary row")

    LOGGER.info(
        "Ingestion cycle complete run_id=%s status=%s symbols=%s rows=%s",
        run_id,
        final_status,
        symbols_total,
        candles_inserted,
    )
    return CycleResult(
        run_id=run_id,
        symbols_total=symbols_total,
        symbols_ok=symbols_ok,
        symbols_failed=symbols_failed,
        candles_inserted=candles_inserted,
        status=final_status,
    )


def _cycle_status(symbols_total: int, symbols_failed: int) -> str:
    """Map per-symbol failure counts to a run-level status label."""
    if symbols_failed == 0:
        return "success"
    if symbols_total > 0 and symbols_failed >= symbols_total:
        return "failed"
    return "partial"


def _ingest_initial_symbol(
    run_id: UUID,
    symbol: str,
    last_closed_ms: int,
    settings: Settings,
    clickhouse_client: object,
    hyperliquid_client: object,
) -> tuple[int, SymbolStatus]:
    """Fetch and store the initial backfill window for one new symbol."""
    requested_ms, effective_ms, was_clamped = compute_initial_start_ms(
        last_closed_ms=last_closed_ms,
        rest_horizon_candles=settings.ingestion.rest_horizon_candles,
        requested_start_time_utc=settings.ingestion.initial_backfill_start_time_utc,
    )
    if was_clamped:
        LOGGER.warning("Initial backfill for %s clamped to REST horizon", symbol)

    return _fetch_store_status(
        run_id=run_id,
        symbol=symbol,
        mode="initial",
        requested_start_ms=requested_ms,
        effective_start_ms=effective_ms,
        end_ms=last_closed_ms,
        settings=settings,
        clickhouse_client=clickhouse_client,
        hyperliquid_client=hyperliquid_client,
    )


def _ingest_window(
    run_id: UUID,
    symbol: str,
    start_ms: int,
    end_ms: int,
    settings: Settings,
    clickhouse_client: object,
    hyperliquid_client: object,
) -> tuple[int, SymbolStatus]:
    """Fetch and store one incremental window for a stored symbol."""
    return _fetch_store_status(
        run_id=run_id,
        symbol=symbol,
        mode="incremental",
        requested_start_ms=start_ms,
        effective_start_ms=start_ms,
        end_ms=end_ms,
        settings=settings,
        clickhouse_client=clickhouse_client,
        hyperliquid_client=hyperliquid_client,
    )


def _fetch_store_status(
    run_id: UUID,
    symbol: str,
    mode: str,
    requested_start_ms: int,
    effective_start_ms: int,
    end_ms: int,
    settings: Settings,
    clickhouse_client: object,
    hyperliquid_client: object,
) -> tuple[int, SymbolStatus]:
    """Fetch a window, insert it in bounded chunks, and build a status row.

    A failure isolated to this symbol returns a ``failed`` status with zero rows
    so the rest of the cycle proceeds. The shared backward-paginating fetcher
    assembles every page the source still makes reachable and stops defensively
    if the source returns no rows or fails to honor the decreasing end cursor.
    """
    database = settings.clickhouse.database
    batch_max_rows = settings.ingestion.batch_insert_max_rows
    try:
        rows = fetch_candle_window(
            symbol=symbol,
            start_ms=effective_start_ms,
            end_ms=end_ms,
            source=hyperliquid_client,
        )
        inserted = insert_candles(
            clickhouse_client,
            database=database,
            candles=rows,
            batch_max_rows=batch_max_rows,
        )
        status = SymbolStatus(
            run_id=run_id,
            symbol=symbol,
            mode=mode,
            requested_start=ms_to_datetime(requested_start_ms),
            requested_end=ms_to_datetime(end_ms),
            effective_start=ms_to_datetime(effective_start_ms),
            rows_fetched=len(rows),
            rows_inserted=inserted,
            status="success",
        )
        return inserted, status
    except Exception as exc:
        LOGGER.exception("Symbol %s failed during %s ingestion", symbol, mode)
        status = SymbolStatus(
            run_id=run_id,
            symbol=symbol,
            mode=mode,
            requested_start=ms_to_datetime(requested_start_ms),
            requested_end=ms_to_datetime(end_ms),
            effective_start=ms_to_datetime(effective_start_ms),
            rows_fetched=0,
            rows_inserted=0,
            status="failed",
            error=str(exc),
        )
        return 0, status


def _backfill_recoverable_gaps(
    run_id: UUID,
    tracked_symbols: set[str],
    horizon_floor_ms: int,
    settings: Settings,
    clickhouse_client: object,
    hyperliquid_client: object,
) -> tuple[int, list[SymbolStatus], set[str]]:
    """Detect and refetch internal gaps still inside the REST horizon.

    Returns the rows inserted, per-gap status rows, and the set of symbols whose
    gap repair failed. A failure in the detection query itself is swallowed (the
    candle product is already committed) so the cycle still finishes cleanly.
    """
    database = settings.clickhouse.database
    try:
        result = clickhouse_client.query(
            recoverable_gaps_query(database, horizon_floor_ms)
        )
        gaps = parse_gap_rows(list(result.result_rows), datetime_to_ms)
    except Exception:
        LOGGER.exception("Gap detection failed; skipping gap backfill this cycle")
        return 0, [], set()

    inserted_total = 0
    statuses: list[SymbolStatus] = []
    failed_symbols: set[str] = set()

    for gap in gaps:
        # Skip gaps for symbols we are not tracking (for example delisted coins
        # whose historical rows remain but are no longer in the active universe).
        if gap.symbol not in tracked_symbols:
            continue

        LOGGER.info(
            "Backfilling gap symbol=%s missing_minutes=%s",
            gap.symbol,
            gap.missing_minutes,
        )
        inserted, status = _ingest_window(
            run_id=run_id,
            symbol=gap.symbol,
            start_ms=gap.gap_after_ms,
            end_ms=gap.gap_before_ms,
            settings=settings,
            clickhouse_client=clickhouse_client,
            hyperliquid_client=hyperliquid_client,
        )
        inserted_total += inserted
        statuses.append(status)
        if status.status == "failed":
            failed_symbols.add(gap.symbol)

    return inserted_total, statuses, failed_symbols


def main() -> None:
    """Run the long-lived ingestion scheduler."""
    settings = load_settings()
    log_path = setup_logging(settings.ingestion.log_level)
    LOGGER.info("Logging to %s", log_path)
    run_scheduler(
        settings=settings,
        cycle_callback=lambda: run_once(settings, configure_logging=False),
    )
