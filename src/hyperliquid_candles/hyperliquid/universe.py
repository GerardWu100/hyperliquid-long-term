"""Symbol discovery from Hyperliquid `meta` responses."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


LOGGER = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class SymbolSelection:
    """Symbols chosen for one cycle.

    Attributes
    ----------
    selected:
        Active perpetual symbols to ingest, in allowlist order (or sorted in
        ``"all"`` mode).
    unknown:
        Allowlisted symbols that are not active perps (delisted or misspelled),
        sorted. Always empty in ``"all"`` mode.
    """

    selected: tuple[str, ...]
    unknown: tuple[str, ...] = ()


def select_symbols(
    meta_response: dict[str, Any],
    symbols_mode: str,
    symbols_allowlist: tuple[str, ...],
) -> SymbolSelection:
    """Select symbols according to config after validating against `meta`.

    In allowlist mode an unknown symbol is skipped with a warning instead of
    raising, so one delisted or misspelled entry cannot stop collection of the
    other allowlisted symbols.
    """
    active_symbols = active_perp_symbols(meta_response)
    if symbols_mode == "all":
        return SymbolSelection(selected=active_symbols)
    if symbols_mode != "allowlist":
        raise ValueError("symbols_mode must be either 'all' or 'allowlist'")

    active_set = set(active_symbols)
    selected = tuple(
        dict.fromkeys(symbol for symbol in symbols_allowlist if symbol in active_set)
    )
    unknown = tuple(sorted(set(symbols_allowlist) - active_set))
    if unknown:
        LOGGER.warning(
            "Allowlisted symbols are not active Hyperliquid perps; skipping: %s",
            ", ".join(unknown),
        )
    return SymbolSelection(selected=selected, unknown=unknown)
