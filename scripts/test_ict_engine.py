"""Manual smoke test for the Project Sentinel ICT execution engine."""

from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.ict_engine.ict_analyzer import ICTAnalyzer, ICTAnalyzerError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


def main() -> int:
    connector = MT5Connector()

    try:
        connector.connect()
        analyzer = ICTAnalyzer(connector=connector)

        for symbol in ("XAUUSD", "US30"):
            print(f"\nICT execution analysis for {symbol}")
            print("-" * 38)
            pprint(analyzer.analyze(symbol))

        return 0
    except (MT5ConnectorError, ICTAnalyzerError, ValueError) as exc:
        print(f"ICT engine test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
