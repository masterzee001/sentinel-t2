"""Manual smoke test for the Project Sentinel Position Manager."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.execution_engine.position_manager import PositionManager, PositionManagerError


class MockMT5:
    """Minimal MT5 mock for position management tests."""

    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def order_send(self, request: dict) -> dict:
        self.requests.append(request)
        return {"retcode": self.TRADE_RETCODE_DONE, "order": request.get("position") or request.get("order"), "comment": "Mock management accepted"}

    def positions_get(self) -> list[dict]:
        return []

    def orders_get(self) -> list[dict]:
        return []


class MockConnector:
    """Connector shim exposing mocked MT5."""

    def __init__(self) -> None:
        self.mt5 = MockMT5()


def base_position(price_current: float) -> dict:
    """Return a Sentinel BUY position."""
    return {
        "ticket": 123456,
        "symbol": "XAUUSD",
        "type": "BUY",
        "price_open": 4000.0,
        "price_current": price_current,
        "sl": 3980.0,
        "tp": 4060.0,
        "volume": 0.10,
        "point": 0.01,
        "magic": 22001,
        "comment": "Project Sentinel assisted order",
    }


def pending_order() -> dict:
    """Return a Sentinel pending BUY_LIMIT."""
    return {
        "ticket": 654321,
        "symbol": "XAUUSD",
        "type": "BUY_LIMIT",
        "price_open": 3990.0,
        "tp": 4060.0,
        "magic": 22001,
        "comment": "Project Sentinel assisted order",
    }


def print_result(label: str, result: dict) -> None:
    """Print compact management result."""
    print(label)
    print("-" * len(label))
    print(f"Symbol: {result.get('symbol')}")
    print(f"Ticket: {result.get('ticket')}")
    print(f"Status: {result.get('position_status')}")
    print(f"Current R: {result.get('current_r')}")
    print(f"Actions: {[action.get('type') for action in result.get('actions', [])]}")
    print(f"Submitted: {[action.get('result') for action in result.get('submitted_actions', [])]}")
    print("")


def main() -> int:
    logger.remove()
    manager = PositionManager(connector=MockConnector())

    try:
        below_1r = manager.manage_position(base_position(4010.0), mode="advisor")
        at_1r = manager.manage_position(base_position(4021.0), mode="advisor")
        at_2r = manager.manage_position(base_position(4041.0), mode="advisor")
        invalid_pending = manager.manage_pending_order(
            pending_order(),
            context={"news": {"lock_active": True}},
            mode="advisor",
        )
        advisor_noop = manager.manage_position(base_position(4021.0), mode="advisor")
        assisted_approved = manager.manage_position(
            base_position(4021.0),
            mode="assisted",
            confirmation_callback=lambda _subject, _action: True,
        )
    except PositionManagerError as exc:
        print(f"Position manager test failed: {exc}")
        return 1

    print("POSITION MANAGER TEST")
    print("=====================")
    print_result("Below 1R", below_1r)
    print_result("Reached 1R", at_1r)
    print_result("Reached 2R", at_2r)
    print_result("Pending Invalidated By News", invalid_pending)
    print_result("Advisor Mode No-Op", advisor_noop)
    print_result("Assisted Approved Mock Modification", assisted_approved)
    print("Mock MT5 only. No live position modification was placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
