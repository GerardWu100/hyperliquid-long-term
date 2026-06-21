"""Symbol discovery from Hyperliquid `meta` responses."""

from __future__ import annotations

from typing import Any


def active_perp_symbols(meta_response: dict[str, Any]) -> tuple[str, ...]:
    """Return active perpetual symbols from a Hyperliquid `meta` response.

    Hyperliquid names perpetual markets in `universe`. Delisting flags are not
    always present, so the parser treats missing flags as active.
    """
    raw_universe = meta_response.get("universe", [])
    if not isinstance(raw_universe, list):
        raise ValueError("Hyperliquid meta response has no universe list")

    symbols: list[str] = []
    for item in raw_universe:
        if not isinstance(item, dict):
            continue
        symbol = item.get("name")
        is_delisted = bool(item.get("isDelisted", False))
        if isinstance(symbol, str) and symbol and not is_delisted:
            symbols.append(symbol)

    return tuple(sorted(set(symbols)))


def select_symbols(
    meta_response: dict[str, Any],
    symbols_mode: str,
    symbols_allowlist: tuple[str, ...],
) -> tuple[str, ...]:
    """Select symbols according to config after validating against `meta`."""
    active_symbols = active_perp_symbols(meta_response)
    if symbols_mode == "all":
        return active_symbols
    if symbols_mode != "allowlist":
        raise ValueError("symbols_mode must be either 'all' or 'allowlist'")

    active_set = set(active_symbols)
    selected = tuple(symbol for symbol in symbols_allowlist if symbol in active_set)
    missing = sorted(set(symbols_allowlist) - active_set)
    if missing:
        raise ValueError(f"Allowlisted symbols are not active Hyperliquid perps: {missing}")
    return selected
