from __future__ import annotations

from pathlib import Path

from backend.execution_engine.paper_trade_session import PaperTradeSession


def write_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "execution.yaml").write_text(
        """
execution_mode: advisor
allowed_modes:
  - advisor
  - assisted
  - autonomous
assisted_mode:
  require_manual_confirmation: true
  max_slippage_points: 50
  allow_market_orders: true
  allow_limit_orders: true
  allow_stop_orders: false
safety:
  reject_if_news_lock: true
  reject_if_risk_blocked: true
  reject_if_guardrail_blocked: true
  reject_if_confidence_below: 90
  reject_if_rr_below: 3.0
  reject_if_spread_too_high: true
""",
        encoding="utf-8",
    )
    (config_dir / "readiness.yaml").write_text(
        """
enabled: true
checks:
  mt5_connected: true
  account_verified: true
  risk_allowed: true
  news_clear: true
  killzone_valid: true
  guardrails_pass: true
  spread_acceptable: true
  lot_valid: true
  rr_minimum: 3.0
  execution_mode_assisted: true
  manual_confirmation_required: true
spread_limits:
  XAUUSD: 80
allowed_accounts:
  demo:
    - MetaQuotes-Demo
  live: []
""",
        encoding="utf-8",
    )
    (config_dir / "position_manager.yaml").write_text(
        """
enabled: true
mode: assisted
breakeven:
  enabled: true
  trigger_r: 1.0
  buffer_points:
    XAUUSD: 20
partial_profit:
  enabled: true
  trigger_r: 2.0
  close_percent: 30
trailing:
  enabled: true
  mode: structure
""",
        encoding="utf-8",
    )
    (config_dir / "strategy_guardrails.yaml").write_text(
        """
enabled: true
disabled_trade_symbols: []
blocked_killzones: []
symbol_execution_tiers:
  production:
    - US30
  filtered_production:
    - XAUUSD
  observer_only: []
minimum_execution_confidence: 90
minimum_execution_confidence_by_symbol_type:
  priority: 90
  forex: 95
adaptive_penalties: {}
adaptive_bonuses: {}
robustness_365d:
  enabled: false
confidence_band_adjustment:
  execution_ready_minimum: 95
  hot_minimum: 70
  warm_minimum: 40
forex_rules:
  require_smt_confirmation: false
  allowed_symbols: []
  disabled_symbols: []
narrative_rules:
  block_range_phase: false
  caution_distribution_without_smt: true
""",
        encoding="utf-8",
    )
    (config_dir / "alerts.yaml").write_text(
        """
enabled: true
terminal: true
desktop: false
telegram: false
""",
        encoding="utf-8",
    )
    (config_dir / "journal.yaml").write_text(
        f"""
enabled: true
storage_type: jsonl
path: "{(config_dir.parent / 'journal.jsonl').as_posix()}"
record_diagnostic_plans: true
record_rejected_setups: true
""",
        encoding="utf-8",
    )


def make_session(tmp_path: Path) -> PaperTradeSession:
    config_dir = tmp_path / "config"
    write_config(config_dir)
    return PaperTradeSession(config_dir=config_dir)


def test_full_win_scenario(tmp_path: Path):
    session = make_session(tmp_path)

    result = session.run(scenario="A", approval_callback=lambda _request: True)

    assert result["passed"] is True
    assert result["readiness"]["ready"] is True
    assert result["approval_result"] == "YES"
    assert result["mock_execution"]["result"] == "MOCK_SUBMITTED"
    assert result["session"]["outcome"] == "WIN"
    assert result["session"]["realized_rr"] == 3.2
    assert result["session"]["actions_taken"] == [
        "MOVE_SL_TO_BE",
        "PARTIAL_CLOSE",
        "TRAIL_STRUCTURE",
        "CLOSE_POSITION",
    ]
    assert "PAPER DRILL PASSED" in result["terminal_output"]


def test_breakeven_scenario(tmp_path: Path):
    session = make_session(tmp_path)

    result = session.run(scenario="B", approval_callback=lambda _request: True)

    assert result["passed"] is True
    assert result["session"]["outcome"] == "BREAKEVEN"
    assert result["session"]["realized_rr"] == 0.0
    assert "MOVE_SL_TO_BE" in result["session"]["actions_taken"]
    assert "PARTIAL_CLOSE" not in result["session"]["actions_taken"]


def test_stop_loss_scenario(tmp_path: Path):
    session = make_session(tmp_path)

    result = session.run(scenario="C", approval_callback=lambda _request: True)

    assert result["passed"] is True
    assert result["session"]["outcome"] == "LOSS"
    assert result["session"]["realized_rr"] == -1.0
    assert result["session"]["actions_taken"] == ["CLOSE_POSITION"]


def test_readiness_blocked(tmp_path: Path):
    session = make_session(tmp_path)

    result = session.run(scenario="READINESS_BLOCKED", approval_callback=lambda _request: True)

    assert result["passed"] is True
    assert result["session"]["status"] == "CANCELLED"
    assert result["approval_result"] == "NOT_REQUESTED"
    assert result["mock_execution"]["submitted"] is False
    assert result["readiness"]["ready"] is False
    assert any("Spread too high" in reason for reason in result["readiness"]["blocking_reasons"])


def test_approval_rejected(tmp_path: Path):
    session = make_session(tmp_path)

    result = session.run(scenario="APPROVAL_REJECTED", approval_callback=lambda _request: False)

    assert result["passed"] is True
    assert result["session"]["status"] == "CANCELLED"
    assert result["approval_result"] == "NO"
    assert result["mock_execution"]["submitted"] is False
    assert result["readiness"]["ready"] is True
