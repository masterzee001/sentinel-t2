"""Manual smoke test for the Project Sentinel liquidity engine."""

from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer, LiquidityAnalyzerError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


def main() -> int:
    connector = MT5Connector()

    try:
        connector.connect()
        analyzer = LiquidityAnalyzer(connector=connector)

        for symbol in ("XAUUSD", "US30"):
            print(f"\nLiquidity analysis for {symbol}")
            print("-" * 34)
            pprint(analyzer.analyze(symbol))

        return 0
    except (MT5ConnectorError, LiquidityAnalyzerError, ValueError) as exc:
        print(f"Liquidity engine test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
