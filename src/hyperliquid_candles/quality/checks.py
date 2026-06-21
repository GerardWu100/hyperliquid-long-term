"""Ad-hoc ClickHouse data quality report for stored 1-minute candles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hyperliquid_candles.config import Settings, load_settings
from hyperliquid_candles.ingestion.gaps import gap_query
from hyperliquid_candles.logging_setup import setup_logging
from hyperliquid_candles.storage.clickhouse_client import wait_for_clickhouse


@dataclass(frozen=True)
class QualitySection:
    """Named query result included in the text quality report."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def build_quality_report(settings: Settings | None = None) -> str:
    """Run quality checks and return a plain-text report."""
    resolved_settings = settings or load_settings()
    validated = wait_for_clickhouse(
        clickhouse_settings=resolved_settings.clickhouse,
        ingestion_settings=resolved_settings.ingestion,
    )
    database = resolved_settings.clickhouse.database
    sections = [
        _run_query(
            validated.client, "latest_by_symbol", latest_by_symbol_query(database)
        ),
        _run_query(validated.client, "duplicate_keys", duplicate_keys_query(database)),
        _run_query(validated.client, "gaps", gap_query(database)),
        _run_query(validated.client, "daily_counts", daily_counts_query(database)),
        _run_query(validated.client, "parts", parts_query(database)),
        _run_query(validated.client, "last_runs", last_runs_query(database)),
    ]
    return render_text_report(sections)


def render_text_report(sections: list[QualitySection]) -> str:
    """Render query sections as a compact plain-text report."""
    lines: list[str] = ["Hyperliquid Candle Quality Report", ""]
    for section in sections:
        lines.append(f"## {section.name}")
        lines.append(" | ".join(section.columns))
        if not section.rows:
            lines.append("(no rows)")
        for row in section.rows:
            lines.append(" | ".join(str(value) for value in row))
        lines.append("")
    return "\n".join(lines)


def latest_by_symbol_query(database: str) -> str:
    """Return freshness query SQL."""
    return f"""
SELECT
    symbol,
    max(open_time) AS last_candle,
    dateDiff('minute', max(open_time), now64(3)) AS minutes_behind
FROM {database}.candles_1m
GROUP BY symbol
ORDER BY minutes_behind DESC
"""


def duplicate_keys_query(database: str) -> str:
    """Return duplicate raw-key query SQL."""
    return f"""
SELECT symbol, open_time, count() AS c
FROM {database}.candles_1m
GROUP BY symbol, open_time
HAVING c > 1
ORDER BY c DESC
LIMIT 50
"""


def daily_counts_query(database: str) -> str:
    """Return daily row-count coverage query SQL."""
    return f"""
SELECT toDate(open_time) AS d, symbol, count() AS rows
FROM {database}.candles_1m
GROUP BY d, symbol
ORDER BY d DESC, symbol
LIMIT 500
"""


def parts_query(database: str) -> str:
    """Return ClickHouse active-parts health query SQL."""
    return f"""
SELECT partition, count() AS parts, formatReadableSize(sum(bytes_on_disk)) AS size
FROM system.parts
WHERE database = '{database}' AND table = 'candles_1m' AND active
GROUP BY partition
ORDER BY parts DESC
"""


def last_runs_query(database: str) -> str:
    """Return recent ingestion run metadata query SQL."""
    return f"""
SELECT started_at, finished_at, status, symbols_ok, symbols_failed, candles_inserted, error
FROM {database}.ingestion_runs
ORDER BY started_at DESC
LIMIT 20
"""


def _run_query(client: object, name: str, query: str) -> QualitySection:
    """Execute one ClickHouse query and normalize the result object."""
    result = client.query(query)
    columns = tuple(str(column) for column in result.column_names)
    rows = tuple(tuple(row) for row in result.result_rows)
    return QualitySection(name=name, columns=columns, rows=rows)


def main() -> None:
    """Run the quality report and print it to stdout."""
    settings = load_settings()
    setup_logging(settings.ingestion.log_level)
    print(build_quality_report(settings))
