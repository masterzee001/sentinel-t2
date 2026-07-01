from __future__ import annotations

from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer
from backend.memory_engine.hierarchical_market_memory import HierarchicalMarketMemory
from scripts.run_hierarchical_market_memory import build_hierarchical_memory_report


def test_hierarchical_memory_capture_has_all_layers():
    engine = HierarchicalMarketMemory()

    memory = engine.build_memory(symbol="XAUUSD")

    assert memory["mode"] == "ADVISORY_ONLY"
    assert memory["macro_memory"]["status"] == "PASS"
    assert memory["session_memory"]["status"] == "PASS"
    assert memory["trigger_memory"]["status"] == "PASS"
    assert memory["experience_memory"]["status"] == "PASS"
    assert memory["regime_memory"]["status"] == "PASS"
    assert memory["production_metrics_modified"] is False


def test_macro_memory_tracks_required_timeframes_and_levels():
    macro = HierarchicalMarketMemory().capture_macro_memory(symbol="US30", source={})

    assert macro["timeframes"] == ["W1", "D1", "H4"]
    assert macro["previous_week_high"] > macro["previous_week_low"]
    assert macro["previous_day_high"] > macro["previous_day_low"]
    assert macro["major_swing_highs"]
    assert macro["htf_order_blocks"]
    assert macro["htf_fvgs"]
    assert macro["premium_discount_zones"]["equilibrium"] > 0


def test_session_memory_tracks_intraday_ranges_and_sweeps():
    session = HierarchicalMarketMemory().capture_session_memory(symbol="XAUUSD", source={})

    assert session["timeframes"] == ["H1", "M15"]
    assert session["asian_range"]["high"] > session["asian_range"]["low"]
    assert session["london_session"]["high"] > session["london_session"]["low"]
    assert session["ny_session"]["high"] > session["ny_session"]["low"]
    assert session["session_liquidity_sweeps"][0]["strength"] == "strong"
    assert session["m15_structure_exists"] is True


def test_m5_and_m1_trigger_memory_are_captured():
    trigger = HierarchicalMarketMemory().capture_trigger_memory(symbol="XAUUSD", source={})

    assert trigger["timeframes"] == ["M5", "M1"]
    assert trigger["micro_sweep_events"][0]["timeframe"] == "M5"
    assert trigger["m5_mss"]["detected"] is True
    assert trigger["m1_mss"]["detected"] is True
    assert trigger["trigger_fvg"]["timeframe"] == "M1"
    assert trigger["mitigation_state"]["valid"] is True


def test_m5_m1_confidence_scores_are_advisory_after_m15_structure():
    memory = HierarchicalMarketMemory().build_memory(symbol="XAUUSD")

    advisory = ConfidenceAnalyzer.calculate_memory_advisory_scores(
        {"hierarchical_memory": memory},
        {"mss": {"detected": True}},
    )

    assert advisory["status"] == "ADVISORY_READY"
    assert advisory["m5_trigger_score"] > 0
    assert advisory["m1_precision_score"] > 0
    assert advisory["production_score_impact"] == 0


def test_m1_m5_cannot_create_trade_without_m15_structure():
    memory = HierarchicalMarketMemory().build_memory(
        symbol="XAUUSD",
        source={"m15_structure_exists": False, "session": {"m15_structure_exists": False}},
    )

    advisory = ConfidenceAnalyzer.calculate_memory_advisory_scores(
        {"hierarchical_memory": memory},
        {"mss": {"detected": False}},
    )

    assert advisory == {
        "status": "BLOCKED_NO_M15_STRUCTURE",
        "m5_trigger_score": 0,
        "m1_precision_score": 0,
        "memory_alignment_score": 0,
        "production_score_impact": 0,
    }


def test_score_stickiness_flags_stale_symbol_scores():
    engine = HierarchicalMarketMemory()

    report = engine.score_stickiness(engine.sample_score_records(), unchanged_threshold=3)

    assert report["decision"] == "PASS"
    assert report["symbols"]["XAUUSD"]["stale_symbol_score_warning"] is True
    assert report["symbols"]["XAUUSD"]["score_refresh_frequency"] == 0.0
    assert report["symbols"]["US30"]["stale_symbol_score_warning"] is False
    assert report["symbols"]["US30"]["score_variance"] == 3.0


def test_sprint_15_report_preserves_production_and_dry_run_safety():
    report = build_hierarchical_memory_report()

    assert report["decision"] == "PASS"
    assert report["production_baseline_preserved"] is True
    assert report["confidence_integration"]["production_score_impact"] == 0
    assert report["confidence_integration"]["m1_m5_can_create_trade"] is False
    assert report["assisted_execution"]["enabled"] is True
    assert report["assisted_execution"]["mode"] == "DEMO_ONLY"
    assert report["assisted_execution"]["dry_run_only"] is True
    assert report["assisted_execution"]["actual_order_send_blocked"] is True
