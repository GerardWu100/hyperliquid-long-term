import logging
from pathlib import Path

from hyperliquid_candles import logging_setup


def test_setup_logging_prunes_old_runs_and_quiets_httpx(
    tmp_path: Path, monkeypatch
) -> None:
    """Old run files beyond the keep limit are deleted; httpx drops to WARNING at INFO."""
    monkeypatch.setattr(logging_setup, "LOG_RUNS_KEPT", 2)
    for sequence in (1, 2, 3):
        (tmp_path / f"2026-01-01_{sequence:03d}.log").write_text("x")
        (tmp_path / f"2026-01-01_{sequence:03d}_errors.log").write_text("x")
    (tmp_path / "2026-01-01_001.log.1").write_text("x")
    (tmp_path / "notes.txt").write_text("keep")

    log_path = logging_setup.setup_logging("INFO", logs_dir=tmp_path)

    remaining = sorted(path.name for path in tmp_path.iterdir())
    assert remaining == sorted(
        [
            "2026-01-01_003.log",
            "2026-01-01_003_errors.log",
            "notes.txt",
            log_path.name,
            log_path.name.replace(".log", "_errors.log"),
        ]
    )
    assert logging.getLogger("httpx").level == logging.WARNING
    logging.getLogger().handlers.clear()
