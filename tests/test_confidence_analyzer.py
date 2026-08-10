from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer


def write_configs(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "rule_weights.yaml").write_text(
        """
modes:
  balanced:
    daily_bias: 15
    h4_narrative: 20
    liquidity_sweep: 20
    mss: 20
    fvg_quality: 15
    session_quality: 5
    target_clarity: 5
    minimum_confidence: 90
""",
        encoding="utf-8",
    )
    (config_dir / "trading_rules.yaml").write_text(
        """
markets:
  allowed:
    - "XAUUSD"
    - "US30"
    - "EURUSD"
    - "GBPUSD"
  forex:
    enabled: true
    symbols:
      - "EURUSD"
      - "GBPUSD"
    minimum_confidence: 95
    require_a_grade_setup: true
hard_filters:
  minimum_rr: 3.0
""",
        encoding="utf-8",
    )
    (config_dir / "market_sessions.yaml").write_text(
        """
timezone: "Africa/Lagos"
markets:
  XAUUSD:
    sessions:
      london:
        start: "08:00"
        end: "11:00"
      new_york:
        start: "13:30"
        end: "16:00"
  US30:
    sessions:
      new_york:
        start: "13:30"
        end: "16:00"
  EURUSD:
    sessions:
      london:
        start: "08:00"
        end: "11:00"
      new_york:
        start: "13:30"
        end: "16:00"
  GBPUSD:
    sessions:
      london:
        start: "08:00"
        end: "11:00"
      new_york:
        start: "13:30"
        end: "16:00"
""",
        encoding="utf-8",
    )
    (config_dir / "killzones.yaml").write_text(
        """
timezone: WAT
killzones:
  london_open:
    start: "08:00"
    end: "09:30"
    symbols:
      - XAUUSD
      - EURUSD
      - GBPUSD
    quality_score: 10
    commentary: "London open raid window."
  london_continuation:
    start: "09:30"
    end: "11:00"
    symbols:
      - XAUUSD
      - EURUSD
      - GBPUSD
    quality_score: 7
    commentary: "London continuation or reversal confirmation window."
  new_york_open:
    start: "13:30"
    end: "15:00"
    symbols:
      - XAUUSD
      - US30
      - EURUSD
      - GBPUSD
    quality_score: 10
    commentary: "New York open volatility and displacement window."
  new_york_continuation:
    start: "15:00"
    end: "16:00"
    symbols:
      - XAUUSD
      - US30
      - EURUSD
      - GBPUSD
    quality_score: 7
    commentary: "New York continuation or target delivery window."
  dead_zone:
    start: "16:00"
    end: "23:59"
    symbols:
      - XAUUSD
      - US30
      - EURUSD
      - GBPUSD
    quality_score: 0
    commentary: "Outside preferred execution window. Avoid new trades."
""",
        encoding="utf-8",
    )
    (config_dir / "smt_pairs.yaml").write_text(
        """
enabled: true
pairs:
  - name: "EURUSD_GBPUSD"
    primary: "EURUSD"
    comparison: "GBPUSD"
    weight: 10
    preferred_sessions:
      - london_open
      - london_continuation
      - new_york_open
  - name: "XAUUSD_EURUSD"
    primary: "XAUUSD"
    comparison: "EURUSD"
    weight: 7
    preferred_sessions:
      - london_open
      - new_york_open
  - name: "XAUUSD_GBPUSD"
    primary: "XAUUSD"
    comparison: "GBPUSD"
    weight: 7
    preferred_sessions:
      - london_open
      - new_york_open
  - name: "US30_XAUUSD"
    primary: "US30"
    comparison: "XAUUSD"
    weight: 5
    preferred_sessions:
      - new_york_open
""",
        encoding="utf-8",
    )
    (config_dir / "strategy_guardrails.yaml").write_text(
        """
enabled: true
disabled_trade_symbols:
  - GBPUSD
blocked_killzones:
  - london_continuation
minimum_execution_confidence: 95
confidence_band_adjustment:
  execution_ready_minimum: 95
  hot_minimum: 70
  warm_minimum: 40
forex_rules:
  require_smt_confirmation: true
  allowed_symbols:
    - EURUSD
  disabled_symbols:
    - GBPUSD
narrative_rules:
  block_range_phase: true
  caution_distribution_without_smt: true
""",
        encoding="utf-8",
    )


def make_analyzer(tmp_path: Path) -> ConfidenceAnalyzer:
    config_dir = tmp_path / "config"
    write_configs(config_dir)
    return ConfidenceAnalyzer(
        connector=object(),
        trend_analyzer=object(),
        liquidity_analyzer=object(),
        ict_analyzer=object(),
        config_dir=config_dir,
    )


def aligned_inputs():
    trend = {
        "daily_bias": "bullish",
        "h4_bias": "bullish",
        "overall_bias": "bullish",
    }
    liquidity = {
        "latest_sweep": {"strength": "strong"},
        "nearest_buy_side_target": {"name": "PDH"},
        "nearest_sell_side_target": None,
    }
    ict = {
        "mss": {"detected": True, "direction": "bullish"},
        "fvg": {"detected": True, "direction": "bullish", "grade": "A"},
        "order_block": {"detected": True, "direction": "bullish"},
        "premium_discount": {"current_zone": "discount"},
        "rejection_reasons": [],
    }
    context = {
        "symbol": "XAUUSD",
        "analysis_time": datetime(2026, 6, 26, 9, 0, tzinfo=ZoneInfo("Africa/Lagos")),
        "risk_reward": 3.0,
    }
    return trend, liquidity, ict, context


def test_score_calculation(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()

    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    assert scores == {
        "daily_bias": 15,
        "h4_narrative": 20,
        "liquidity_sweep": 20,
        "mss": 20,
        "fvg_quality": 15,
        "session_quality": 5,
        "target_clarity": 5,
        "smt": 0,
    }
    assert analyzer.calculate_total_confidence(scores) == 100


def test_hard_rejection_rules(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    ict["mss"] = {"detected": False, "direction": "bullish"}
    ict["premium_discount"] = {"current_zone": "unavailable"}
    context = {
        "symbol": "XAUUSD",
        "analysis_time": datetime(2026, 6, 26, 12, 0, tzinfo=ZoneInfo("Africa/Lagos")),
        "risk_reward": 2.0,
        "high_impact_news_lock_active": True,
        "daily_loss_limit_hit": True,
        "max_trades_per_day_hit": True,
    }
    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    reasons = analyzer.evaluate_hard_rejections(
        symbol="XAUUSD",
        trend=trend,
        liquidity=liquidity,
        ict=ict,
        scores=scores,
        total_confidence=analyzer.calculate_total_confidence(scores),
        context=context,
        direction="bullish",
    )

    assert "Outside valid killzone" in reasons
    assert "MSS not confirmed" in reasons
    assert "Premium/discount unavailable" in reasons
    assert "RR below 3" in reasons
    assert "High impact news lock active" in reasons
    assert "Daily loss limit hit" in reasons
    assert "Max trades per day hit" in reasons


def test_approval_threshold(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    context = {
        **context,
        "analysis_time": datetime(2026, 6, 26, 14, 0, tzinfo=ZoneInfo("Africa/Lagos")),
        "killzone": {"active_killzone": "new_york_open", "is_valid": True, "quality_score": 10},
        "smt": {"smt_detected": True},
    }
    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")
    total = analyzer.calculate_total_confidence(scores)

    reasons = analyzer.evaluate_hard_rejections(
        symbol="US30",
        trend=trend,
        liquidity=liquidity,
        ict=ict,
        scores=scores,
        total_confidence=total,
        context=context,
        direction="bullish",
    )

    assert total >= analyzer.rule_weights["minimum_confidence"]
    assert reasons == []


def test_direction_alignment_rejects_mismatch(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    ict["fvg"] = {"detected": True, "direction": "bearish", "grade": "A"}
    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    reasons = analyzer.evaluate_hard_rejections(
        symbol="XAUUSD",
        trend=trend,
        liquidity=liquidity,
        ict=ict,
        scores=scores,
        total_confidence=analyzer.calculate_total_confidence(scores),
        context=context,
        direction="bullish",
    )

    assert "FVG direction not aligned with MSS" in reasons


def test_decision_rejected_when_below_threshold(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    scores = {
        "daily_bias": 15,
        "h4_narrative": 0,
        "liquidity_sweep": 0,
        "mss": 0,
        "fvg_quality": 0,
        "session_quality": 5,
        "target_clarity": 0,
        "smt": 0,
    }

    assert analyzer.calculate_total_confidence(scores) == 20
    assert analyzer.calculate_total_confidence(scores) < analyzer.rule_weights["minimum_confidence"]


def test_session_quality_partial_for_continuation_killzone(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    context = {
        **context,
        "analysis_time": datetime(2026, 6, 26, 10, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    }

    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    assert scores["session_quality"] == 4
    assert context["killzone"]["active_killzone"] == "london_continuation"


def test_smt_alignment_adds_bonus(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    context["smt"] = {
        "smt_detected": True,
        "direction": "bullish",
        "confidence": 7,
    }

    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    assert scores["smt"] == 7


def test_smt_conflict_subtracts_and_warns(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    context = {
        "smt": {
            "smt_detected": True,
            "direction": "bearish",
            "confidence": 7,
        }
    }

    assert analyzer.score_smt(context, direction="bullish") == -7
    assert analyzer.build_warnings(context, direction="bullish") == ["SMT conflicts with setup direction"]


def test_xauusd_inside_london_session(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    context = {
        "symbol": "XAUUSD",
        "analysis_time": datetime(2026, 6, 26, 8, 30, tzinfo=ZoneInfo("Africa/Lagos")),
    }

    assert analyzer.is_valid_session(context) is True


def test_xauusd_outside_session(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    context = {
        "symbol": "XAUUSD",
        "analysis_time": datetime(2026, 6, 26, 12, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    }

    assert analyzer.is_valid_session(context) is False


def test_us30_inside_new_york_session(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    context = {
        "symbol": "US30",
        "analysis_time": datetime(2026, 6, 26, 14, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    }

    assert analyzer.is_valid_session(context) is True


def test_us30_outside_new_york_session(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    context = {
        "symbol": "US30",
        "analysis_time": datetime(2026, 6, 26, 11, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    }

    assert analyzer.is_valid_session(context) is False


def test_eurusd_inside_london_session(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    context = {
        "symbol": "EURUSD",
        "analysis_time": datetime(2026, 6, 26, 8, 30, tzinfo=ZoneInfo("Africa/Lagos")),
    }

    assert analyzer.is_valid_session(context) is True


def test_gbpusd_outside_forex_session(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    context = {
        "symbol": "GBPUSD",
        "analysis_time": datetime(2026, 6, 26, 12, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    }

    assert analyzer.is_valid_session(context) is False


def test_session_filter_converts_utc_to_wat(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    context = {
        "symbol": "XAUUSD",
        "analysis_time": datetime(2026, 6, 26, 7, 30, tzinfo=ZoneInfo("UTC")),
    }

    assert analyzer.is_valid_session(context) is True


def test_forex_minimum_confidence_is_95(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)

    assert analyzer.get_minimum_confidence("EURUSD") == 95
    assert analyzer.get_minimum_confidence("GBPUSD") == 95
    assert analyzer.get_minimum_confidence("XAUUSD") == 90


def test_forex_requires_a_grade_setup(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    context = {**context, "symbol": "EURUSD"}
    ict["fvg"] = {"detected": True, "direction": "bullish", "grade": "B"}
    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    reasons = analyzer.evaluate_hard_rejections(
        symbol="EURUSD",
        trend=trend,
        liquidity=liquidity,
        ict=ict,
        scores=scores,
        total_confidence=analyzer.calculate_total_confidence(scores),
        context=context,
        direction="bullish",
    )

    assert "Forex requires A-grade setup" in reasons


def test_forex_requires_aligned_bias_and_liquidity_sweep(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    context = {**context, "symbol": "GBPUSD"}
    trend = {**trend, "daily_bias": "bearish", "h4_bias": "bearish"}
    liquidity = {**liquidity, "latest_sweep": None}
    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    reasons = analyzer.evaluate_hard_rejections(
        symbol="GBPUSD",
        trend=trend,
        liquidity=liquidity,
        ict=ict,
        scores=scores,
        total_confidence=analyzer.calculate_total_confidence(scores),
        context=context,
        direction="bullish",
    )

    assert "Forex daily bias not aligned" in reasons
    assert "Forex 4H narrative not aligned" in reasons
    assert "Forex liquidity sweep missing" in reasons


def test_guardrail_reasons_append_to_confidence_rejections(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    context = {
        **context,
        "symbol": "GBPUSD",
        "killzone": {"active_killzone": "london_open", "is_valid": True, "quality_score": 10},
        "smt": {"smt_detected": False},
        "narrative": {"phase": "expansion"},
    }
    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    reasons = analyzer.evaluate_hard_rejections(
        symbol="GBPUSD",
        trend=trend,
        liquidity=liquidity,
        ict=ict,
        scores=scores,
        total_confidence=analyzer.calculate_total_confidence(scores),
        context=context,
        direction="bullish",
    )

    assert "GBPUSD disabled by strategy guardrail" in reasons
    assert "Adjusted confidence below execution threshold" in reasons
    assert context["guardrail"]["status"] == "BLOCKED"
    assert context["guardrail"]["guardrail_penalty_total"] == 12
    assert "Forex without SMT penalty" in context["guardrail"]["guardrail_warnings"]
    assert "No SMT expansion robustness penalty" in context["guardrail"]["guardrail_warnings"]


def test_news_lock_rejects_affected_symbol(tmp_path: Path):
    analyzer = make_analyzer(tmp_path)
    trend, liquidity, ict, context = aligned_inputs()
    context = {
        **context,
        "news_status": {
            "enabled": True,
            "lock_active": True,
            "event_name": "CPI",
            "minutes_to_event": 18,
            "affected_symbols": ["XAUUSD", "US30", "EURUSD", "GBPUSD"],
            "reason": "High impact news lock active: CPI in 18 minutes.",
        },
    }
    scores = analyzer.calculate_scores(trend, liquidity, ict, context, direction="bullish")

    reasons = analyzer.evaluate_hard_rejections(
        symbol="XAUUSD",
        trend=trend,
        liquidity=liquidity,
        ict=ict,
        scores=scores,
        total_confidence=analyzer.calculate_total_confidence(scores),
        context=context,
        direction="bullish",
    )

    assert "High impact news lock active" in reasons


def test_confidence_bands():
    assert ConfidenceAnalyzer.get_confidence_band(39) == ("COLD", "Ignore")
    assert ConfidenceAnalyzer.get_confidence_band(40) == ("WARM", "Monitor")
    assert ConfidenceAnalyzer.get_confidence_band(69) == ("WARM", "Monitor")
    assert ConfidenceAnalyzer.get_confidence_band(70) == ("HOT", "Prepare")
    assert ConfidenceAnalyzer.get_confidence_band(89) == ("HOT", "Prepare")
    assert ConfidenceAnalyzer.get_confidence_band(90) == ("EXECUTION_READY", "Trade Allowed")
