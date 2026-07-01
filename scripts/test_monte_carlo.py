"""Offline smoke test for the Monte Carlo stress test engine."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.analytics.monte_carlo_engine import MonteCarloEngine


def main() -> int:
    """Run synthetic Monte Carlo stress test without live execution."""
    trades = [{"rr": rr, "symbol": "US30", "killzone": "new_york_open"} for rr in [3, -1, 0, 2, -1, 4, -1, 1, 0, -1]]
    engine = MonteCarloEngine(config={"simulations": 500, "risk_models": [0.25, 0.5, 1.0], "random_seed": 42})
    report = engine.run(trades=trades, source_path="synthetic")

    assert report["available"] is True
    assert report["safe_risk_percent"] in {0.25, 0.5}
    assert report["autonomous_mode_recommended"] is False
    assert "0.5%" in report["risk_models"]

    print("MONTE CARLO TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
