"""Manual smoke test for the Project Sentinel trend engine."""

from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.trend_engine.trend_analyzer import TrendAnalyzer, TrendAnalyzerError


def main() -> int:
    connector = MT5Connector()

    try:
        connector.connect()
        analyzer = TrendAnalyzer(connector=connector)

        for symbol in ("XAUUSD", "US30"):
            print(f"\nTrend analysis for {symbol}")
            print("-" * 30)
            pprint(analyzer.get_overall_bias(symbol))

        return 0
    except (MT5ConnectorError, TrendAnalyzerError, ValueError) as exc:
        print(f"Trend engine test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
