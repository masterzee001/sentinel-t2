"""Exit-policy variant experiments, judged out-of-sample (Phase 2, Law 5).

Each variant rescans the production symbols with its own exit simulation and
forward window, then runs the expectancy-sizing walk-forward on the resulting
book. Compared against the promoted configuration (naked SL/TP3, 24-bar
window: +12.59R baseline / rwPF 1.11; sized +14.50R / rwPF 1.28).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backtesting.backtest_engine import BacktestEngine, BacktestEngineError
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from scripts.run_sizing_walkforward import collect_production_trades, walkforward_evaluation

REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "exit_experiments_report.json"

# breakeven_at_r=99 disables the BE move; partial 0 disables partials.
VARIANTS: dict[str, dict[str, Any]] = {
    "runner_3r_f96": {
        "forward_candles": 96,
        "exit": {"enabled": True, "breakeven_at_r": 99.0, "partial_at_r": 99.0, "partial_close_percent": 0, "final_target_r": 3.0},
        "hypothesis": "The 6h window was cutting runners short; give 3R targets 24h.",
    },
    "be2r_3r_f24": {
        "forward_candles": 24,
        "exit": {"enabled": True, "breakeven_at_r": 2.0, "partial_at_r": 99.0, "partial_close_percent": 0, "final_target_r": 3.0},
        "hypothesis": "A late (2R) breakeven protects without dying on noise.",
    },
    "runner_4r_f96": {
        "forward_candles": 96,
        "exit": {"enabled": True, "breakeven_at_r": 99.0, "partial_at_r": 99.0, "partial_close_percent": 0, "final_target_r": 4.0},
        "hypothesis": "Runner dominance suggests wider targets pay.",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--variants", nargs="*", default=list(VARIANTS))
    args = parser.parse_args()

    logger.remove()
    connector = MT5Connector()
    results: dict[str, Any] = {}
    try:
        connector.connect()
        for name in args.variants:
            spec = VARIANTS[name]
            engine = BacktestEngine(connector=connector)
            engine.config["scan"]["forward_candles"] = int(spec["forward_candles"])
            engine.simulator = type(engine.simulator)(
                {**engine.config.get("simulation", {}), "exit_management": spec["exit"]}
            )
            trades = collect_production_trades(engine, days=args.days)
            evaluation = walkforward_evaluation(trades)
            results[name] = {
                "hypothesis": spec["hypothesis"],
                "forward_candles": spec["forward_candles"],
                "exit": spec["exit"],
                "trades_total": len(trades),
                "baseline": evaluation["baseline"],
                "sized": evaluation["sized"],
                "verdict": evaluation["verdict"],
                "per_quarter": evaluation["per_quarter"],
            }
            print(
                f"{name}: baseline {evaluation['baseline']['net_rr']}R (rwPF {evaluation['baseline']['risk_weighted_pf']})"
                f" | sized {evaluation['sized']['net_rr']}R (rwPF {evaluation['sized']['risk_weighted_pf']})"
            )
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "days": args.days,
            "promoted_reference": {"baseline_net_rr": 12.59, "baseline_rwpf": 1.11, "sized_net_rr": 14.5, "sized_rwpf": 1.28},
            "variants": results,
        }
        REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(f"Report: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
        return 0
    except (MT5ConnectorError, BacktestEngineError, ValueError, KeyError) as exc:
        print(f"Exit experiments failed: {exc}")
        return 1
    finally:
        connector.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
