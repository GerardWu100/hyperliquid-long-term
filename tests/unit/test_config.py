from pathlib import Path

import pytest

from hyperliquid_candles.config import Settings, load_settings


def test_load_settings_reads_env_and_commented_config(tmp_path: Path) -> None:
    """Configuration should combine ClickHouse env keys with TOML tunables."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "IVYDB_CLICKHOUSE_HOST=clickhouse",
                "IVYDB_CLICKHOUSE_PORT=8123",
                "IVYDB_CLICKHOUSE_USERNAME=reader",
                "IVYDB_CLICKHOUSE_PASSWORD=secret",
                "IVYDB_CLICKHOUSE_SECURE=true",
                "IVYDB_CLICKHOUSE_DATABASE=hyperliquid",
            ]
        ),
        encoding="utf-8",
    )

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "poll_interval_sec = 3600",
                'symbols_mode = "allowlist"',
                'symbols_allowlist = ["BTC", "ETH"]',
                'initial_backfill_start_time_utc = "2026-01-01T00:00:00Z"',
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, env_path=env_path)

    assert settings.clickhouse.host == "clickhouse"
    assert settings.clickhouse.port == 8123
    assert settings.clickhouse.secure is True
    assert settings.ingestion.poll_interval_sec == 3600
    assert settings.ingestion.symbols_mode == "allowlist"
    assert settings.ingestion.symbols_allowlist == ("BTC", "ETH")
    assert settings.ingestion.initial_backfill_start_time_utc == "2026-01-01T00:00:00Z"


def test_settings_reject_invalid_symbol_mode() -> None:
    """Only the documented symbol selection modes should be accepted."""
    with pytest.raises(ValueError, match="symbols_mode"):
        Settings.from_values(
            env_values={
                "IVYDB_CLICKHOUSE_HOST": "localhost",
                "IVYDB_CLICKHOUSE_PORT": "8123",
                "IVYDB_CLICKHOUSE_USERNAME": "user",
                "IVYDB_CLICKHOUSE_PASSWORD": "password",
                "IVYDB_CLICKHOUSE_SECURE": "false",
                "IVYDB_CLICKHOUSE_DATABASE": "hyperliquid",
            },
            config_values={"symbols_mode": "everything"},
        )
