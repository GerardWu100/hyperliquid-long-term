"""Ad-hoc ClickHouse data quality report for stored 1-minute candles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hyperliquid_candles.config import IngestionSettings, Settings, load_settings
from hyperliquid_candles.hyperliquid.client import HyperliquidClient
from hyperliquid_candles.hyperliquid.universe import select_symbols
from hyperliquid_candles.ingestion.gaps import gap_query
from hyperliquid_candles.logging_setup import setup_logging
from hyperliquid_candles.ratelimit import TokenBucket
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
    rate_limiter = TokenBucket(
        tokens_per_minute=resolved_settings.ingestion.weight_budget_per_min,
    )
    hyperliquid = HyperliquidClient(
        timeout_sec=resolved_settings.ingestion.request_timeout_sec,
        max_retries=resolved_settings.ingestion.max_retries,
        rate_limiter=rate_limiter,
    )
    try:
        meta_response = hyperliquid.fetch_meta()
    finally:
        hyperliquid.close()

    active_symbols = select_symbols(
        meta_response=meta_response,
        symbols_mode=resolved_settings.ingestion.symbols_mode,
        symbols_allowlist=resolved_settings.ingestion.symbols_allowlist,
    )
    database = resolved_settings.clickhouse.database
    latest_section = _run_query(
        validated.client,
        "latest_by_symbol",
        latest_by_symbol_query(database, active_symbols),
    )
    sections = [
        latest_section,
        _freshness_alert_section(latest_section, resolved_settings.ingestion),
        _run_query(validated.client, "duplicate_keys", duplicate_keys_query(database)),
        _run_query(validated.client, "gaps", gap_query(database)),
        _run_query(validated.client, "daily_counts", daily_counts_query(database)),
        _run_query(validated.client, "parts", parts_query(database)),
        _run_query(validated.client, "last_runs", last_runs_query(database)),
    ]
    return render_text_report(sections)


def classify_freshness(minutes_behind: float, ingestion: IngestionSettings) -> str:
    """Map a per-symbol staleness in minutes to an alert severity.

    Severity rises as stored data falls further behind the latest closed candle.
    The `critical` tier is tuned to fire before staleness reaches Hyperliquid's
    configured REST recovery window (about 83 hours for 5,000 continuous
    one-minute slots), beyond which missing candles may no longer be available.
    """
    if minutes_behind >= ingestion.alert_critical_min:
        return "critical"
    if minutes_behind >= ingestion.alert_urgent_min:
        return "urgent"
    if minutes_behind >= ingestion.alert_serious_min:
        return "serious"
    if minutes_behind >= ingestion.alert_warn_min:
        return "warning"
    return "ok"


def _freshness_alert_section(
    latest_section: QualitySection, ingestion: IngestionSettings
) -> QualitySection:
    """Build a severity-labelled view of symbols that are behind the warn threshold.

    Reuses the already-fetched `latest_by_symbol` rows (symbol, last_candle,
    minutes_behind) instead of issuing a second query.
    """
    alert_rows: list[tuple[Any, ...]] = []
    for symbol, last_candle, minutes_behind in latest_section.rows:
        severity = classify_freshness(float(minutes_behind), ingestion)
        if severity != "ok":
            alert_rows.append((symbol, last_candle, minutes_behind, severity))

    alert_rows.sort(key=lambda row: float(row[2]), reverse=True)
    return QualitySection(
        name="freshness_alerts",
        columns=("symbol", "last_candle", "minutes_behind", "severity"),
        rows=tuple(alert_rows),
    )


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


def latest_by_symbol_query(database: str, active_symbols: tuple[str, ...]) -> str:
    """Return freshness query SQL for currently active symbols only."""
    active_filter = _symbol_filter_sql(active_symbols)
    return f"""
SELECT
    symbol,
    max(open_time) AS last_candle,
    dateDiff('minute', max(open_time), now64(3)) AS minutes_behind
FROM {database}.candles_1m
WHERE {active_filter}
GROUP BY symbol
ORDER BY minutes_behind DESC
"""


def _symbol_filter_sql(active_symbols: tuple[str, ...]) -> str:
    """Return a ClickHouse filter for the active symbol tuple.

    The quality report fetches the active universe from Hyperliquid before this
    query runs. Symbols not in that universe may remain valid historical data,
    but they should not create live freshness alerts after delisting.
    """
    if not active_symbols:
        return "0"

    quoted_symbols: list[str] = []
    for symbol in active_symbols:
        # ClickHouse string literals use single quotes. Doubling embedded single
        # quotes keeps the query valid even if an exchange symbol ever contains
        # one, although normal Hyperliquid symbols are simple names like BTC.
        escaped_symbol = symbol.replace("'", "''")
        quoted_symbols.append(f"'{escaped_symbol}'")

    symbol_list = ", ".join(quoted_symbols)
    return f"symbol IN ({symbol_list})"


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
