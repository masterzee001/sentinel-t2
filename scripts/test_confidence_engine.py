"""Manual smoke test for the Project Sentinel confidence engine."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from pprint import pprint
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer, ConfidenceAnalyzerError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


def main() -> int:
    connector = MT5Connector()

    try:
        connector.connect()
        analyzer = ConfidenceAnalyzer(connector=connector)

        for symbol in ("XAUUSD", "US30"):
            print(f"\nConfidence analysis for {symbol}")
            print("-" * 34)
            pprint(
                analyzer.analyze(
                    symbol,
                    context={
                        "symbol": symbol,
                        "analysis_time": datetime.now(ZoneInfo("Africa/Lagos")),
                        "risk_reward": 3.0,
                    },
                )
            )

        return 0
    except (MT5ConnectorError, ConfidenceAnalyzerError, ValueError) as exc:
        print(f"Confidence engine test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
