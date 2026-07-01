"""Manual smoke test for the Project Sentinel Narrative Engine."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.narrative_engine.narrative_analyzer import NarrativeAnalyzer, NarrativeAnalyzerError


def print_narrative(narrative: dict) -> None:
    """Print the core narrative fields for a desk-style smoke test."""
    print(f"Symbol: {narrative['symbol']}")
    print(f"Bias: {narrative['bias']}")
    print(f"Phase: {narrative['phase']}")
    print(f"Swept Liquidity: {', '.join(narrative['swept_liquidity']) or 'None'}")
    print(f"Unswept Liquidity: {', '.join(narrative['unswept_liquidity']) or 'None'}")
    print(f"Current Zone: {narrative['current_zone']}")
    print(f"Likely Draw: {narrative['likely_draw']}")
    print(f"Summary: {narrative['summary']}")


def main() -> int:
    logger.remove()
    connector = MT5Connector()

    try:
        connector.connect()
        analyzer = NarrativeAnalyzer(connector=connector)

        for symbol in ("XAUUSD", "US30"):
            print(f"\nNarrative for {symbol}")
            print("-" * 34)
            print_narrative(analyzer.analyze(symbol))

        return 0
    except (MT5ConnectorError, NarrativeAnalyzerError, ValueError) as exc:
        print(f"Narrative engine test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
