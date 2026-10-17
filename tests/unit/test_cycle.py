from dataclasses import replace

import pytest
from hyperliquid_candles import app
from hyperliquid_candles.config import ClickHouseSettings, IngestionSettings, Settings
from hyperliquid_candles.hyperliquid.candles import INTERVAL_MS, Candle
from hyperliquid_candles.storage.runs import RUN_COLUMNS, SYMBOL_STATUS_COLUMNS


class FakeQueryResult:
    """ClickHouse query result with no rows (no watermarks, no gaps)."""

    result_rows: tuple[tuple[object, ...], ...] = ()


class RecordingClickHouse:
    """ClickHouse stand-in that answers every query with no rows and records inserts."""

    def __init__(self) -> None:
        self.inserts: dict[str, list[dict[str, object]]] = {}

    def query(self, query: str) -> FakeQueryResult:
        return FakeQueryResult()

    def insert(
        self, table: str, data: list[tuple[object, ...]], column_names: list[str]
    ) -> None:
        rows = [dict(zip(column_names, row, strict=True)) for row in data]
        self.inserts.setdefault(table.split(".")[-1], []).extend(rows)


class FakeHyperliquid:
    """Hyperliquid stand-in that lists BTC and ETH and returns one candle per request."""

    def __init__(self, meta_error: Exception | None = None) -> None:
        self.meta_error = meta_error

    def fetch_meta(self) -> dict[str, object]:
        if self.meta_error is not None:
            raise self.meta_error
        return {"universe": [{"name": "BTC"}, {"name": "ETH"}]}

    def fetch_candles(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        return [
            Candle(
                symbol=symbol,
                open_time_ms=end_ms,
                close_time_ms=end_ms + INTERVAL_MS - 1,
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
                trades=1,
            )
        ]


def _settings(**ingestion_overrides: object) -> Settings:
    return Settings(
        clickhouse=ClickHouseSettings(
            host="localhost",
            port=8123,
            username="default",
            password="password",
            secure=False,
            database="hyperliquid",
        ),
        ingestion=replace(IngestionSettings(), **ingestion_overrides),
    )


def test_unknown_allowlisted_symbol_is_skipped_not_fatal() -> None:
    """A misspelled allowlist entry is recorded as skipped while BTC still ingests."""
    clickhouse = RecordingClickHouse()
    result = app.run_ingestion_cycle(
        settings=_settings(symbols_mode="allowlist", symbols_allowlist=("BTC", "BTCC")),
        clickhouse_client=clickhouse,
        hyperliquid_client=FakeHyperliquid(),
    )

    assert (result.status, result.symbols_ok, result.symbols_skipped) == (
        "partial",
        1,
        1,
    )
    assert {row["symbol"] for row in clickhouse.inserts["candles_1m"]} == {"BTC"}
    statuses = {
        row["symbol"]: row for row in clickhouse.inserts["ingestion_symbol_status"]
    }
    assert statuses["BTCC"]["status"] == "skipped"
    assert statuses["BTC"]["status"] == "success"
    (run_row,) = clickhouse.inserts["ingestion_runs"]
    assert run_row["status"] == "partial"
    assert "BTCC" in run_row["error"]
    assert set(run_row) == set(RUN_COLUMNS)
    assert set(statuses["BTCC"]) == set(SYMBOL_STATUS_COLUMNS)


def test_all_unknown_allowlist_marks_cycle_failed() -> None:
    """Collecting nothing is a failed cycle, not a success with zero symbols."""
    clickhouse = RecordingClickHouse()
    result = app.run_ingestion_cycle(
        settings=_settings(symbols_mode="allowlist", symbols_allowlist=("FOO",)),
        clickhouse_client=clickhouse,
        hyperliquid_client=FakeHyperliquid(),
    )

    assert result.status == "failed"
    assert clickhouse.inserts["ingestion_runs"][0]["status"] == "failed"


def test_successful_cycle_has_empty_run_error() -> None:
    """A clean cycle stores an empty error string."""
    clickhouse = RecordingClickHouse()
    result = app.run_ingestion_cycle(
        settings=_settings(),
        clickhouse_client=clickhouse,
        hyperliquid_client=FakeHyperliquid(),
    )

    assert result.status == "success"
    assert clickhouse.inserts["ingestion_runs"][0]["error"] == ""


def test_cycle_level_failure_writes_failed_run_row_with_error() -> None:
    """A `meta` failure still leaves a failed `ingestion_runs` row, then re-raises."""
    clickhouse = RecordingClickHouse()
    with pytest.raises(RuntimeError):
        app.run_ingestion_cycle(
            settings=_settings(),
            clickhouse_client=clickhouse,
            hyperliquid_client=FakeHyperliquid(meta_error=RuntimeError("meta down")),
        )

    (run_row,) = clickhouse.inserts["ingestion_runs"]
    assert run_row["status"] == "failed"
    assert run_row["error"] == "RuntimeError: meta down"
