from __future__ import annotations

from backend.execution_engine.position_manager import PositionManager


class MockMT5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.requests = []

    def order_send(self, request: dict) -> dict:
        self.requests.append(request)
        return {"retcode": self.TRADE_RETCODE_DONE, "order": request.get("position") or request.get("order"), "comment": "done"}

    def positions_get(self):
        return []

    def orders_get(self):
        return []


class MockConnector:
    def __init__(self) -> None:
        self.mt5 = MockMT5()


def make_manager() -> PositionManager:
    return PositionManager(connector=MockConnector())


def position(**overrides):
    value = {
        "ticket": 123456,
        "symbol": "XAUUSD",
        "type": "BUY",
        "price_open": 4000.0,
        "price_current": 4020.0,
        "sl": 3980.0,
        "tp": 4060.0,
        "volume": 0.10,
        "point": 0.01,
        "magic": 22001,
        "comment": "Project Sentinel assisted order",
    }
    value.update(overrides)
    return value


def order(**overrides):
    value = {
        "ticket": 654321,
        "symbol": "XAUUSD",
        "type": "BUY_LIMIT",
        "price_open": 3990.0,
        "price_current": 4000.0,
        "tp": 4060.0,
        "magic": 22001,
        "comment": "Project Sentinel assisted order",
    }
    value.update(overrides)
    return value


def test_r_calculation_buy_and_sell():
    manager = make_manager()

    assert manager.calculate_current_r(position(price_current=4020.0)) == 1.0
    sell_position = position(type="SELL", price_open=4000.0, sl=4020.0, price_current=3960.0)
    assert manager.calculate_current_r(sell_position) == 2.0


def test_breakeven_trigger_recommends_move():
    manager = make_manager()

    result = manager.manage_position(position(price_current=4021.0), mode="advisor")

    assert result["current_r"] == 1.05
    assert result["actions"][0]["type"] == "MOVE_SL_TO_BE"
    assert result["actions"][0]["request"]["sl"] == 4000.2
    assert result["submitted_actions"] == []


def test_position_below_1r_has_no_action():
    manager = make_manager()

    result = manager.manage_position(position(price_current=4010.0), mode="advisor")

    assert result["current_r"] == 0.5
    assert result["actions"] == []


def test_partial_profit_trigger():
    manager = make_manager()

    result = manager.manage_position(position(price_current=4040.0), mode="advisor")
    action_types = [action["type"] for action in result["actions"]]

    assert result["current_r"] == 2.0
    assert "PARTIAL_CLOSE" in action_types
    partial = next(action for action in result["actions"] if action["type"] == "PARTIAL_CLOSE")
    assert partial["request"]["volume"] == 0.03


def test_pending_invalidation_by_news_lock():
    manager = make_manager()

    result = manager.manage_pending_order(order(), context={"news": {"lock_active": True}}, mode="advisor")

    assert result["position_status"] == "PENDING"
    assert result["actions"][0]["type"] == "CANCEL_PENDING_ORDER"
    assert result["actions"][0]["reason"] == "News lock became active"
    assert result["submitted_actions"] == []


def test_advisor_mode_no_modification_even_when_action_recommended():
    manager = make_manager()

    result = manager.manage_position(position(price_current=4021.0), mode="advisor")

    assert result["actions"]
    assert result["submitted_actions"] == []
    assert manager.connector.mt5.requests == []


def test_assisted_confirmation_submits_mock_modification():
    manager = make_manager()

    result = manager.manage_position(
        position(price_current=4021.0),
        mode="assisted",
        confirmation_callback=lambda _subject, action: action["type"] == "MOVE_SL_TO_BE",
    )

    assert result["submitted_actions"][0]["type"] == "MOVE_SL_TO_BE"
    assert result["submitted_actions"][0]["submitted"] is True
    assert result["submitted_actions"][0]["result"] == "SUCCESS"
    assert manager.connector.mt5.requests[0]["action"] == MockMT5.TRADE_ACTION_SLTP


def test_gbpusd_cannot_reach_position_manager_order_send():
    manager = make_manager()
    request = manager.build_move_sl_request(position(symbol="GBPUSD", price_current=4021.0))

    result = manager.submit_mt5_request(request)

    assert result["submitted"] is False
    assert result["result"] == "BLOCKED_OBSERVER_SYMBOL"
    assert manager.connector.mt5.requests == []


def test_non_sentinel_management_request_is_blocked():
    manager = make_manager()
    request = manager.build_move_sl_request(position(price_current=4021.0))
    request["magic"] = 999

    result = manager.submit_mt5_request(request)

    assert result["submitted"] is False
    assert result["result"] == "BLOCKED_OBSERVER_SYMBOL"
    assert "Sentinel magic" in result["broker_message"]
    assert manager.connector.mt5.requests == []


def test_assisted_confirmation_rejection_does_not_submit():
    manager = make_manager()

    result = manager.manage_position(
        position(price_current=4021.0),
        mode="assisted",
        confirmation_callback=lambda _subject, _action: False,
    )

    assert result["submitted_actions"][0]["result"] == "REJECTED_BY_USER"
    assert manager.connector.mt5.requests == []
