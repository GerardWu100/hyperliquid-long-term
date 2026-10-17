"""Configuration loading for the Hyperliquid candle ingestion service.

The service has two configuration sources: `.env` for ClickHouse connection
details and `config.toml` for non-secret ingestion tunables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import tomllib
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Return the directory for writable runtime files such as ``logs/``.

    Defaults to the project root for local ``uv run`` commands. Set
    ``HL_DATA_DIR`` to relocate logs (and optional ``config.toml`` overrides)
    to another directory. The Docker service sets ``HL_DATA_DIR=/data`` and
    bind-mounts ``~/.containers/hyperliquid-candles`` there for logs only.

    ClickHouse secrets always load from ``PROJECT_ROOT / ".env"`` so one file in
    the repo root works for both local runs and Docker (compose bind-mounts it
    into the image at ``/app/.env``).

    This is resolved from the process environment rather than from ``.env`` on
    purpose: the loader needs to know where writable files live before it can
    read ``.env``, so that location cannot come from inside the secrets file.
    """
    override = os.environ.get("HL_DATA_DIR")
    return Path(override) if override else PROJECT_ROOT


def _builtin_config_path() -> Path:
    """Return the repo (or image) `config.toml` that holds every default."""
    return PROJECT_ROOT / "config.toml"


def _resolve_override_config_path(config_path: Path | None) -> Path | None:
    """Return the optional ``config.toml`` that overrides the built-in file.

    An explicit ``config_path`` wins. Otherwise ``HL_DATA_DIR/config.toml`` is
    used when it exists, so Docker operators can change tunables without
    rebuilding the image. ``None`` means the built-in file is used alone.
    """
    if config_path is not None:
        return config_path
    data_config = data_dir() / "config.toml"
    if (
        data_config.exists()
        and data_config.resolve() != _builtin_config_path().resolve()
    ):
        return data_config
    return None


def load_config_values(config_path: Path | None = None) -> dict[str, Any]:
    """Return built-in ``config.toml`` values with override keys merged on top.

    The override file only needs the keys it changes; every other key keeps
    its built-in value. Unknown keys in either file raise, so a misspelled
    option fails at startup instead of being ignored silently.

    Raises
    ------
    ValueError
        Raised when a file contains a key that is not an ingestion setting.
    """
    config_values: dict[str, Any] = {}
    override_path = _resolve_override_config_path(config_path)
    for path in (_builtin_config_path(), override_path):
        if path is None or not path.exists():
            continue
        file_values = tomllib.loads(path.read_text(encoding="utf-8"))
        unknown_keys = sorted(set(file_values) - _INGESTION_FIELD_NAMES)
        if unknown_keys:
            raise ValueError(f"Unknown keys in {path}: {unknown_keys}")
        config_values.update(file_values)
    return config_values


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
    """Runtime settings for REST polling, backfill, and monitoring.

    Defaults equal the values in the built-in ``config.toml`` (a unit test
    enforces this), so a missing key means the same thing everywhere.
    """

    poll_interval_sec: int = 1800
    overlap_candles: int = 5
    rest_horizon_candles: int = 5000
    weight_budget_per_min: int = 1000
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
    ) -> Settings:
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

        defaults = IngestionSettings()
        values: dict[str, Any] = {}
        for field in fields(IngestionSettings):
            raw_value = config_values.get(field.name, getattr(defaults, field.name))
            if field.name == "symbols_allowlist":
                if not isinstance(raw_value, (list, tuple)):
                    raise ValueError("symbols_allowlist must be a TOML list of strings")
                values[field.name] = tuple(str(symbol) for symbol in raw_value)
            else:
                # Coerce to the default's type (int or str) so TOML values like
                # `poll_interval_sec = "1800"` fail loudly or convert cleanly.
                values[field.name] = type(getattr(defaults, field.name))(raw_value)

        if values["symbols_mode"] not in {"all", "allowlist"}:
            raise ValueError("symbols_mode must be either 'all' or 'allowlist'")

        ingestion = IngestionSettings(**values)
        _validate_positive_tunables(ingestion)
        return cls(clickhouse=clickhouse, ingestion=ingestion)


def load_settings(
    config_path: Path | None = None,
    env_path: Path | None = None,
) -> Settings:
    """Load settings from project files and the current process environment.

    Parameters
    ----------
    config_path:
        Optional override ``config.toml``; defaults to ``HL_DATA_DIR/config.toml``
        when it exists. Its keys are merged over the built-in file.
    env_path:
        ClickHouse ``.env`` file; defaults to ``PROJECT_ROOT / ".env"``.
    """
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

    config_values = load_config_values(config_path)

    try:
        return Settings.from_values(env_values=merged_env, config_values=config_values)
    except ValueError as exc:
        if "Missing required environment variable" not in str(exc):
            raise
        raise ValueError(
            f"{exc}. Create {resolved_env_path} from .env.example "
            f"(for example: cp .env.example .env) and set ClickHouse connection values."
        ) from exc


_CLICKHOUSE_ENV_KEYS = (
    "IVYDB_CLICKHOUSE_HOST",
    "IVYDB_CLICKHOUSE_PORT",
    "IVYDB_CLICKHOUSE_USERNAME",
    "IVYDB_CLICKHOUSE_PASSWORD",
    "IVYDB_CLICKHOUSE_SECURE",
    "IVYDB_CLICKHOUSE_DATABASE",
)


_INGESTION_FIELD_NAMES = frozenset(field.name for field in fields(IngestionSettings))


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
        "rest_horizon_candles": ingestion.rest_horizon_candles,
        "weight_budget_per_min": ingestion.weight_budget_per_min,
        "request_timeout_sec": ingestion.request_timeout_sec,
        "max_retries": ingestion.max_retries,
        "batch_insert_max_rows": ingestion.batch_insert_max_rows,
    }
    for field_name, value in positive_fields.items():
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")
