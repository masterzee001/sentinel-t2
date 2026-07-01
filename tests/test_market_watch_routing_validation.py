from __future__ import annotations

from backend.market_watch_engine.routing_validation import (
    annotate_routing,
    classify_performance_ladder,
    classify_routing,
    counterfactual_reroutes,
    market_watch_iq,
    repeated_bad_routing,
    srms,
    strategy_checklists,
)
from scripts.run_backtest_365d import approved_robustness_metrics, metrics_within_tolerance, normalize_metrics
from scripts.run_market_watch_backtest import build_market_watch_report


def complete_trend_record(**overrides):
    record = {
        "trade_id": "test-1",
        "selected_strategy": "trend_following",
        "outcome": "WIN",
        "rr": 1.5,
        "dominant_pattern": "trend_continuation",
        "trend_strength": 84,
        "volatility_expansion": 62,
        "pullback_quality": 70,
        "continuation_structure": 75,
        "htf_bias_aligned": True,
        "exhaustion_score": 45,
        "session_quality": 84,
        "continuation_target_clear": True,
        "confidence": 94,
        "noise_score": 20,
        "strategy_scores": {"ict_liquidity": 50, "trend_following": 88, "mean_reversion": 54},
    }
    record.update(overrides)
    return record


def test_strategy_checklist_enforcement():
    record = complete_trend_record()

    checklists = strategy_checklists(record)

    assert checklists["trend_following"]["complete"] is True
    assert checklists["ict_liquidity"]["complete"] is False


def test_routing_classification_flags_wrong_strategy():
    record = complete_trend_record(
        selected_strategy="ict_liquidity",
        sweep_detected=True,
        mss_confirmed=False,
        displacement_score=45,
        fvg_detected=False,
        ob_detected=False,
        premium_discount_alignment=40,
        likely_draw_on_liquidity="",
        strategy_scores={"ict_liquidity": 82, "trend_following": 88, "mean_reversion": 55},
    )

    result = classify_routing(record)

    assert result["routing_class"] == "MISROUTED_TO_ICT"
    assert result["recommended_strategy"] == "trend_following"


def test_repeated_bad_routing_detection():
    records = annotate_routing(
        [
            complete_trend_record(trade_id="a", selected_strategy="ict_liquidity", outcome="LOSS", mss_confirmed=False, noise_score=64),
            complete_trend_record(trade_id="b", selected_strategy="ict_liquidity", outcome="LOSS", mss_confirmed=False, noise_score=64),
        ]
    )

    repeated = repeated_bad_routing(records)

    assert repeated["counts"]["ict_liquidity"] == 2


def test_counterfactual_rerouting_logic():
    records = annotate_routing(
        [
            complete_trend_record(
                trade_id="loss-1",
                selected_strategy="trend_following",
                dominant_pattern="noisy_chop",
                outcome="LOSS",
                rr=-1.0,
                noise_score=78,
            )
        ]
    )

    reroutes = counterfactual_reroutes(records)

    assert reroutes[0]["recommended_strategy"] == "no_trade"
    assert reroutes[0]["rerouted_outcome"] == "AVOIDED_LOSS"


def test_market_watch_iq_and_srms_scoring():
    records = annotate_routing(
        [
            complete_trend_record(trade_id="good"),
            complete_trend_record(trade_id="bad-a", selected_strategy="ict_liquidity", outcome="LOSS", mss_confirmed=False, noise_score=64),
            complete_trend_record(trade_id="bad-b", selected_strategy="ict_liquidity", outcome="LOSS", mss_confirmed=False, noise_score=64),
        ]
    )
    repeated = repeated_bad_routing(records)

    iq = market_watch_iq(records, repeated)
    memory = srms(repeated["total"], 2)

    assert iq["routing_accuracy"] < 100
    assert memory["value"] == 0.0
    assert memory["classification"] == "POOR"


def test_performance_ladder_classification():
    result = classify_performance_ladder({"pf": 1.92, "win_rate": 62.4, "trades": 91, "max_drawdown": 2.95}, baseline_preserved=True)

    assert result == "STAGE 1 QUALIFIED"


def test_routing_report_preserves_advisory_baseline():
    report = build_market_watch_report(
        {
            "production_recalculation_diagnostics": {
                "metrics": {"profit_factor": 0.83, "win_rate": 45.45, "trades_approved": 99, "max_drawdown": 5.0}
            }
        }
    )

    assert report["matches_approved_baseline"] is True
    assert metrics_within_tolerance(normalize_metrics(approved_robustness_metrics()), report["market_watch_advisory_mode"])
    assert report["performance_ladder_classification"] == "ELITE QUALIFIED"
    assert report["routing_forensics"]["routing_summary"]["CORRECTLY_ROUTED"] > 0
