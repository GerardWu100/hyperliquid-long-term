"""Loop scheduler with startup catch-up and graceful shutdown."""

from __future__ import annotations

import logging
import random
import signal
import time
from collections.abc import Callable

from hl_candles.config import Settings

LOGGER = logging.getLogger(__name__)


def run_scheduler(settings: Settings, cycle_callback: Callable[[], object]) -> None:
    """Run one immediate catch-up cycle, then repeat on the configured cadence."""
    stop_requested = False

    def _request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        LOGGER.info("Received signal %s; stopping after current sleep or cycle", signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    while not stop_requested:
        cycle_callback()
        jitter_seconds = random.uniform(0, min(30, settings.ingestion.poll_interval_sec / 10))
        sleep_seconds = settings.ingestion.poll_interval_sec + jitter_seconds
        LOGGER.info("Sleeping %.1f seconds before next ingestion cycle", sleep_seconds)

        deadline = time.monotonic() + sleep_seconds
        while not stop_requested and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
