"""Manual smoke test for the Project Sentinel MT5 connector."""

from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


def main() -> int:
    connector = MT5Connector()

    try:
        connector.connect()

        print("Account info:")
        pprint(connector.get_account_info())

        for symbol in ("XAUUSD", "US30"):
            print(f"\nLatest tick for {symbol}:")
            pprint(connector.get_latest_tick(symbol))

        print("\nLatest 100 XAUUSD M15 candles:")
        candles = connector.get_historical_candles("XAUUSD", "M15", count=100)
        print(candles.tail())
        return 0
    except MT5ConnectorError as exc:
        print(f"MT5 connection test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
