from __future__ import annotations

from pathlib import Path

import pytest

from backend.execution_engine.readiness_checker import ReadinessChecker


class FakeConnector:
    def __init__(self, *, connected: bool = True):
        self.connected = connected

    def is_initialized(self) -> bool:
        return self.connected

    def get_account_info(self) -> dict:
        return {"login": 123456, "server": "MetaQuotes-Demo", "account_mode": "demo", "balance": 2000.0, "equity": 2000.0}

    def get_symbol_info(self, symbol: str) -> dict:
        return {"volume_min": 0.01, "volume_max": 5.0, "volume_step": 0.01, "point": 0.01}

    def get_latest_tick(self, symbol: str) -> dict:
        return {"bid": 4000.0, "ask": 4000.2}


def write_config(config_dir: Path) -> None:
    config_dir.mkdir()
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


def make_checker(tmp_path: Path, *, connected: bool = True) -> ReadinessChecker:
    config_dir = tmp_path / "config"
    write_config(config_dir)
    return ReadinessChecker(connector=FakeConnector(connected=connected), config_dir=config_dir)


def plan(**overrides):
    value = {
        "symbol": "XAUUSD",
        "risk": {"lot_size": 0.02, "rr_to_tp3": 3.2},
    }
    value.update(overrides)
    return value


def context(**overrides):
    value = {
        "account": {"login": 123456, "server": "MetaQuotes-Demo", "account_mode": "demo"},
        "risk": {"permission": {"status": "ALLOWED", "trade_allowed": True}},
        "news": {"status": "CLEAR", "lock_active": False},
        "killzone": {"active_killzone": "new_york_open", "is_valid": True},
        "guardrail": {"status": "PASS", "reasons": []},
        "spread_points": 20,
        "symbol_info": {"volume_min": 0.01, "volume_max": 5.0, "volume_step": 0.01},
    }
    value.update(overrides)
    return value


def test_all_pass(tmp_path: Path):
    checker = make_checker(tmp_path)

    result = checker.check(plan(), context=context(), execution_mode="assisted", manual_confirmation_required=True)

    assert result["ready"] is True
    assert result["score"] == 11
    assert result["checks_passed"] == 11
    assert result["checks_failed"] == 0
    assert result["blocking_reasons"] == []


def test_news_blocked(tmp_path: Path):
    checker = make_checker(tmp_path)

    result = checker.check(
        plan(),
        context=context(news={"status": "LOCKED", "lock_active": True, "reason": "News lock active"}),
        execution_mode="assisted",
        manual_confirmation_required=True,
    )

    assert result["ready"] is False
    assert "News lock active" in result["blocking_reasons"]


def test_spread_high(tmp_path: Path):
    checker = make_checker(tmp_path)

    result = checker.check(plan(), context=context(spread_points=100), execution_mode="assisted", manual_confirmation_required=True)

    assert result["ready"] is False
    assert any("Spread too high" in reason for reason in result["blocking_reasons"])


def test_wrong_mode(tmp_path: Path):
    checker = make_checker(tmp_path)

    result = checker.check(plan(), context=context(), execution_mode="advisor", manual_confirmation_required=True)

    assert result["ready"] is False
    assert "Execution mode must be assisted" in result["blocking_reasons"]


def test_multiple_failures_and_score_logic(tmp_path: Path):
    checker = make_checker(tmp_path, connected=False)

    result = checker.check(
        plan(risk={"lot_size": 0.0, "rr_to_tp3": 2.0}),
        context=context(
            risk={"permission": {"status": "BLOCKED", "trade_allowed": False}},
            killzone={"active_killzone": "none", "is_valid": False},
            guardrail={"status": "BLOCKED", "reasons": ["London continuation blocked"]},
        ),
        execution_mode="advisor",
        manual_confirmation_required=False,
    )

    assert result["ready"] is False
    assert result["checks_failed"] == 8
    assert result["score"] == 3
    assert "MT5 terminal disconnected" in result["blocking_reasons"]
    assert "Risk Governor blocked" in result["blocking_reasons"]
    assert "Invalid killzone" in result["blocking_reasons"]
    assert "London continuation blocked" in result["blocking_reasons"]
    assert "Lot size must be greater than zero" in result["blocking_reasons"]
    assert "RR below 3.0" in result["blocking_reasons"]
    assert "Execution mode must be assisted" in result["blocking_reasons"]
    assert "Manual confirmation is required" in result["blocking_reasons"]


def test_account_failure_path(tmp_path: Path):
    checker = make_checker(tmp_path)

    result = checker.check(
        plan(),
        context=context(account={"login": 123456, "server": "Wrong-Demo", "account_mode": "demo"}),
        execution_mode="assisted",
        manual_confirmation_required=True,
    )

    assert result["ready"] is False
    assert any("Wrong-Demo" in reason for reason in result["blocking_reasons"])


@pytest.mark.parametrize(
    ("check", "plan_updates", "context_updates", "execution_mode", "manual_confirmation", "connected", "expected_reason"),
    [
        ("mt5_connected", {}, {}, "assisted", True, False, "MT5 terminal disconnected"),
        ("account_verified", {}, {"account": {"login": 123456, "server": "Wrong-Demo", "account_mode": "demo"}}, "assisted", True, True, "Wrong-Demo"),
        ("risk_allowed", {}, {"risk": {"permission": {"status": "BLOCKED", "trade_allowed": False}}}, "assisted", True, True, "Risk Governor blocked"),
        ("news_clear", {}, {"news": {"status": "LOCKED", "lock_active": True, "reason": "News lock active"}}, "assisted", True, True, "News lock active"),
        ("killzone_valid", {}, {"killzone": {"active_killzone": "none", "is_valid": False}}, "assisted", True, True, "Invalid killzone"),
        ("guardrails_pass", {}, {"guardrail": {"status": "BLOCKED", "reasons": ["Guardrail blocked"]}}, "assisted", True, True, "Guardrail blocked"),
        ("spread_acceptable", {}, {"spread_points": 100}, "assisted", True, True, "Spread too high"),
        ("lot_valid", {"risk": {"lot_size": 0.0, "rr_to_tp3": 3.2}}, {}, "assisted", True, True, "Lot size must be greater than zero"),
        ("rr_validation", {"risk": {"lot_size": 0.02, "rr_to_tp3": 2.0}}, {}, "assisted", True, True, "RR below 3.0"),
        ("execution_mode_assisted", {}, {}, "advisor", True, True, "Execution mode must be assisted"),
        ("manual_confirmation_required", {}, {}, "assisted", False, True, "Manual confirmation is required"),
    ],
)
def test_each_failure_path(
    tmp_path: Path,
    check: str,
    plan_updates: dict,
    context_updates: dict,
    execution_mode: str,
    manual_confirmation: bool,
    connected: bool,
    expected_reason: str,
):
    checker = make_checker(tmp_path, connected=connected)

    result = checker.check(
        plan(**plan_updates),
        context=context(**context_updates),
        execution_mode=execution_mode,
        manual_confirmation_required=manual_confirmation,
    )
    result_by_check = {item["check"]: item for item in result["results"]}

    assert result["ready"] is False
    assert result_by_check[check]["status"] == "FAIL"
    assert any(expected_reason in reason for reason in result["blocking_reasons"])
