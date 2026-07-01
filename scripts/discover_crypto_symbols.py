"""Discover crypto symbols exposed by the connected MT5 broker."""

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
    """Connect to MT5 and print crypto symbol candidates."""
    configure_terminal_logging()
    registry = SymbolRegistry()
    connector = MT5Connector(supported_symbols=set(registry.symbols() + registry.aliases_for("BTCUSD")))
    patterns = ["BTC", "XBT", "CRYPTO"]
    try:
        connector.connect()
        matches = filter_crypto_matches(SymbolDiscovery.discover(connector.mt5, patterns), registry.aliases_for("BTCUSD"))
        preferred = SymbolDiscovery.choose_preferred(matches, [*registry.aliases_for("BTCUSD"), "BTC", "XBT"])
        print("Detected crypto symbols:")
        print("")
        if matches:
            for item in matches:
                print(f"* {item['symbol']}")
        else:
            print("* none")
        print("")
        print(f"Preferred BTC symbol: {preferred or 'NOT FOUND'}")
        return 0 if preferred else 1
    except (MT5ConnectorError, RuntimeError, ValueError) as exc:
        print("Detected crypto symbols:")
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


def filter_crypto_matches(matches: list[dict], aliases: list[str]) -> list[dict]:
    """Keep likely broker crypto CFD aliases and remove equity/ETF noise."""
    alias_set = {alias.upper() for alias in aliases}
    allowed_prefixes = ("BTCUSD", "XBTUSD", "ETHUSD")
    filtered = []
    for item in matches:
        name = str(item.get("symbol", "")).upper()
        path = str(item.get("path", "")).upper()
        if name in alias_set or name in {"BTC", "XBT"} or name.startswith(allowed_prefixes) or "CRYPTO" in path:
            filtered.append(item)
    return sorted(filtered, key=lambda value: value["symbol"])


if __name__ == "__main__":
    raise SystemExit(main())
