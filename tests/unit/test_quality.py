from hyperliquid_candles.quality.checks import latest_by_symbol_query


def test_latest_by_symbol_query_filters_to_active_symbols() -> None:
    """Freshness SQL should ignore stored symbols that are no longer active."""
    query = latest_by_symbol_query(
        database="market_data",
        active_symbols=("BTC", "ETH"),
    )

    assert "FROM market_data.candles_1m" in query
    assert "WHERE symbol IN ('BTC', 'ETH')" in query
    assert "GROUP BY symbol" in query
