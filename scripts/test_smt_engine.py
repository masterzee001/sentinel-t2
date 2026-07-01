"""Manual smoke test for the Project Sentinel SMT Engine."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.smt_engine.smt_analyzer import SMTAnalyzer, SMTAnalyzerError


def print_smt(result: dict) -> None:
    """Print one clean SMT result line."""
    status = SMTAnalyzer.format_summary(result)
    print(
        f"{result['pair_name']} | {result['timeframe']} | detected={result['smt_detected']} | "
        f"direction={result['direction'] or 'none'} | confidence={result['confidence']} | {status}"
    )


def main() -> int:
    logger.remove()
    connector = MT5Connector()
    try:
        connector.connect()
        analyzer = SMTAnalyzer(connector=connector)
        print("SMT ENGINE")
        for result in analyzer.analyze_all(timeframe="M15"):
            print_smt(result)
        print("Advisor Mode only: no execution action was taken.")
        return 0
    except (MT5ConnectorError, SMTAnalyzerError, ValueError) as exc:
        print(f"SMT engine test failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
