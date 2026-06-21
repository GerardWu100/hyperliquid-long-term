"""Configuration loading for the Hyperliquid candle ingestion service.

The service has two configuration sources: `.env` for ClickHouse connection
details and `config.toml` for non-secret ingestion tunables.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ClickHouseSettings:
    """Connection settings for an existing ClickHouse HTTP endpoint.

    Parameters
    ----------
    host:
        Host name or IP address reachable from the current runtime.
    port:
        ClickHouse HTTP interface port.
    username:
        ClickHouse user name.
    password:
        ClickHouse password.
    secure:
        True when HTTPS/TLS should be used.
    database:
        Target database that stores Hyperliquid tables.
    """

    host: str
    port: int
    username: str
    password: str
    secure: bool
    database: str


@dataclass(frozen=True)
class IngestionSettings:
    """Runtime settings for REST polling, backfill, and monitoring."""

    poll_interval_sec: int = 900
    overlap_candles: int = 5
    rest_horizon_min: int = 5000
    weight_budget_per_min: int = 900
    request_timeout_sec: int = 30
    max_retries: int = 4
    symbols_mode: str = "all"
    symbols_allowlist: tuple[str, ...] = ()
    initial_backfill_start_time_utc: str = ""
    batch_insert_max_rows: int = 100000
    log_level: str = "INFO"
    readiness_backoff_initial_sec: int = 2
    readiness_backoff_max_sec: int = 60
    readiness_backoff_jitter_sec: int = 1
    alert_warn_min: int = 60
    alert_serious_min: int = 720
    alert_urgent_min: int = 2880
    alert_critical_min: int = 4320


@dataclass(frozen=True)
class Settings:
    """Complete application settings for one service process."""

    clickhouse: ClickHouseSettings
    ingestion: IngestionSettings

    @classmethod
    def from_values(
        cls,
        env_values: dict[str, str | None],
        config_values: dict[str, Any],
    ) -> "Settings":
        """Build settings from already-loaded environment and TOML values.

        Raises
        ------
        ValueError
            Raised when required ClickHouse values are absent or a tunable has
            an invalid value.
        """
        clickhouse = ClickHouseSettings(
            host=_required_env(env_values, "IVYDB_CLICKHOUSE_HOST"),
            port=int(_required_env(env_values, "IVYDB_CLICKHOUSE_PORT")),
            username=_required_env(env_values, "IVYDB_CLICKHOUSE_USERNAME"),
            password=_required_env(env_values, "IVYDB_CLICKHOUSE_PASSWORD"),
            secure=_parse_bool(_required_env(env_values, "IVYDB_CLICKHOUSE_SECURE")),
            database=_required_env(env_values, "IVYDB_CLICKHOUSE_DATABASE"),
        )

        symbols_mode = str(config_values.get("symbols_mode", "all"))
        if symbols_mode not in {"all", "allowlist"}:
            raise ValueError("symbols_mode must be either 'all' or 'allowlist'")

        allowlist_values = config_values.get("symbols_allowlist", [])
        if not isinstance(allowlist_values, list):
            raise ValueError("symbols_allowlist must be a TOML list of strings")

        ingestion = IngestionSettings(
            poll_interval_sec=int(config_values.get("poll_interval_sec", 900)),
            overlap_candles=int(config_values.get("overlap_candles", 5)),
            rest_horizon_min=int(config_values.get("rest_horizon_min", 5000)),
            weight_budget_per_min=int(config_values.get("weight_budget_per_min", 900)),
            request_timeout_sec=int(config_values.get("request_timeout_sec", 30)),
            max_retries=int(config_values.get("max_retries", 4)),
            symbols_mode=symbols_mode,
            symbols_allowlist=tuple(str(symbol) for symbol in allowlist_values),
            initial_backfill_start_time_utc=str(
                config_values.get("initial_backfill_start_time_utc", "")
            ),
            batch_insert_max_rows=int(
                config_values.get("batch_insert_max_rows", 100000)
            ),
            log_level=str(config_values.get("log_level", "INFO")),
            readiness_backoff_initial_sec=int(
                config_values.get("readiness_backoff_initial_sec", 2)
            ),
            readiness_backoff_max_sec=int(
                config_values.get("readiness_backoff_max_sec", 60)
            ),
            readiness_backoff_jitter_sec=int(
                config_values.get("readiness_backoff_jitter_sec", 1)
            ),
            alert_warn_min=int(config_values.get("alert_warn_min", 60)),
            alert_serious_min=int(config_values.get("alert_serious_min", 720)),
            alert_urgent_min=int(config_values.get("alert_urgent_min", 2880)),
            alert_critical_min=int(config_values.get("alert_critical_min", 4320)),
        )

        _validate_positive_tunables(ingestion)
        return cls(clickhouse=clickhouse, ingestion=ingestion)


def load_settings(
    config_path: Path | None = None,
    env_path: Path | None = None,
) -> Settings:
    """Load settings from project files and the current process environment."""
    resolved_config_path = config_path or PROJECT_ROOT / "config.toml"
    resolved_env_path = env_path or PROJECT_ROOT / ".env"

    # Precedence: the `.env` file is the authoritative source for ClickHouse
    # connection values; the process environment only fills keys the file omits.
    # This is deliberate. On a host whose shell already exports these variables,
    # a value can be silently corrupted (for example a password containing `$`
    # mangled by shell expansion). Letting the file win keeps the documented
    # `.env` as the single source of truth and makes loading deterministic.
    file_env = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
    merged_env: dict[str, str | None] = {
        key: os.environ[key] for key in _CLICKHOUSE_ENV_KEYS if key in os.environ
    }
    merged_env.update(file_env)

    config_values: dict[str, Any] = {}
    if resolved_config_path.exists():
        config_values = tomllib.loads(resolved_config_path.read_text(encoding="utf-8"))

    return Settings.from_values(env_values=merged_env, config_values=config_values)


_CLICKHOUSE_ENV_KEYS = (
    "IVYDB_CLICKHOUSE_HOST",
    "IVYDB_CLICKHOUSE_PORT",
    "IVYDB_CLICKHOUSE_USERNAME",
    "IVYDB_CLICKHOUSE_PASSWORD",
    "IVYDB_CLICKHOUSE_SECURE",
    "IVYDB_CLICKHOUSE_DATABASE",
)


def _required_env(env_values: dict[str, str | None], key: str) -> str:
    """Return a required environment value or raise a clear configuration error."""
    value = env_values.get(key)
    if value is None or value == "":
        raise ValueError(f"Missing required environment variable: {key}")
    return value


def _parse_bool(raw_value: str) -> bool:
    """Parse boolean strings used in `.env` files."""
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {raw_value!r}")


def _validate_positive_tunables(ingestion: IngestionSettings) -> None:
    """Validate tunables where zero or negative values would break scheduling."""
    positive_fields = {
        "poll_interval_sec": ingestion.poll_interval_sec,
        "overlap_candles": ingestion.overlap_candles,
        "rest_horizon_min": ingestion.rest_horizon_min,
        "weight_budget_per_min": ingestion.weight_budget_per_min,
        "request_timeout_sec": ingestion.request_timeout_sec,
        "max_retries": ingestion.max_retries,
        "batch_insert_max_rows": ingestion.batch_insert_max_rows,
    }
    for field_name, value in positive_fields.items():
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")
