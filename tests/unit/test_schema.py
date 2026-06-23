"""Unit tests for ClickHouse schema bootstrap behavior."""

from __future__ import annotations

import pytest

from hyperliquid_candles.storage.schema import create_schema, schema_statements


class FakeClient:
    """Records ClickHouse commands and can simulate permission failures."""

    def __init__(self, *, deny_create_database: bool = False) -> None:
        """Configure whether CREATE DATABASE should raise ACCESS_DENIED."""
        self.deny_create_database = deny_create_database
        self.commands: list[str] = []

    def command(self, query: str) -> object:
        """Record executed SQL and optionally reject CREATE DATABASE."""
        self.commands.append(query)
        if self.deny_create_database and query.startswith("CREATE DATABASE"):
            raise FakeAccessDeniedError(
                "Code: 497. DB::Exception: Not enough privileges. "
                "CREATE DATABASE ON hyperliquid.*",
                code=497,
            )
        return None


class FakeAccessDeniedError(Exception):
    """Minimal stand-in for clickhouse_connect DatabaseError in unit tests."""

    def __init__(self, message: str, *, code: int) -> None:
        """Store ClickHouse-style error metadata on the exception instance."""
        super().__init__(message)
        self.code = code


def test_create_schema_runs_table_ddl_when_create_database_is_denied() -> None:
    """Database-scoped users should still bootstrap tables when the DB exists."""
    client = FakeClient(deny_create_database=True)

    create_schema(client=client, database="hyperliquid")

    assert client.commands[0].startswith("CREATE DATABASE IF NOT EXISTS hyperliquid")
    assert client.commands[1:] == schema_statements("hyperliquid")


def test_create_schema_propagates_unrelated_database_errors() -> None:
    """Only CREATE DATABASE permission failures are treated as skippable."""

    class BrokenClient:
        def command(self, query: str) -> object:
            raise RuntimeError("connection reset")

    with pytest.raises(RuntimeError, match="connection reset"):
        create_schema(client=BrokenClient(), database="hyperliquid")
