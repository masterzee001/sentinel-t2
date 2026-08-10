from __future__ import annotations

from backend.execution_engine.execution_engine import ExecutionEngine


class MockMT5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.requests = []

    def order_send(self, request: dict) -> dict:
        self.requests.append(request)
        return {"retcode": self.TRADE_RETCODE_DONE, "order": 12345, "comment": "done"}


class MockConnector:
    def __init__(self) -> None:
        self.mt5 = MockMT5()

    def is_initialized(self) -> bool:
        return True

    def get_account_info(self) -> dict:
        return {"login": 123456, "server": "MetaQuotes-Demo", "account_mode": "demo", "balance": 2000.0, "equity": 2000.0}

    def get_symbol_info(self, symbol: str) -> dict:
        return {"volume_min": 0.01, "volume_max": 10.0, "volume_step": 0.01, "point": 0.01}

    def get_latest_tick(self, symbol: str) -> dict:
        return {"bid": 4000.0, "ask": 4000.2}


def make_engine() -> ExecutionEngine:
    return ExecutionEngine(connector=MockConnector())


def sample_plan(**overrides):
    plan = {
        "symbol": "US30",
        "direction": "bearish",
        "confidence": 96,
        "execution_allowed": True,
        "entry": {"type": "limit", "price": 4010.0, "source": "OB_FVG_confluence"},
        "stop_loss": {"price": 4028.0, "distance": 18.0, "source": "liquidity_sweep"},
        "take_profit": {"tp1": 3992.0, "tp2": 3974.0, "tp3": 3952.0},
        "risk": {"lot_size": 0.02, "rr_to_tp3": 3.22},
    }
    plan.update(overrides)
    return plan


def passing_context(**overrides):
    context = {
        "confidence": {
            "total_confidence": 96,
            "guardrail_adjusted_confidence": 96,
            "guardrail": {"status": "PASS"},
        },
        "risk": {"permission": {"trade_allowed": True}},
        "news": {"lock_active": False},
        "killzone": {"active_killzone": "new_york_open", "is_valid": True},
        "guardrail": {"status": "PASS", "reasons": []},
        "account": {"login": 123456, "server": "MetaQuotes-Demo", "account_mode": "demo"},
        "spread_points": 20,
        "max_spread_points": 50,
        "symbol_info": {"volume_min": 0.01, "volume_max": 10.0, "volume_step": 0.01},
    }
    context.update(overrides)
    return context


def test_order_type_mapping_limit_and_market():
    assert ExecutionEngine.determine_order_type(sample_plan(direction="bullish")) == "BUY_LIMIT"
    assert ExecutionEngine.determine_order_type(sample_plan(direction="bearish")) == "SELL_LIMIT"
    market_plan = sample_plan(
        direction="bullish",
        entry={"type": "market", "price": 4010.0, "source": "immediate_confirmation"},
    )
    assert ExecutionEngine.determine_order_type(market_plan) == "BUY"


def test_validation_checks_block_unsafe_plan():
    engine = make_engine()
    plan = sample_plan(execution_allowed=False, risk={"lot_size": 0.02, "rr_to_tp3": 2.2})
    context = passing_context(
        news={"lock_active": True},
        risk={"permission": {"trade_allowed": False}},
        confidence={"total_confidence": 84, "guardrail": {"status": "BLOCKED"}},
        guardrail={"status": "BLOCKED"},
        spread_points=80,
        max_spread_points=50,
    )

    reasons = engine.validate_safety(plan, context=context, order_type="SELL_LIMIT")

    assert "Trade plan execution is not allowed" in reasons
    assert "High impact news lock active" in reasons
    assert "Risk Governor blocked" in reasons
    assert "Strategy guardrail blocked" in reasons
    assert "Confidence below execution threshold" in reasons
    assert "RR below 3" in reasons
    assert "Spread too high" in reasons


def test_advisor_mode_never_submits_order():
    engine = make_engine()

    result = engine.execute(sample_plan(), context=passing_context(), mode="advisor")

    assert result["validation_status"] == "PASS"
    assert result["execution_allowed"] is False
    assert result["order_submitted"] is False
    assert result["order_result"] == "ADVISOR_MODE_NO_ORDER"


def test_assisted_mode_manual_rejection_blocks_submit():
    engine = make_engine()

    result = engine.execute(
        sample_plan(),
        context=passing_context(),
        mode="assisted",
        confirmation_callback=lambda _: False,
    )

    assert result["order_submitted"] is False
    assert result["order_result"] == "REJECTED_BY_USER"
    assert engine.connector.mt5.requests == []


def test_successful_mock_submit():
    engine = make_engine()

    result = engine.execute(
        sample_plan(),
        context=passing_context(),
        mode="assisted",
        confirmation_callback=lambda _: True,
    )

    assert result["execution_allowed"] is True
    assert result["readiness"]["ready"] is True
    assert result["order_submitted"] is True
    assert result["order_result"] == "SUCCESS"
    assert result["ticket"] == 12345
    assert engine.connector.mt5.requests[0]["symbol"] == "US30"


def test_eurusd_cannot_reach_submit_order():
    engine = make_engine()

    result = engine.execute(
        sample_plan(symbol="EURUSD"),
        context=passing_context(),
        mode="assisted",
        confirmation_callback=lambda _: True,
    )

    assert result["validation_status"] == "BLOCKED"
    assert result["order_result"] == "BLOCKED_OBSERVER_SYMBOL"
    assert result["order_submitted"] is False
    assert engine.connector.mt5.requests == []


def test_submit_order_blocks_observer_symbol_directly():
    engine = make_engine()

    result = engine.submit_order({"symbol": "BTCUSD"})

    assert result["order_result"] == "BLOCKED_OBSERVER_SYMBOL"
    assert result["order_submitted"] is False
    assert engine.connector.mt5.requests == []


def test_readiness_failure_blocks_submit():
    engine = make_engine()

    result = engine.execute(
        sample_plan(),
        context=passing_context(account={"login": 123456, "server": "Wrong-Demo", "account_mode": "demo"}),
        mode="assisted",
        confirmation_callback=lambda _: True,
    )

    assert result["order_submitted"] is False
    assert result["order_result"] == "BLOCKED_BY_READINESS"
    assert "Wrong-Demo" in result["broker_message"]
    assert engine.connector.mt5.requests == []


def test_order_request_uses_tp3_and_stop_loss():
    engine = make_engine()

    prepared = engine.prepare_execution(sample_plan(), context=passing_context(), mode="assisted")

    request = prepared["order_request"]
    assert prepared["order_type"] == "SELL_LIMIT"
    assert request["price"] == 4010.0
    assert request["sl"] == 4028.0
    assert request["tp"] == 3952.0
    assert request["volume"] == 0.02
