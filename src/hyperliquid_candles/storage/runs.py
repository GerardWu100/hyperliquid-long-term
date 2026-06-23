"""Writers for ingestion run metadata tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4


class InsertClient(Protocol):
    """Minimal ClickHouse insert protocol used by metadata writers."""

    def insert(
        self,
        table: str,
        data: list[tuple[object, ...]],
        column_names: list[str],
    ) -> object:
        """Insert rows into ClickHouse."""


@dataclass(frozen=True)
class RunSummary:
    """One row for `ingestion_runs`."""

    run_id: UUID
    mode: str
    started_at: datetime
    finished_at: datetime | None
    symbols_total: int
    symbols_ok: int
    symbols_failed: int
    candles_inserted: int
    status: str
    error: str = ""


@dataclass(frozen=True)
class SymbolStatus:
    """One row for `ingestion_symbol_status`."""

    run_id: UUID
    symbol: str
    mode: str
    requested_start: datetime
    requested_end: datetime
    effective_start: datetime
    rows_fetched: int
    rows_inserted: int
    status: str
    error: str = ""


RUN_COLUMNS = [
    "run_id",
    "mode",
    "started_at",
    "finished_at",
    "symbols_total",
    "symbols_ok",
    "symbols_failed",
    "candles_inserted",
    "status",
    "error",
]

SYMBOL_STATUS_COLUMNS = [
    "run_id",
    "symbol",
    "mode",
    "requested_start",
    "requested_end",
    "effective_start",
    "rows_fetched",
    "rows_inserted",
    "status",
    "error",
]


def new_run_id() -> UUID:
    """Create a unique run identifier."""
    return uuid4()


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(tz=UTC)


def ms_to_datetime(value_ms: int) -> datetime:
    """Convert Unix epoch milliseconds to timezone-aware UTC datetime."""
    return datetime.fromtimestamp(value_ms / 1000, tz=UTC)


def insert_run_summary(
    client: InsertClient, database: str, summary: RunSummary
) -> None:
    """Insert one ingestion run summary row."""
    row = (
        summary.run_id,
        summary.mode,
        summary.started_at,
        summary.finished_at,
        summary.symbols_total,
        summary.symbols_ok,
        summary.symbols_failed,
        summary.candles_inserted,
        summary.status,
        summary.error,
    )
    client.insert(
        table=f"{database}.ingestion_runs",
        data=[row],
        column_names=RUN_COLUMNS,
    )


def insert_symbol_statuses(
    client: InsertClient,
    database: str,
    statuses: list[SymbolStatus],
) -> None:
    """Insert per-symbol cycle outcomes."""
    if not statuses:
        return

    rows = [
        (
            status.run_id,
            status.symbol,
            status.mode,
            status.requested_start,
            status.requested_end,
            status.effective_start,
            status.rows_fetched,
            status.rows_inserted,
            status.status,
            status.error,
        )
        for status in statuses
    ]
    client.insert(
        table=f"{database}.ingestion_symbol_status",
        data=rows,
        column_names=SYMBOL_STATUS_COLUMNS,
    )
