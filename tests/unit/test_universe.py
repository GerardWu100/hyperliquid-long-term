import logging

import pytest
from hyperliquid_candles.hyperliquid.universe import select_symbols

META = {
    "universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "OLD", "isDelisted": True}]
}


def test_allowlist_skips_unknown_and_delisted_symbols_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One bad allowlist entry must not stop the other symbols from being selected."""
    with caplog.at_level(logging.WARNING):
        selection = select_symbols(
            meta_response=META,
            symbols_mode="allowlist",
            symbols_allowlist=("ETH", "BTCC", "OLD", "BTC"),
        )

    assert selection.selected == ("ETH", "BTC")
    assert selection.unknown == ("BTCC", "OLD")
    assert "BTCC, OLD" in caplog.text


def test_all_mode_selects_every_active_symbol() -> None:
    """`all` mode ignores the allowlist and never reports unknown symbols."""
    selection = select_symbols(
        meta_response=META, symbols_mode="all", symbols_allowlist=("FOO",)
    )

    assert selection.selected == ("BTC", "ETH")
    assert selection.unknown == ()
