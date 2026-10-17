import tomllib
from dataclasses import fields
from pathlib import Path

import pytest
from hyperliquid_candles import config as config_module
from hyperliquid_candles.config import IngestionSettings, Settings, load_settings


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


def test_load_settings_reads_repo_root_env_when_hl_data_dir_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.env` in the repo root should work even when logs use HL_DATA_DIR."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HL_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "IVYDB_CLICKHOUSE_HOST=clickhouse",
                "IVYDB_CLICKHOUSE_PORT=8123",
                "IVYDB_CLICKHOUSE_USERNAME=reader",
                "IVYDB_CLICKHOUSE_PASSWORD=secret",
                "IVYDB_CLICKHOUSE_SECURE=false",
                "IVYDB_CLICKHOUSE_DATABASE=hyperliquid",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text('symbols_mode = "all"\n', encoding="utf-8")

    settings = load_settings()

    assert settings.clickhouse.host == "clickhouse"
    assert settings.clickhouse.port == 8123


def test_load_settings_prefers_hl_data_dir_config_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An optional config.toml under HL_DATA_DIR should override the repo copy."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HL_DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "IVYDB_CLICKHOUSE_HOST=clickhouse",
                "IVYDB_CLICKHOUSE_PORT=8123",
                "IVYDB_CLICKHOUSE_USERNAME=reader",
                "IVYDB_CLICKHOUSE_PASSWORD=secret",
                "IVYDB_CLICKHOUSE_SECURE=false",
                "IVYDB_CLICKHOUSE_DATABASE=hyperliquid",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        "poll_interval_sec = 1800\n", encoding="utf-8"
    )
    (data_dir / "config.toml").write_text("poll_interval_sec = 900\n", encoding="utf-8")

    settings = load_settings()

    assert settings.ingestion.poll_interval_sec == 900


def test_load_settings_hints_when_repo_root_env_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing repo-root `.env` should mention creating it from the example file."""
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    with pytest.raises(ValueError, match=r"cp \.env\.example \.env"):
        load_settings()


def test_builtin_config_toml_matches_code_defaults() -> None:
    """Every built-in config.toml value equals the IngestionSettings default."""
    builtin_values = tomllib.loads(
        (config_module.PROJECT_ROOT / "config.toml").read_text(encoding="utf-8")
    )
    defaults = IngestionSettings()

    assert set(builtin_values) == {field.name for field in fields(IngestionSettings)}
    for key, value in builtin_values.items():
        expected = tuple(value) if isinstance(value, list) else value
        assert getattr(defaults, key) == expected, key


def test_partial_override_keeps_builtin_values_for_missing_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An override with one key changes only that key; the rest come from the built-in file."""
    monkeypatch.setenv("HL_DATA_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text("poll_interval_sec = 600\n", encoding="utf-8")

    config_values = config_module.load_config_values()

    assert config_values["poll_interval_sec"] == 600
    assert config_values["weight_budget_per_min"] == 1000
    assert config_values["log_level"] == "INFO"


def test_override_with_unknown_key_fails_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misspelled option must raise instead of being ignored."""
    monkeypatch.setenv("HL_DATA_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        "poll_interval_secs = 600\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="poll_interval_secs"):
        config_module.load_config_values()
