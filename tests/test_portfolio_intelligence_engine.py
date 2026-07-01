from __future__ import annotations

from backend.portfolio_intelligence.portfolio_intelligence_engine import (
    PortfolioIntelligenceEngine,
    TimeframeConfluenceEngine,
    sprint_18b_success,
)
from scripts.run_portfolio_intelligence import build_portfolio_intelligence_report


def base_candidate(**overrides):
    candidate = {
        "symbol": "XAUUSD",
        "grade": "A+",
        "confidence": 94,
        "mss_confirmed": True,
        "killzone_valid": True,
        "risk_allowed": True,
        "risk_percent": 0.25,
        "direction": "buy",
        "regime": "trend",
        "session": "NY",
        "timeframes": {"D1": "bullish", "H4": "bullish", "M15": "bullish", "M5": "bullish", "M1": "bullish"},
    }
    candidate.update(overrides)
    return candidate


def test_timeframe_confluence_full_stack_and_m1_diagnostic_only():
    result = TimeframeConfluenceEngine().classify(
        {"D1": "bullish", "H4": "bullish", "M15": "bullish", "M5": "bullish", "M1": "bullish"}
    )

    assert result["tier"] == "FULL_STACK_CONFLUENCE"
    assert result["score"] == 100
    assert result["m1_can_create_trade"] is False
    assert result["m5_can_bypass_core"] is False


def test_timeframe_confluence_structural_tactical_and_conflict():
    engine = TimeframeConfluenceEngine()
    structural = engine.classify({"D1": "bullish", "H4": "bullish", "M15": "bullish", "M5": "mixed"})
    tactical = engine.classify({"D1": "mixed", "H4": "mixed", "M15": "bearish", "M5": "bearish"})
    conflict = engine.classify({"D1": "bullish", "H4": "bullish", "M15": "bearish", "M5": "bearish"})

    assert structural["tier"] == "STRUCTURAL_CONFLUENCE"
    assert tactical["tier"] == "TACTICAL_CONFLUENCE"
    assert conflict["tier"] == "CONFLICT"


def test_pas_allows_strong_production_candidate_without_changing_gate():
    result = PortfolioIntelligenceEngine().evaluate_candidate(base_candidate())

    assert result["status"] == "PASS"
    assert result["decision"] == "ALLOW"
    assert result["pas"] >= 75
    assert result["advisory_only"] is True
    assert result["production_gate_changed"] is False


def test_pas_blocks_core_quality_failure_even_when_confluence_is_strong():
    result = PortfolioIntelligenceEngine().evaluate_candidate(base_candidate(confidence=88, mss_confirmed=True))

    assert result["status"] == "CORE_BLOCKED"
    assert result["decision"] == "CORE_BLOCKED"
    assert "BELOW_MIN_CONFIDENCE" in result["reasons"]
    assert result["production_gate_changed"] is False


def test_m5_m1_cannot_bypass_mss_grade_or_killzone():
    result = PortfolioIntelligenceEngine().evaluate_candidate(
        base_candidate(mss_confirmed=False, grade="B", killzone_valid=False)
    )

    assert result["status"] == "CORE_BLOCKED"
    assert result["decision"] == "CORE_BLOCKED"
    assert {"MSS_NOT_CONFIRMED", "GRADE_LOCK", "INVALID_KILLZONE"}.issubset(set(result["reasons"]))


def test_sandbox_and_observer_symbols_excluded_from_pas_statistics():
    engine = PortfolioIntelligenceEngine()
    sandbox = engine.evaluate_candidate(base_candidate(symbol="NAS100"))
    observer = engine.evaluate_candidate(base_candidate(symbol="EURUSD"))

    assert sandbox["status"] == "EXCLUDED"
    assert observer["status"] == "EXCLUDED"
    assert sandbox["included_in_pas_statistics"] is False
    assert observer["included_in_pas_statistics"] is False


def test_correlation_intelligence_restricts_same_cluster_overlap():
    result = PortfolioIntelligenceEngine().evaluate_candidate(
        base_candidate(symbol="US30", risk_percent=0.25),
        portfolio_state={"open_trades": [{"symbol": "US30", "risk_percent": 0.4, "direction": "buy"}]},
    )

    assert result["components"]["correlation"]["correlation_block"] is True
    assert result["components"]["correlation"]["same_cluster_trades"] == 1


def test_conflict_confluence_suppresses_unless_aplus_override_active():
    candidate = base_candidate(timeframes={"D1": "bullish", "H4": "bullish", "M15": "bearish", "M5": "bearish"})
    suppressed = PortfolioIntelligenceEngine().evaluate_candidate(candidate)
    allowed = PortfolioIntelligenceEngine().evaluate_candidate({**candidate, "a_plus_override_active": True})

    assert suppressed["decision"] == "SUPPRESS"
    assert allowed["decision"] in {"ALLOW", "REDUCED_RISK_ALLOW"}


def test_sprint_18b_report_meets_targets_and_stays_diagnostic():
    report = build_portfolio_intelligence_report()
    after = report["windows"]["365D"]["after"]

    assert sprint_18b_success(after) is True
    assert report["mode"] == "DIAGNOSTIC_ADVISORY_ONLY"
    assert report["affect_production"] is False
    assert report["decision"] == "PASS"
    assert report["production_baseline"] == "Updated Safely"


def test_sprint_18b_forensic_audit_safety_and_metric_isolation():
    report = build_portfolio_intelligence_report()
    audit = report["audit"]
    safety = report["safety"]

    assert audit["level"] == "FORENSIC"
    assert audit["conflicts_found"] is False
    assert audit["checks"]["metric_contamination"]["status"] == "PASS"
    assert audit["checks"]["symbol_tier_integrity"]["status"] == "PASS"
    assert safety["broker_submission_disabled"] is True
    assert safety["autonomous_execution"] is False
    assert safety["assisted_submit_orders"] is False
