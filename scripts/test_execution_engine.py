"""Manual smoke test for the Project Sentinel Execution Engine."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.execution_engine.execution_engine import ExecutionEngine, ExecutionEngineError


class MockMT5:
    """Minimal MT5 mock for execution smoke tests."""

    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def order_send(self, request: dict) -> dict:
        return {
            "retcode": self.TRADE_RETCODE_DONE,
            "order": 220011,
            "comment": "Mock order accepted",
            "request": request,
        }


class MockConnector:
    """Connector shim exposing only the MT5 module needed by ExecutionEngine."""

    def __init__(self) -> None:
        self.mt5 = MockMT5()


def sample_trade_plan() -> dict:
    """Return an approved sample trade plan."""
    return {
        "symbol": "XAUUSD",
        "direction": "bearish",
        "confidence": 96,
        "execution_allowed": True,
        "entry": {"type": "limit", "price": 4010.0, "source": "OB_FVG_confluence"},
        "stop_loss": {"price": 4028.0, "distance": 18.0, "source": "liquidity_sweep"},
        "take_profit": {"tp1": 3992.0, "tp2": 3974.0, "tp3": 3952.0},
        "risk": {"lot_size": 0.02, "rr_to_tp3": 3.22},
    }


def sample_context() -> dict:
    """Return passing execution context."""
    return {
        "confidence": {
            "total_confidence": 96,
            "guardrail_adjusted_confidence": 96,
            "guardrail": {"status": "PASS"},
        },
        "risk": {"permission": {"trade_allowed": True}},
        "news": {"lock_active": False},
        "spread_points": 20,
        "max_spread_points": 50,
    }


def print_case(label: str, result: dict) -> None:
    """Print compact execution test output."""
    print(label)
    print("-" * len(label))
    print(f"Mode: {result.get('execution_mode')}")
    print(f"Order Type: {result.get('order_type')}")
    print(f"Execution Allowed: {result.get('execution_allowed')}")
    print(f"Validation: {result.get('validation_status')}")
    print(f"Submitted: {result.get('order_submitted')}")
    print(f"Result: {result.get('order_result')}")
    print(f"Ticket: {result.get('ticket')}")
    print(f"Reasons: {result.get('validation_reasons')}")
    print("")


def main() -> int:
    logger.remove()
    engine = ExecutionEngine(connector=MockConnector())
    plan = sample_trade_plan()
    context = sample_context()

    try:
        advisor = engine.execute(plan, context=context, mode="advisor")
        assisted_approved = engine.execute(
            plan,
            context=context,
            mode="assisted",
            confirmation_callback=lambda _: True,
        )
        assisted_rejected = engine.execute(
            plan,
            context=context,
            mode="assisted",
            confirmation_callback=lambda _: False,
        )
        blocked_context = {
            **context,
            "news": {"lock_active": True},
            "risk": {"permission": {"trade_allowed": False}},
        }
        blocked = engine.execute(plan, context=blocked_context, mode="assisted", confirmation_callback=lambda _: True)
    except ExecutionEngineError as exc:
        print(f"Execution engine test failed: {exc}")
        return 1

    print("EXECUTION ENGINE TEST")
    print("=====================")
    print_case("Advisor Mode", advisor)
    print_case("Assisted Approved", assisted_approved)
    print_case("Assisted Rejected", assisted_rejected)
    print_case("Blocked By Safety", blocked)
    print("Assisted only. No live MT5 order was placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
