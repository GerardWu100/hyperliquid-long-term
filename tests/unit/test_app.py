from pathlib import Path
from uuid import UUID

import hyperliquid_candles.app as app
from hyperliquid_candles.config import ClickHouseSettings, IngestionSettings, Settings


class FakeValidatedClickHouse:
    """Small ClickHouse readiness result used to avoid network calls in app tests."""

    client = object()
    version = "test-version"


class FakeHyperliquidClient:
    """Small Hyperliquid client replacement that records close calls."""

    was_closed = False

    def __init__(
        self,
        timeout_sec: int,
        max_retries: int,
        rate_limiter: object,
    ) -> None:
        """Accept the production constructor shape without opening HTTP sockets."""
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.rate_limiter = rate_limiter

    def close(self) -> None:
        """Record that the production cleanup path closed the client."""
        type(self).was_closed = True


def test_run_once_can_skip_logging_setup_for_scheduler_cycles(monkeypatch) -> None:
    """Scheduler cycles should not recreate log files after process startup."""
    settings = Settings(
        clickhouse=ClickHouseSettings(
            host="localhost",
            port=8123,
            username="default",
            password="password",
            secure=False,
            database="hyperliquid",
        ),
        ingestion=IngestionSettings(),
    )
    logging_calls: list[str] = []

    def fake_setup_logging(log_level: str) -> Path:
        """Record unexpected logging setup calls instead of touching the filesystem."""
        logging_calls.append(log_level)
        return Path("logs/test.log")

    def fake_run_ingestion_cycle(
        settings: Settings,
        clickhouse_client: object,
        hyperliquid_client: object,
    ) -> app.CycleResult:
        """Return a deterministic cycle result after dependency setup completes."""
        return app.CycleResult(
            run_id=UUID("00000000-0000-0000-0000-000000000001"),
            symbols_total=1,
            symbols_ok=1,
            symbols_failed=0,
            candles_inserted=0,
            status="success",
        )

    monkeypatch.setattr(app, "setup_logging", fake_setup_logging)
    monkeypatch.setattr(
        app, "wait_for_clickhouse", lambda **kwargs: FakeValidatedClickHouse()
    )
    monkeypatch.setattr(app, "create_schema", lambda **kwargs: None)
    monkeypatch.setattr(app, "HyperliquidClient", FakeHyperliquidClient)
    monkeypatch.setattr(app, "run_ingestion_cycle", fake_run_ingestion_cycle)

    result = app.run_once(settings=settings, configure_logging=False)

    assert result.status == "success"
    assert logging_calls == []
    assert FakeHyperliquidClient.was_closed is True
