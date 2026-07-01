from __future__ import annotations

from backend.market_watch_engine.market_watch_engine import MarketWatchEngine
from backend.market_watch_engine.pattern_detector import PatternDetector
from backend.market_watch_engine.session_specialization import SessionSpecialization
from backend.market_watch_engine.strategy_planners import StrategyPlanners
from backend.market_watch_engine.strategy_scorer import StrategyScorer
from backend.market_watch_engine.strategy_selector import StrategySelector
from scripts.run_backtest_365d import approved_robustness_metrics, metrics_within_tolerance, normalize_metrics
from scripts.run_market_watch_backtest import build_market_watch_report


def test_session_specialization_returns_symbol_session_status():
    engine = SessionSpecialization()

    us30 = engine.evaluate("US30", "new_york_open")
    london = engine.evaluate("US30", "london_continuation")
    nas100 = engine.evaluate("USTEC", "new_york_open")

    assert us30["session_status"] == "preferred"
    assert us30["session_quality"] == 92
    assert london["session_status"] == "blocked"
    assert nas100["symbol"] == "NAS100"
    assert nas100["session_status"] == "observer"


def test_pattern_detector_identifies_expected_pattern_types():
    detector = PatternDetector()

    sweep = detector.detect(
        symbol="US30",
        context={"trend_strength": 84, "range_score": 18, "sweep_detected": True, "mss_confirmed": True, "exhaustion_score": 71, "noise_score": 12},
    )
    trend = detector.detect(symbol="NAS100", context={"trend_strength": 82, "range_score": 20, "noise_score": 10})
    chop = detector.detect(symbol="BTCUSD", context={"trend_strength": 30, "range_score": 75, "noise_score": 80})

    assert sweep["dominant_pattern"] == "liquidity_sweep_reversal"
    assert trend["dominant_pattern"] == "trend_continuation"
    assert chop["dominant_pattern"] == "noisy_chop"


def test_strategy_scorer_returns_all_strategy_scores():
    scorer = StrategyScorer()
    pattern = {
        "dominant_pattern": "liquidity_sweep_reversal",
        "trend_strength": 75,
        "range_score": 20,
        "sweep_detected": True,
        "mss_confirmed": True,
        "volatility_expansion": 60,
        "overextension_score": 50,
        "exhaustion_score": 70,
    }

    scores = scorer.score(
        pattern=pattern,
        session={"session_quality": 92},
        context={"fvg_present": True, "order_block_present": True, "premium_discount_alignment": 80, "narrative_alignment": 85},
    )

    assert set(("ict_liquidity", "trend_following", "mean_reversion")).issubset(scores)
    assert scores["ict_liquidity"] >= 70
    assert "reasoning" in scores


def test_strategy_selector_picks_highest_valid_score():
    selector = StrategySelector()

    result = selector.select(
        {"ict_liquidity": 91, "trend_following": 80, "mean_reversion": 74},
        session={"session_quality": 90, "session_status": "preferred"},
        pattern={"dominant_pattern": "liquidity_sweep_reversal", "mss_confirmed": True},
    )

    assert result["selected_strategy"] == "trend_following"
    assert result["selected_weighted_score"] == 96.0
    assert result["status"] == "ADVISORY_SELECTED"


def test_strategy_selector_returns_no_trade_when_scores_are_weak():
    selector = StrategySelector()

    result = selector.select(
        {"ict_liquidity": 50, "trend_following": 57, "mean_reversion": 60},
        session={"session_quality": 80, "session_status": "preferred"},
        pattern={"dominant_pattern": "no_clear_pattern"},
    )

    assert result["selected_strategy"] == "no_trade"
    assert result["status"] == "NO_TRADE"


def test_strategy_selector_noisy_chop_returns_no_trade():
    selector = StrategySelector()

    result = selector.select(
        {"ict_liquidity": 95, "trend_following": 95, "mean_reversion": 95},
        session={"session_quality": 90, "session_status": "preferred"},
        pattern={"dominant_pattern": "noisy_chop", "mss_confirmed": True},
    )

    assert result["selected_strategy"] == "no_trade"
    assert result["weighted_scores"] == {"ict_liquidity": 0.0, "trend_following": 0.0, "mean_reversion": 0.0}


def test_strategy_selector_uses_expectancy_weighted_selection():
    selector = StrategySelector()

    result = selector.select(
        {"ict_liquidity": 100, "trend_following": 80, "mean_reversion": 82},
        session={"session_quality": 90, "session_status": "preferred"},
        pattern={"dominant_pattern": "liquidity_sweep_reversal", "mss_confirmed": True},
    )

    assert result["weighted_scores"]["trend_following"] == 96.0
    assert result["weighted_scores"]["ict_liquidity"] == 80.0
    assert result["selected_strategy"] == "trend_following"


def test_strategy_selector_prefers_trend_following_on_trend_continuation():
    selector = StrategySelector()

    result = selector.select(
        {"ict_liquidity": 98, "trend_following": 76, "mean_reversion": 70},
        session={"session_quality": 82, "session_status": "preferred"},
        pattern={"dominant_pattern": "trend_continuation", "mss_confirmed": True},
    )

    assert result["selected_strategy"] == "trend_following"
    assert result["selected_weighted_score"] == 91.2


def test_strategy_selector_rejects_weak_ict_without_mss_or_smt_sample():
    selector = StrategySelector()

    result = selector.select(
        {"ict_liquidity": 100, "trend_following": 60, "mean_reversion": 60},
        session={"session_quality": 92, "session_status": "preferred"},
        pattern={"dominant_pattern": "liquidity_sweep_reversal", "mss_confirmed": False, "smt_sample": 0},
    )

    assert result["weighted_scores"]["ict_liquidity"] == 20.0
    assert result["selected_strategy"] == "no_trade"


def test_strategy_specific_planners_return_diagnostic_plans():
    planners = StrategyPlanners()

    plan = planners.plan(
        symbol="US30",
        selection={"selected_strategy": "trend_following", "selected_score": 82},
        pattern={"dominant_pattern": "trend_continuation"},
        session={"session_quality": 90},
        context={"pullback_structure": 100.0, "continuation_invalidation": 95.0, "prior_high_low": 110.0},
    )

    assert plan["plan_status"] == "DIAGNOSTIC_PLAN_READY"
    assert plan["plan_quality"] == "diagnostic_only"
    assert plan["execution_allowed"] is False
    assert plan["strategy_name"] == "Trend Following"


def test_market_watch_orchestrator_is_advisory_and_non_production():
    engine = MarketWatchEngine()

    result = engine.analyze(
        "US30",
        context={
            "session": "new_york_open",
            "trend_strength": 84,
            "range_score": 18,
            "sweep_detected": True,
            "mss_confirmed": True,
            "exhaustion_score": 71,
            "fvg_present": True,
            "order_block_present": True,
        },
    )

    assert result["market_watch_status"] == "ADVISORY"
    assert result["affects_production"] is False
    assert result["trade_plan"]["execution_allowed"] is False
    assert result["selected_strategy"] in {"ict_liquidity", "trend_following", "mean_reversion", "no_trade"}


def test_market_watch_advisory_mode_does_not_affect_production_metrics():
    report = build_market_watch_report(
        {
            "production_recalculation_diagnostics": {
                "metrics": {"profit_factor": 0.83, "win_rate": 45.45, "trades_approved": 99, "max_drawdown": 5.0}
            }
        }
    )

    assert report["matches_approved_baseline"] is True
    assert metrics_within_tolerance(normalize_metrics(approved_robustness_metrics()), report["market_watch_advisory_mode"])
    assert report["historical_failed_market_watch"]["pf"] == 0.83
    assert report["market_watch_4_1_weighted"]["pf"] == 1.62
    assert report["market_watch_4_2a_routing_learning"]["pf"] == 1.92
    assert report["market_watch_stage2_result"]["pf"] == 2.23
    assert report["market_watch_experimental_before"]["pf"] == 2.56
    assert report["market_watch_experimental_after"]["pf"] >= 2.8


def test_market_watch_observer_symbols_remain_non_invasive():
    report = build_market_watch_report({})

    for symbol in ("NAS100", "BTCUSD", "EURUSD", "GBPUSD"):
        item = report["strategy_diagnostics"][symbol]
        assert item["affects_production"] is False
        assert item["trade_plan"]["execution_allowed"] is False


def test_market_watch_autonomous_execution_remains_disabled():
    report = build_market_watch_report({})

    assert report["market_watch"]["advisory_only"] is True
    assert report["market_watch"]["affect_production"] is False
    assert report["market_watch_advisory_mode"] == report["approved_baseline"]


def test_market_watch_existing_approved_baseline_remains_protected():
    report = build_market_watch_report({})

    assert report["approved_baseline"] == {"pf": 1.58, "win_rate": 58.7, "trades": 56, "max_drawdown": 2.97}
    assert report["market_watch_advisory_mode"] == report["approved_baseline"]
