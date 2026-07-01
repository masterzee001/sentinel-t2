"""Manual smoke test for the Project Sentinel Trade Planner."""

from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.trade_planner.trade_planner import TradePlanner, TradePlannerError


def main() -> int:
    connector = MT5Connector()

    try:
        connector.connect()
        planner = TradePlanner(connector=connector)

        for symbol in ("XAUUSD", "US30"):
            print(f"\nTrade plan for {symbol}")
            print("-" * 34)
            pprint(planner.analyze(symbol))

        return 0
    except (MT5ConnectorError, TradePlannerError, ValueError) as exc:
        print(f"Trade planner test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
