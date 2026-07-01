from __future__ import annotations

import pandas as pd

from backend.backtesting.historical_decision_brain import HistoricalBacktestDecisionBrain
from backend.shared.cost_engine import CostInput, estimate_execution_cost
from backend.shared.shared_candidate_scanner import SharedCandidateScanner
from backend.shared.shared_decision_adapter import DecisionRequest, SharedDecisionAdapter
from backend.shared.trade_lifecycle import validate_transition
from backend.shared.unified_state_registry import state_from_confidence_band, state_from_observer_label
from scripts.run_unified_logic_audit import (
    build_dead_logic_registry,
    build_legacy_report_purge,
    build_unified_logic_audit,
)


class FakeConfidenceAnalyzer:
    def analyze(self, symbol: str, context: dict | None = None) -> dict:
        return {
            "symbol": symbol,
            "decision": "APPROVED",
            "context_seen": context or {},
        }


def test_shared_decision_adapter_calls_same_confidence_brain_for_all_modes():
    adapter = SharedDecisionAdapter(confidence_analyzer=FakeConfidenceAnalyzer())

    for mode in ("LIVE", "DEMO", "REPLAY", "BACKTEST", "PAPER"):
        result = adapter.evaluate(DecisionRequest(mode=mode, symbol="xauusd", context={"source": mode}))

        assert result["status"] == "PASS"
        assert result["decision_source"] == "ConfidenceAnalyzer"
        assert result["parity_compliant"] is True
        assert result["decision"]["symbol"] == "XAUUSD"
        assert result["decision"]["context_seen"]["source"] == mode


def test_historical_replay_delegates_to_live_confidence_payload_logic():
    adapter = SharedDecisionAdapter(confidence_analyzer=HistoricalBacktestDecisionBrain())
    context = {
        "mode": "BACKTEST",
        "minimum_rr": 3.0,
        "risk_reward": 3.2,
        "plan": {"candidate_detected": True, "execution_allowed": True, "direction": "bullish", "narrative_phase": "expansion"},
        "killzone": {"is_valid": True, "active_killzone": "new_york_open", "quality_score": 10},
        "historical_analysis": {
            "trend": {"daily_bias": "bullish", "h4_bias": "bullish", "overall_bias": "bullish"},
            "liquidity": {"latest_sweep": True, "nearest_buy_side_target": 120.0},
            "ict": {
                "mss": {"detected": True, "direction": "bullish"},
                "fvg": {"detected": True, "direction": "bullish", "grade": "A"},
                "order_block": {"detected": True, "direction": "bullish"},
                "premium_discount": {"current_zone": "discount"},
            },
        },
    }

    approved = adapter.evaluate(DecisionRequest(mode="BACKTEST", symbol="XAUUSD", context=context))["decision"]
    context["historical_analysis"]["ict"]["mss"]["detected"] = False
    missing_mss = adapter.evaluate(DecisionRequest(mode="BACKTEST", symbol="XAUUSD", context=context))["decision"]

    assert approved["decision_source"] == "ConfidenceAnalyzer"
    assert approved["historical_adapter_source"] == "HistoricalBacktestDecisionBrain"
    assert approved["live_equivalent"] is True
    assert approved["decision"] == "APPROVED"
    assert missing_mss["decision"] == "REJECTED"
    assert "MSS not confirmed" in missing_mss["rejection_reasons"]
    assert missing_mss["total_confidence"] < approved["total_confidence"]


def test_shared_candidate_scanner_builds_live_and_replay_envelopes():
    scanner = SharedCandidateScanner()
    live = scanner.scan_live_candidate("xauusd", context={"risk_reward": 3.0})
    frame = pd.DataFrame(
        {
            "time": pd.date_range("2026-06-01", periods=25, freq="15min"),
            "open": [95.0] * 24 + [100.0],
            "high": [100.0] * 24 + [115.0],
            "low": [90.0] * 24 + [95.0],
            "close": [95.0] * 24 + [112.0],
        }
    )
    replay = scanner.scan_replay_candidate(
        "xauusd",
        frame,
        killzone={"active_killzone": "new_york_open", "quality_score": 10},
        minimum_rr=3.0,
    )

    assert live["mode"] == "LIVE"
    assert live["candidate_detected"] is True
    assert live["candidate_source"] == "continuous_live_symbol_scan"
    assert replay["mode"] == "BACKTEST"
    assert replay["candidate_detected"] is True
    assert replay["candidate_source"] == "closed_candle_shared_scanner"


def test_unified_state_registry_separates_observer_from_execution_candidate():
    execution_ready = state_from_confidence_band("EXECUTION_READY")
    observer_hot = state_from_observer_label("HOT")

    assert execution_ready.unified_state == "TRADE_CANDIDATE"
    assert execution_ready.execution_allowed is True
    assert observer_hot.input_label == "OBSERVER_HOT"
    assert observer_hot.unified_state == "SETUP_VALID"
    assert observer_hot.execution_allowed is False


def test_cost_engine_covers_spread_slippage_latency_fill_and_delay():
    estimate = estimate_execution_cost(
        CostInput(
            spread_points=20,
            slippage_points=4,
            latency_ms=500,
            fill_probability=0.9,
            approval_delay_seconds=30,
            mode="stressed",
        )
    )

    assert estimate["mode"] == "stressed"
    assert estimate["spread_points"] == 20
    assert estimate["slippage_points"] == 4
    assert estimate["latency_ms"] == 500
    assert estimate["approval_delay_seconds"] == 30
    assert estimate["fill_probability"] == 0.9
    assert estimate["total_cost_points"] > 20


def test_trade_lifecycle_registry_validates_required_transitions():
    assert validate_transition("CANDIDATE", "APPROVED")["status"] == "PASS"
    assert validate_transition("ACTIVE", "PARTIAL_EXIT")["status"] == "PASS"
    assert validate_transition("CLOSED", "ACTIVE")["status"] == "FAIL"


def test_unified_logic_audit_reports_unizim_achieved():
    audit = build_unified_logic_audit()

    assert audit["shared_candidate_scanner"]["status"] == "PASS"
    assert audit["shared_candidate_scanner"]["scanner_enforced_by_live"] is True
    assert audit["shared_candidate_scanner"]["scanner_enforced_by_backtest"] is True
    assert audit["shared_decision_adapter"]["adapter_available"] is True
    assert audit["shared_decision_adapter"]["status"] == "PASS"
    assert audit["shared_decision_adapter"]["adapter_enforced_by_backtest"] is True
    assert audit["shared_decision_adapter"]["adapter_enforced_by_replay"] is True
    assert audit["unified_state_registry"]["status"] == "PASS"
    assert audit["single_cost_engine"]["status"] == "PASS"
    assert audit["trade_lifecycle_parity"]["status"] == "PASS"
    assert audit["remaining_truth_gaps"]["count"] == 0
    assert audit["unizim_achieved"] is True
    assert audit["decision"] == "PASS"


def test_dead_logic_and_legacy_purge_classifications():
    dead_logic = build_dead_logic_registry()
    legacy = build_legacy_report_purge()

    assert dead_logic["engines"]["ProductionPromotionEngine"]["classification"] == "ACTIVE_DECISION_ENGINE"
    assert dead_logic["engines"]["SharedCandidateScanner"]["classification"] == "ACTIVE_DECISION_ENGINE"
    assert dead_logic["engines"]["MarketWatch"]["classification"] == "ADVISORY_ONLY"
    assert "truth_gap_audit.json" in legacy["bucket_a_authoritative"]
    assert "backtest_365d_summary.json" in legacy["bucket_c_invalidated"]
