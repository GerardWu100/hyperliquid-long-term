"""Logging setup for service runs and local commands."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from hyperliquid_candles.config import data_dir

# Size at which a log file rolls over to `<name>.1`, `<name>.2`, ...
LOG_MAX_BYTES = 10 * 1024 * 1024
# Rolled-over files kept per log file; older ones are deleted.
LOG_BACKUP_COUNT = 5
# Process runs (one `YYYY-MM-DD_NNN` set of files each) kept in the logs folder.
# Older runs are deleted at startup so a crash-restart loop cannot fill the disk.
LOG_RUNS_KEPT = 30
# Chatty third-party loggers that log one INFO line per HTTP request.
HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")
# `YYYY-MM-DD_NNN` prefix shared by every file of one process run.
LOG_RUN_PREFIX_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}_\d{3})(_errors)?\.log(\.\d+)?$"
)


def setup_logging(log_level: str, logs_dir: Path | None = None) -> Path:
    """Configure console and size-rotated file logging.

    Each process writes ``YYYY-MM-DD_NNN.log`` plus ``YYYY-MM-DD_NNN_errors.log``.
    Both roll over at ``LOG_MAX_BYTES`` and keep ``LOG_BACKUP_COUNT`` old files.
    Only the newest ``LOG_RUNS_KEPT`` process runs stay in the folder. httpx and
    httpcore log at WARNING unless ``log_level`` is DEBUG, because at INFO they
    write one line per Hyperliquid request.

    Parameters
    ----------
    log_level:
        Standard Python logging level name.
    logs_dir:
        Optional log directory. Defaults to `logs/` under `data_dir()`.

    Returns
    -------
    Path
        Path to the main log file for this process.
    """
    resolved_logs_dir = logs_dir or data_dir() / "logs"
    resolved_logs_dir.mkdir(parents=True, exist_ok=True)
    _prune_old_log_runs(resolved_logs_dir, runs_kept=LOG_RUNS_KEPT - 1)
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

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    error_handler = RotatingFileHandler(
        error_log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)

    http_client_level = logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    for logger_name in HTTP_CLIENT_LOGGERS:
        logging.getLogger(logger_name).setLevel(http_client_level)
    return log_path


def _prune_old_log_runs(logs_dir: Path, runs_kept: int) -> None:
    """Delete every file of all but the newest ``runs_kept`` process runs.

    Run prefixes sort by date then sequence number, so name order is age order.
    Files that do not match the log naming pattern are left alone.
    """
    files_by_run: dict[str, list[Path]] = {}
    for path in logs_dir.iterdir():
        match = LOG_RUN_PREFIX_PATTERN.match(path.name)
        if match and path.is_file():
            files_by_run.setdefault(match.group(1), []).append(path)
    old_runs = sorted(files_by_run)[: max(len(files_by_run) - runs_kept, 0)]
    for run_prefix in old_runs:
        for path in files_by_run[run_prefix]:
            path.unlink(missing_ok=True)


def _next_log_path(logs_dir: Path) -> Path:
    """Return a `YYYY-MM-DD_NNN.log` path that does not exist yet."""
    date_prefix = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    sequence = 1
    while True:
        candidate = logs_dir / f"{date_prefix}_{sequence:03d}.log"
        if not candidate.exists():
            return candidate
        sequence += 1
