"""Discover index symbols exposed by the connected MT5 broker."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.symbols.symbol_discovery import SymbolDiscovery
from backend.symbols.symbol_registry import SymbolRegistry


def main() -> int:
    """Connect to MT5 and print NAS100/index symbol candidates."""
    configure_terminal_logging()
    registry = SymbolRegistry()
    connector = MT5Connector(supported_symbols=set(registry.symbols() + registry.aliases_for("NAS100")))
    patterns = ["NAS100", "USTEC", "NASUSD", "US100", "NDX"]
    try:
        connector.connect()
        matches = filter_index_matches(SymbolDiscovery.discover(connector.mt5, patterns), registry.aliases_for("NAS100"))
        preferred = SymbolDiscovery.choose_preferred(matches, [*registry.aliases_for("NAS100"), "USTECH100M", "US100"])
        print("Detected index symbols:")
        print("")
        if matches:
            for item in matches:
                print(f"* {item['symbol']}")
        else:
            print("* none")
        print("")
        print(f"Preferred NAS100 symbol: {preferred or 'NOT FOUND'}")
        return 0 if preferred else 1
    except (MT5ConnectorError, RuntimeError, ValueError) as exc:
        print("Detected index symbols:")
        print("")
        print("* unavailable")
        print("")
        print(f"Discovery failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def configure_terminal_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level="ERROR")


def filter_index_matches(matches: list[dict], aliases: list[str]) -> list[dict]:
    """Keep likely NAS100 CFD aliases and remove individual Nasdaq equities."""
    alias_set = {alias.upper() for alias in aliases}
    allowed_prefixes = ("NAS100", "USTEC", "USTECH", "NASUSD", "US100", "NDX")
    filtered = []
    for item in matches:
        name = str(item.get("symbol", "")).upper()
        path = str(item.get("path", "")).upper()
        description = str(item.get("description", "")).upper()
        if name in alias_set or name.startswith(allowed_prefixes) or ("INDEX" in path and "NASDAQ" in description):
            filtered.append(item)
    return sorted(filtered, key=lambda value: value["symbol"])


if __name__ == "__main__":
    raise SystemExit(main())
