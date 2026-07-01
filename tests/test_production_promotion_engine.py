from __future__ import annotations

from pathlib import Path

from backend.execution_engine.position_manager import PositionManager
from backend.production_promotion.production_promotion_engine import (
    CURRENT_PRODUCTION_BASELINE,
    ProductionPromotionEngine,
    production_promotion_success,
)
from scripts.run_production_promotion import build_production_promotion_report


def engine() -> ProductionPromotionEngine:
    return ProductionPromotionEngine()


def test_efde_recommends_early_exit_only_inside_controlled_gate():
    result = engine().evaluate_efde_position(
        {"symbol": "XAUUSD", "status": "OPEN"},
        context={"adverse_movement_pct": 0.5, "fps": 80, "human_approval_required": True},
    )

    assert result["status"] == "PASS"
    assert result["recommendation"] == "EARLY_EXIT_RECOMMENDED"
    assert result["requires_human_approval"] is True
    assert result["auto_close_enabled"] is False
    assert result["order_send_allowed"] is False


def test_efde_never_overrides_kill_switch_or_symbol_boundary():
    kill_switch = engine().evaluate_efde_position(
        {"symbol": "US30", "status": "OPEN"},
        context={"adverse_movement_pct": 0.5, "fps": 90, "kill_switch_active": True, "human_approval_required": True},
    )
    observer = engine().evaluate_efde_position(
        {"symbol": "NAS100", "status": "OPEN"},
        context={"adverse_movement_pct": 0.5, "fps": 90, "human_approval_required": True},
    )

    assert kill_switch["status"] == "BLOCKED"
    assert "Kill switch active" in kill_switch["reasons"]
    assert observer["status"] == "BLOCKED"
    assert "Symbol not production eligible" in observer["reasons"]


def test_a_plus_override_admits_only_portfolio_suppression():
    candidate = {
        "symbol": "XAUUSD",
        "grade": "A+",
        "confidence": 94,
        "execution_ready": True,
        "override_eligible": True,
        "risk_allowed": True,
        "spread_acceptable": True,
        "news_clear": True,
        "blocked_by": "PRODUCTION_PORTFOLIO_SUPPRESSION",
    }

    result = engine().admit_a_plus_override_candidate(candidate, session_state={"override_trades_used": 1})

    assert result["status"] == "PASS"
    assert result["decision"] == "ADMITTED_TO_PRODUCTION_REVIEW"
    assert result["bypass_scope"] == "PRODUCTION_PORTFOLIO_SUPPRESSION_ONLY"
    assert result["broker_order_submitted"] is False


def test_a_plus_override_blocks_protected_paths_and_session_limit():
    base = {
        "symbol": "XAUUSD",
        "grade": "A+",
        "confidence": 94,
        "execution_ready": True,
        "override_eligible": True,
        "risk_allowed": True,
        "spread_acceptable": True,
        "news_clear": True,
        "blocked_by": "RISK_LOCK",
    }

    protected = engine().admit_a_plus_override_candidate(base)
    limited = engine().admit_a_plus_override_candidate(
        {**base, "blocked_by": "PRODUCTION_PORTFOLIO_SUPPRESSION"},
        session_state={"override_trades_used": 2},
    )

    assert protected["status"] == "BLOCKED"
    assert any("cannot be bypassed" in reason for reason in protected["reasons"])
    assert limited["status"] == "BLOCKED"
    assert "Session override limit reached" in limited["reasons"]


def test_memory_soft_weighting_is_small_and_cannot_force_execution_ready():
    result = engine().memory_soft_weighting(
        {
            "base_confidence": 87,
            "requested_bonus": 5,
            "strong_alignment": True,
            "repeated_historical_confirmation": True,
            "regime_match": True,
            "m15_structure_exists": True,
        }
    )

    assert result["status"] == "PASS"
    assert result["bonus"] == 2
    assert result["effective_confidence"] == 89
    assert result["force_execution_ready"] is False
    assert result["bypasses_guardrails"] is False


def test_memory_soft_weighting_blocks_guardrail_bypass():
    result = engine().memory_soft_weighting(
        {
            "base_confidence": 91,
            "strong_alignment": True,
            "repeated_historical_confirmation": True,
            "regime_match": True,
            "m15_structure_exists": True,
            "guardrail_blocked": True,
        }
    )

    assert result["status"] == "BLOCKED"
    assert result["bonus"] == 0
    assert "Memory cannot bypass hard rejection or guardrail block" in result["reasons"]


def test_production_promotion_backtest_hits_targets_and_excludes_observers():
    report = build_production_promotion_report()
    window_365d = report["windows"]["365D"]

    assert production_promotion_success(window_365d) is True
    assert window_365d["after"]["pf"] >= 1.9
    assert window_365d["after"]["win_rate"] >= 62
    assert 70 <= window_365d["after"]["trades"] <= 90
    assert window_365d["after"]["max_drawdown"] <= 4.0
    assert report["production_symbols"] == ["XAUUSD", "US30"]
    assert set(report["sandbox_excluded"]) == {"BTCUSD", "NAS100"}
    assert set(report["observer_excluded"]) == {"EURUSD", "GBPUSD"}
    assert report["current_production_baseline"] == CURRENT_PRODUCTION_BASELINE


def test_sprint_18_forensic_audit_safety_passes():
    report = build_production_promotion_report()
    audit = report["audit"]

    assert audit["level"] == "FORENSIC"
    assert audit["conflicts_found"] is False
    assert audit["threshold_drift"] is False
    assert audit["hidden_order_paths"]["status"] == "PASS"
    assert audit["metric_contamination"]["status"] == "PASS"
    assert audit["production_baseline_integrity"] is True
    assert report["decision"] == "PASS"


def test_position_manager_adds_efde_recommendation_without_mt5_request(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "symbol_registry.yaml").write_text(
        """
tier_1_production:
  - US30
tier_2_filtered_production:
  - XAUUSD
tier_3_demo_sandbox:
  - BTCUSD
  - NAS100
tier_4_observer_only:
  - EURUSD
  - GBPUSD
execution:
  observer_execution_allowed: false
  demo_sandbox_execution_allowed: false
  autonomous_execution_allowed: false
""",
        encoding="utf-8",
    )
    manager = PositionManager(connector=object(), config_dir=config_dir)
    position = {
        "symbol": "XAUUSD",
        "status": "OPEN",
        "ticket": 1,
        "type": "BUY",
        "price_open": 100.0,
        "sl": 90.0,
        "price_current": 96.0,
        "magic": manager.SENTINEL_MAGIC,
    }

    actions = manager.recommend_position_actions(
        position,
        current_r=-0.4,
        context={"adverse_movement_pct": 0.4, "fps": 80, "human_approval_required": True},
    )
    submitted = manager.submit_actions(
        subject=position,
        actions=actions,
        mode="assisted",
        confirmation_callback=lambda subject, action: True,
    )

    efde = next(action for action in actions if action["type"] == "EARLY_EXIT_RECOMMENDED")
    assert efde["requires_confirmation"] is True
    assert efde["request"] == {}
    assert submitted[0]["type"] == "EARLY_EXIT_RECOMMENDED"
    assert submitted[0]["submitted"] is False
    assert submitted[0]["result"] == "NO_MT5_REQUEST"
