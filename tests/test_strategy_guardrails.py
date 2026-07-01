from __future__ import annotations

from pathlib import Path

from backend.guardrails.strategy_guardrails import StrategyGuardrails


def write_guardrail_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "strategy_guardrails.yaml").write_text(
        """
enabled: true
disabled_trade_symbols:
  - GBPUSD
blocked_killzones:
  - london_continuation
symbol_execution_tiers:
  production:
    - US30
  filtered_production:
    - XAUUSD
  demo_sandbox:
    - BTCUSD
    - NAS100
  observer_only:
    - EURUSD
    - GBPUSD
minimum_execution_confidence: 95
minimum_execution_confidence_by_symbol_type:
  priority: 90
  forex: 95
adaptive_penalties:
  london_open: 12
  london_continuation: 100
  range_phase: 8
  no_smt_confirmation: 5
  no_smt_expansion: 5
  forex_without_smt: 7
  distribution_without_smt: 5
adaptive_bonuses:
  new_york_continuation: 5
robustness_365d:
  enabled: true
  eurusd_disabled_reason: "EURUSD observer mode: 365D PF below threshold"
  xauusd_require_smt_or_confidence: 95
  xauusd_block_london_open_without_smt: true
  xauusd_block_london_continuation: true
  us30_allowed_killzones:
    - new_york_open
    - new_york_continuation
  us30_preferred_killzone: new_york_continuation
  block_london_continuation: true
  london_open_requires_smt: true
  no_smt_expansion_penalty: true
  warn_no_smt_loss_cluster: true
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


def make_guardrails(tmp_path: Path) -> StrategyGuardrails:
    config_dir = tmp_path / "config"
    write_guardrail_config(config_dir)
    return StrategyGuardrails(config_dir=config_dir)


def test_gbpusd_disabled(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="GBPUSD",
        total_confidence=100,
        killzone={"active_killzone": "london_open"},
        smt={"smt_detected": True},
        narrative_phase="expansion",
    )

    assert result["status"] == "BLOCKED"
    assert "GBPUSD disabled by strategy guardrail" in result["reasons"]
    assert "London Open robustness penalty" in result["warnings"]


def test_london_continuation_penalty(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="XAUUSD",
        total_confidence=100,
        killzone={"active_killzone": "london_continuation"},
        smt={"smt_detected": True},
        narrative_phase="expansion",
    )

    assert result["status"] == "BLOCKED"
    assert result["original_confidence"] == 100
    assert result["guardrail_penalty_total"] == 100
    assert result["guardrail_adjusted_confidence"] == 0
    assert "London continuation blocked by strategy guardrail" in result["reasons"]
    assert "London continuation penalty" in result["guardrail_warnings"]


def test_confidence_90_to_94_warns_only_when_adjusted_confidence_passes(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="US30",
        total_confidence=94,
        killzone={"active_killzone": "new_york_open"},
        smt={"smt_detected": True},
        narrative_phase="expansion",
    )

    assert result["status"] == "PASS"
    assert result["adjusted_confidence_band"] == "HOT"
    assert result["guardrail_adjusted_confidence"] == 94
    assert "Confidence 90-94 requires caution" in result["guardrail_warnings"]


def test_forex_without_smt_penalty_and_adjusted_confidence_threshold(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="EURUSD",
        total_confidence=100,
        killzone={"active_killzone": "new_york_open"},
        smt={"smt_detected": False},
        narrative_phase="expansion",
    )

    assert result["status"] == "BLOCKED"
    assert result["guardrail_penalty_total"] == 12
    assert result["guardrail_adjusted_confidence"] == 88
    assert "EURUSD observer mode: 365D PF below threshold" in result["reasons"]
    assert "Forex without SMT penalty" in result["guardrail_warnings"]
    assert "No SMT expansion robustness penalty" in result["guardrail_warnings"]
    assert "Adjusted confidence below execution threshold" in result["reasons"]


def test_nas100_observer_mode_blocked(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="NAS100",
        total_confidence=100,
        killzone={"active_killzone": "new_york_open"},
        smt={"smt_detected": True},
        narrative_phase="expansion",
    )

    assert result["status"] == "BLOCKED"
    assert "NAS100 demo sandbox: production execution disabled" in result["reasons"]


def test_range_phase_penalty(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="US30",
        total_confidence=100,
        killzone={"active_killzone": "new_york_open"},
        smt={"smt_detected": True},
        narrative_phase="range",
    )

    assert result["status"] == "PASS"
    assert result["guardrail_penalty_total"] == 8
    assert result["guardrail_adjusted_confidence"] == 92
    assert "Range phase penalty" in result["guardrail_warnings"]


def test_distribution_without_smt_warns(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="US30",
        total_confidence=100,
        killzone={"active_killzone": "new_york_open"},
        smt={"smt_detected": False},
        narrative_phase="distribution",
    )

    assert result["status"] == "PASS"
    assert result["reasons"] == []
    assert result["guardrail_penalty_total"] == 10
    assert result["guardrail_adjusted_confidence"] == 90
    assert "No SMT confirmation penalty" in result["guardrail_warnings"]
    assert "Distribution without SMT penalty" in result["guardrail_warnings"]


def test_hard_guardrails_preserve_old_veto_behavior(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate_hard(
        symbol="XAUUSD",
        total_confidence=94,
        killzone={"active_killzone": "london_continuation"},
        smt={"smt_detected": False},
        narrative_phase="range",
    )

    assert result["status"] == "BLOCKED"
    assert "London continuation blocked by strategy guardrail" in result["reasons"]
    assert "Confidence below guarded execution threshold" in result["reasons"]
    assert "Range phase blocked by strategy guardrail" in result["reasons"]


def test_adaptive_hard_blocks_still_block(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="XAUUSD",
        total_confidence=100,
        killzone={"active_killzone": "new_york_open", "is_valid": False},
        smt={"smt_detected": True},
        narrative_phase="expansion",
        risk_blocked=True,
        news_lock_active=True,
        mss_confirmed=False,
        rr_to_final=2.5,
    )

    assert result["status"] == "BLOCKED"
    assert "Risk Governor blocked" in result["reasons"]
    assert "High impact news lock active" in result["reasons"]
    assert "Outside valid killzone" in result["reasons"]
    assert "MSS not confirmed" in result["reasons"]
    assert "RR below 3" in result["reasons"]


def test_xauusd_london_open_missing_smt_warns_until_sample_threshold(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="XAUUSD",
        total_confidence=100,
        killzone={"active_killzone": "london_open"},
        smt={"smt_detected": False, "available": False},
        narrative_phase="expansion",
    )

    assert result["status"] == "PASS"
    assert "XAUUSD London Open requires SMT alignment" not in result["reasons"]
    assert "London Open requires SMT alignment" not in result["reasons"]
    assert "XAUUSD SMT warning only until sample >= 10" in result["guardrail_warnings"]
    assert "No SMT loss-cluster warning" in result["guardrail_warnings"]


def test_xauusd_missing_smt_does_not_hard_block_below_sample_threshold(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    result = guardrails.evaluate(
        symbol="XAUUSD",
        total_confidence=94,
        killzone={"active_killzone": "new_york_open"},
        smt={"smt_detected": False, "available": False},
        narrative_phase="reversal",
    )

    assert result["status"] == "PASS"
    assert "XAUUSD requires SMT alignment or confidence >= 95" not in result["reasons"]
    assert "XAUUSD SMT warning only until sample >= 10" in result["guardrail_warnings"]


def test_xauusd_smt_hard_rule_activates_after_sample_threshold(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)
    guardrails.config["robustness_365d"]["xauusd_smt_sample_trades"] = 10

    result = guardrails.evaluate(
        symbol="XAUUSD",
        total_confidence=94,
        killzone={"active_killzone": "new_york_open"},
        smt={"smt_detected": False, "available": False},
        narrative_phase="reversal",
    )

    assert result["status"] == "BLOCKED"
    assert "XAUUSD requires SMT alignment or confidence >= 95" in result["reasons"]


def test_us30_rejects_london_and_prefers_ny_continuation(tmp_path: Path):
    guardrails = make_guardrails(tmp_path)

    london = guardrails.evaluate(
        symbol="US30",
        total_confidence=100,
        killzone={"active_killzone": "london_open"},
        smt={"smt_detected": True},
        narrative_phase="expansion",
    )
    ny_continuation = guardrails.evaluate(
        symbol="US30",
        total_confidence=90,
        killzone={"active_killzone": "new_york_continuation"},
        smt={"smt_detected": False, "available": False},
        narrative_phase="reversal",
    )

    assert london["status"] == "BLOCKED"
    assert "US30 London session blocked by 365D robustness guardrail" in london["reasons"]
    assert ny_continuation["status"] == "PASS"
    assert ny_continuation["guardrail_bonus_total"] == 5
    assert ny_continuation["guardrail_adjusted_confidence"] == 95
    assert "NY Continuation robustness bonus" in [item["reason"] for item in ny_continuation["bonuses"]]
