"""ClickHouse client construction and readiness validation."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from hyperliquid_candles.config import ClickHouseSettings, IngestionSettings

LOGGER = logging.getLogger(__name__)


class ClickHouseDependencyNotReady(RuntimeError):
    """Raised when ClickHouse is temporarily unreachable."""


class ClickHouseConfigurationError(RuntimeError):
    """Raised when ClickHouse is reachable but permissions or schema fail."""


@dataclass(frozen=True)
class ValidatedClickHouse:
    """Connected ClickHouse client plus server version string."""

    client: Client
    version: str


def connect_clickhouse(settings: ClickHouseSettings) -> Client:
    """Open a ClickHouse HTTP client from typed settings.

    The client is intentionally opened against the server's default database
    rather than the target database. All DDL, inserts, and queries in this
    project use fully-qualified ``{database}.table`` names, so no default
    database is needed. Connecting without the target database also lets startup
    run ``CREATE DATABASE IF NOT EXISTS`` on a fresh ClickHouse where the target
    database does not exist yet, instead of failing during the readiness probe.
    """
    return clickhouse_connect.get_client(
        host=settings.host,
        port=settings.port,
        username=settings.username,
        password=settings.password,
        secure=settings.secure,
    )


def wait_for_clickhouse(
    clickhouse_settings: ClickHouseSettings,
    ingestion_settings: IngestionSettings,
) -> ValidatedClickHouse:
    """Wait indefinitely for ClickHouse dependency readiness.

    Connection-style failures retry with exponential backoff and jitter. Once a
    client is reachable, permission or SQL errors are allowed to surface to the
    caller because those require human action.
    """
    sleep_seconds = ingestion_settings.readiness_backoff_initial_sec

    while True:
        try:
            client = connect_clickhouse(clickhouse_settings)
            client.query("SELECT 1")
            version = str(client.query("SELECT version()").result_rows[0][0])
            LOGGER.info("Connected to ClickHouse version %s", version)
            return ValidatedClickHouse(client=client, version=version)
        except Exception as exc:
            if not _looks_like_dependency_not_ready(exc):
                raise ClickHouseConfigurationError(str(exc)) from exc

            LOGGER.warning("ClickHouse not ready: %s", exc)
            jitter = random.uniform(0, ingestion_settings.readiness_backoff_jitter_sec)
            time.sleep(sleep_seconds + jitter)
            sleep_seconds = min(
                sleep_seconds * 2,
                ingestion_settings.readiness_backoff_max_sec,
            )


def _looks_like_dependency_not_ready(exc: Exception) -> bool:
    """Classify common network/startup failures as dependency-not-ready."""
    message = str(exc).lower()
    retryable_fragments = (
        "connection refused",
        "connect timeout",
        "connection timed out",
        "name or service not known",
        "temporary failure in name resolution",
        "failed to establish",
        "remote end closed connection",
    )
    return any(fragment in message for fragment in retryable_fragments)
