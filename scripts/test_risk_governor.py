"""Manual smoke test for the Project Sentinel Risk Governor."""

from __future__ import annotations

import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.risk_manager.risk_governor import RiskGovernor, RiskGovernorError


def main() -> int:
    connector = MT5Connector()

    try:
        connector.connect()
        governor = RiskGovernor(connector=connector)
        result = governor.evaluate()

        print("\nRisk Governor")
        print("-" * 34)
        pprint(result)
        return 0
    except (MT5ConnectorError, RiskGovernorError, ValueError) as exc:
        print(f"Risk governor test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
