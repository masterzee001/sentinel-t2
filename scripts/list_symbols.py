"""Discover broker-specific MT5 symbols for Gold and US30."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


KEYWORDS = ("XAU", "GOLD", "US30", "DJ30", "DJI", "DOW", "WALL")
OUTPUT_PATH = PROJECT_ROOT / "data" / "symbol_discovery.csv"
DISPLAY_FIELDS = (
    "name",
    "description",
    "currency_base",
    "currency_profit",
    "trade_mode",
    "visible",
)


def symbol_to_dict(symbol: Any) -> dict[str, Any]:
    """Convert an MT5 symbol object to a plain dictionary."""
    if hasattr(symbol, "_asdict"):
        return dict(symbol._asdict())
    if hasattr(symbol, "__dict__"):
        return dict(symbol.__dict__)
    raise MT5ConnectorError(f"Cannot convert symbol response of type {type(symbol)!r} to dict.")


def matches_keywords(symbol: dict[str, Any]) -> bool:
    """Return whether a symbol name or description matches discovery keywords."""
    name = str(symbol.get("name", "")).upper()
    description = str(symbol.get("description", "")).upper()
    search_text = f"{name} {description}"
    return any(keyword in search_text for keyword in KEYWORDS)


def discover_symbols(connector: MT5Connector) -> list[dict[str, Any]]:
    """Retrieve and filter all MT5 symbols for Gold and US30 candidates."""
    logger.info("Retrieving all available MT5 symbols.")
    symbols = connector.mt5.symbols_get()
    if symbols is None:
        last_error = connector.mt5.last_error() if hasattr(connector.mt5, "last_error") else "unknown error"
        raise MT5ConnectorError(f"Failed to retrieve MT5 symbols: {last_error}")

    matches = [symbol_to_dict(symbol) for symbol in symbols]
    matches = [symbol for symbol in matches if matches_keywords(symbol)]
    matches.sort(key=lambda symbol: str(symbol.get("name", "")))

    logger.info("Found {} matching symbols from {} available symbols.", len(matches), len(symbols))
    return matches


def save_symbols(symbols: list[dict[str, Any]], output_path: Path = OUTPUT_PATH) -> None:
    """Save discovery results to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=DISPLAY_FIELDS)
        writer.writeheader()
        for symbol in symbols:
            writer.writerow({field: symbol.get(field, "") for field in DISPLAY_FIELDS})

    logger.info("Saved symbol discovery results to {}.", output_path)


def print_symbols(symbols: list[dict[str, Any]]) -> None:
    """Print discovery results in a clean table."""
    if not symbols:
        print("No matching symbols found.")
        return

    rows = [
        {field: str(symbol.get(field, "")) for field in DISPLAY_FIELDS}
        for symbol in symbols
    ]
    widths = {
        field: max(len(field), *(len(row[field]) for row in rows))
        for field in DISPLAY_FIELDS
    }

    header = " | ".join(field.ljust(widths[field]) for field in DISPLAY_FIELDS)
    separator = "-+-".join("-" * widths[field] for field in DISPLAY_FIELDS)

    print(header)
    print(separator)
    for row in rows:
        print(" | ".join(row[field].ljust(widths[field]) for field in DISPLAY_FIELDS))


def main() -> int:
    connector = MT5Connector()

    try:
        connector.connect()
        symbols = discover_symbols(connector)
        print_symbols(symbols)
        save_symbols(symbols)
        print(f"\nSaved {len(symbols)} matching symbols to {OUTPUT_PATH}")
        return 0
    except MT5ConnectorError as exc:
        logger.error("Symbol discovery failed: {}", exc)
        print(f"Symbol discovery failed: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover - final guard for manual script use.
        logger.exception("Unexpected symbol discovery error: {}", exc)
        print(f"Unexpected symbol discovery error: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
