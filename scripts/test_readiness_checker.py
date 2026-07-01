"""Offline smoke test for the assisted execution readiness checker."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.execution_engine.readiness_checker import ReadinessChecker


class FakeConnector:
    def is_initialized(self) -> bool:
        return True

    def get_account_info(self) -> dict:
        return {"login": 123456, "server": "MetaQuotes-Demo", "account_mode": "demo", "balance": 2000.0, "equity": 2000.0}

    def get_symbol_info(self, symbol: str) -> dict:
        return {"volume_min": 0.01, "volume_max": 5.0, "volume_step": 0.01, "point": 0.01}

    def get_latest_tick(self, symbol: str) -> dict:
        return {"bid": 4000.0, "ask": 4000.2}


def sample_plan() -> dict:
    return {"symbol": "XAUUSD", "risk": {"lot_size": 0.02, "rr_to_tp3": 3.2}}


def passing_context() -> dict:
    return {
        "account": {"login": 123456, "server": "MetaQuotes-Demo", "account_mode": "demo"},
        "risk": {"permission": {"status": "ALLOWED", "trade_allowed": True}},
        "news": {"status": "CLEAR", "lock_active": False},
        "killzone": {"active_killzone": "new_york_open", "is_valid": True},
        "guardrail": {"status": "PASS", "reasons": []},
        "spread_points": 20,
        "symbol_info": {"volume_min": 0.01, "volume_max": 5.0, "volume_step": 0.01},
    }


def main() -> int:
    checker = ReadinessChecker(connector=FakeConnector())
    all_pass = checker.check(sample_plan(), context=passing_context(), execution_mode="assisted", manual_confirmation_required=True)
    news_blocked = checker.check(
        sample_plan(),
        context={**passing_context(), "news": {"status": "LOCKED", "lock_active": True, "reason": "News lock active"}},
        execution_mode="assisted",
        manual_confirmation_required=True,
    )
    spread_high = checker.check(sample_plan(), context={**passing_context(), "spread_points": 100}, execution_mode="assisted", manual_confirmation_required=True)
    wrong_mode = checker.check(sample_plan(), context=passing_context(), execution_mode="advisor", manual_confirmation_required=True)
    multiple = checker.check(
        {"symbol": "XAUUSD", "risk": {"lot_size": 0.0, "rr_to_tp3": 2.0}},
        context={**passing_context(), "risk": {"permission": {"status": "BLOCKED", "trade_allowed": False}}},
        execution_mode="advisor",
        manual_confirmation_required=False,
    )

    assert all_pass["ready"] is True
    assert news_blocked["ready"] is False
    assert spread_high["ready"] is False
    assert wrong_mode["ready"] is False
    assert multiple["checks_failed"] >= 4

    print(ReadinessChecker.format_report(all_pass))
    print("")
    print("READINESS CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
