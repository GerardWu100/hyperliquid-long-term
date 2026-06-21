"""Console entrypoint for exactly one ingestion cycle."""

from __future__ import annotations

from hyperliquid_candles.app import run_once


def main() -> None:
    """Run one ingestion cycle using project configuration."""
    result = run_once()
    print(
        "run_id={run_id} status={status} symbols_ok={symbols_ok} "
        "symbols_failed={symbols_failed} candles_inserted={candles_inserted}".format(
            run_id=result.run_id,
            status=result.status,
            symbols_ok=result.symbols_ok,
            symbols_failed=result.symbols_failed,
            candles_inserted=result.candles_inserted,
        )
    )
