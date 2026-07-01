from __future__ import annotations

import json
from pathlib import Path

from backend.analytics.monte_carlo_engine import MonteCarloEngine


def make_engine(**config) -> MonteCarloEngine:
    return MonteCarloEngine(config={"simulations": 500, "random_seed": 7, **config})


def test_simulation_runs():
    engine = make_engine(risk_models=[0.5])
    trades = [{"rr": value} for value in [3, -1, 0, 2, -1, 1, -1, 4]]

    report = engine.run(trades=trades, source_path="synthetic")

    assert report["available"] is True
    assert report["total_simulations"] == 500
    assert report["trades_used"] == 8
    assert "0.5%" in report["risk_models"]
    assert report["risk_models"]["0.5%"]["drawdown"]["p95_dd"] >= 0.0


def test_dd_and_streak_calculation():
    result = MonteCarloEngine.simulate_sequence([2, -1, -1, 3, 0, -1], risk_percent=1.0, starting_balance=2000)

    assert result["final_balance"] > 2000
    assert result["max_drawdown"] > 0
    assert result["max_losing_streak"] == 2
    assert result["max_win_streak"] == 1
    assert result["collapsed"] is False


def test_ruin_probability_tracks_breaches():
    engine = make_engine(risk_models=[1.0], drawdown_limits={"internal_limit": 1.0, "firm_limit": 2.0})
    trades = [{"rr": value} for value in [-1, -1, -1, 2, 2, -1]]

    report = engine.run(trades=trades, source_path="synthetic")
    ruin = report["risk_models"]["1%"]["risk_of_ruin"]

    assert ruin["breach_4_percent"] > 0
    assert ruin["breach_6_percent"] > 0
    assert ruin["account_collapse"] == 0.0


def test_recommendation_logic_caps_safe_risk_at_half_percent():
    engine = make_engine(risk_models=[0.25, 0.5, 0.75, 1.0])
    trades = [{"rr": value} for value in [3, -1, 2, -1, 0, 1, -1, 4, 0, -1]]

    report = engine.run(trades=trades, source_path="synthetic")

    assert report["safe_risk_percent"] <= 0.5
    assert report["autonomous_mode_recommended"] is False
    assert any("Autonomous execution remains disabled" in item for item in report["recommendations"])


def test_extracts_365d_summary_when_trade_log_missing(tmp_path: Path):
    report_path = tmp_path / "data" / "reports" / "backtest_365d_summary.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "days_365": {
                    "wins": 2,
                    "losses": 1,
                    "breakevens": 1,
                    "gross_profit": 100.0,
                    "gross_loss": 25.0,
                    "net_rr": 3.0,
                }
            }
        ),
        encoding="utf-8",
    )
    engine = MonteCarloEngine(project_root=tmp_path, config={"simulations": 50, "risk_models": [0.5]})

    report = engine.run_from_report("data/reports/backtest_365d_summary.json")

    assert report["available"] is True
    assert report["trades_used"] == 4
