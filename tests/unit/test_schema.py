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


def test_candles_schema_uses_benchmarked_lossless_codecs() -> None:
    """The raw candle table should use the benchmarked lossless codecs.

    The compression benchmark showed that Hyperliquid-style crypto prices
    compress best with first differences followed by ZSTD, while noisy
    fractional volume should stay as raw Float64 bytes passed directly to ZSTD.
    This test guards against reintroducing float predictor codecs such as
    Gorilla, which were larger in the measured benchmark.
    """
    candles_ddl = schema_statements("hyperliquid")[0]

    assert (
        "open_time   DateTime64(3, 'UTC')  CODEC(DoubleDelta, ZSTD(12))" in candles_ddl
    )
    assert (
        "close_time  DateTime64(3, 'UTC')  CODEC(DoubleDelta, ZSTD(12))" in candles_ddl
    )
    assert "open        Float64               CODEC(Delta, ZSTD(12))" in candles_ddl
    assert "high        Float64               CODEC(Delta, ZSTD(12))" in candles_ddl
    assert "low         Float64               CODEC(Delta, ZSTD(12))" in candles_ddl
    assert "close       Float64               CODEC(Delta, ZSTD(12))" in candles_ddl
    assert "volume      Float64               CODEC(ZSTD(12))" in candles_ddl
    assert "trades      UInt32                CODEC(T64, ZSTD(12))" in candles_ddl
    assert "Gorilla" not in candles_ddl


def test_schema_statements_do_not_modify_existing_tables() -> None:
    """Schema bootstrap should not alter existing ClickHouse table metadata."""
    statements = schema_statements("hyperliquid")

    assert len(statements) == 3
    assert all("ALTER TABLE" not in statement for statement in statements)
