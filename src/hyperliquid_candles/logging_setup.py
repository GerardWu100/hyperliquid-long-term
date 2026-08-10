"""Logging setup for service runs and local commands."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from hyperliquid_candles.config import data_dir


def setup_logging(log_level: str, logs_dir: Path | None = None) -> Path:
    """Configure console and file logging.

    Parameters
    ----------
    log_level:
        Standard Python logging level name.
    logs_dir:
        Optional log directory. Defaults to `logs/` at the project root.

    Returns
    -------
    Path
        Path to the main log file for this process.
    """
    resolved_logs_dir = logs_dir or data_dir() / "logs"
    resolved_logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = _next_log_path(resolved_logs_dir)
    error_log_path = log_path.with_name(f"{log_path.stem}_errors.log")

    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # `logging.Formatter` calls its converter with the record's own creation
    # time, so the converter must translate that argument. `time.gmtime` turns
    # the record timestamp into UTC calendar fields, which is what the "Z"
    # suffix in `datefmt` promises.
    formatter.converter = time.gmtime

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    error_handler = logging.FileHandler(error_log_path, encoding="utf-8")
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)
    return log_path


def _next_log_path(logs_dir: Path) -> Path:
    """Return a `YYYY-MM-DD_NNN.log` path that does not exist yet."""
    date_prefix = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    sequence = 1
    while True:
        candidate = logs_dir / f"{date_prefix}_{sequence:03d}.log"
        if not candidate.exists():
            return candidate
        sequence += 1
