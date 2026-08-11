from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.live_paper.demo_order_executor import DemoExecutionError, DemoOrderExecutor


class MockMT5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.open_positions: list = []

    def symbol_select(self, symbol, enabled):
        return True

    def symbol_info(self, symbol):
        return SimpleNamespace(
            trade_tick_value=1.0, trade_tick_size=1.0, volume_min=0.1, volume_step=0.1, volume_max=50.0
        )

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(ask=44120.5, bid=44119.5)

    def positions_get(self, symbol=None):
        return self.open_positions

    def order_send(self, request):
        self.requests.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=777, price=request["price"], comment="done")


class MockConnector:
    def __init__(self, server: str = "MetaQuotes-Demo") -> None:
        self.mt5 = MockMT5()
        self.server = server

    def get_account_info(self):
        return {"login": 110883498, "server": self.server, "equity": 3000.0, "balance": 3000.0}


class AllowingGovernor:
    def evaluate(self):
        return {"permission": {"trade_allowed": True, "block_reasons": []}}


class BlockingGovernor:
    def evaluate(self):
        return {"permission": {"trade_allowed": False, "block_reasons": ["Max trades per day hit"]}}


def position(symbol="US30", direction="bullish"):
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": 44120.0,
        "stop_loss": 44090.0,
        "take_profit": 44210.0,
    }


def make_executor(tmp_path: Path, *, server="MetaQuotes-Demo", governor=None) -> DemoOrderExecutor:
    return DemoOrderExecutor(
        MockConnector(server), governor or AllowingGovernor(), tmp_path / "KILL_SWITCH"
    )


def test_demo_gate_refuses_non_demo_servers(tmp_path: Path):
    executor = make_executor(tmp_path, server="FundedNext-Live3")
    with pytest.raises(DemoExecutionError):
        executor.verify_demo_account()
    # A mid-session account change must refuse safely, never crash the loop.
    result = executor.open_position(position())
    assert result["submitted"] is False
    assert "not a demo server" in result["reason"]
    assert executor.connector.mt5.requests == []


def test_open_submits_market_order_with_sl_tp_on_demo(tmp_path: Path):
    executor = make_executor(tmp_path)
    result = executor.open_position(position())

    assert result["submitted"] is True
    assert result["order_ticket"] == 777
    request = executor.connector.mt5.requests[0]
    assert request["sl"] == 44090.0
    assert request["tp"] == 44210.0
    assert request["magic"] == 22077
    # 0.5% of 3000 = 15 risk; stop 30 points at 1.0/unit -> 0.5 raw, step 0.1.
    assert request["volume"] == 0.5


def test_lot_size_floors_down_and_skips_below_minimum(tmp_path: Path):
    executor = make_executor(tmp_path)
    # Huge stop distance forces raw lot below the 0.1 minimum -> skip, never round up.
    assert executor.lot_size("US30", stop_distance=500.0, equity=3000.0) == 0.0
    # Normal case floors to the step.
    assert executor.lot_size("US30", stop_distance=29.0, equity=3000.0) == 0.5


def test_kill_switch_blocks_new_orders(tmp_path: Path):
    executor = make_executor(tmp_path)
    (tmp_path / "KILL_SWITCH").write_text("stop", encoding="utf-8")

    result = executor.open_position(position())
    assert result["submitted"] is False
    assert "kill switch" in result["reason"]
    assert executor.connector.mt5.requests == []


def test_risk_governor_block_prevents_order(tmp_path: Path):
    executor = make_executor(tmp_path, governor=BlockingGovernor())
    result = executor.open_position(position())

    assert result["submitted"] is False
    assert "Max trades per day hit" in result["reason"]
    assert executor.connector.mt5.requests == []


def test_timeout_close_targets_only_sentinel_positions(tmp_path: Path):
    executor = make_executor(tmp_path)
    mt5 = executor.connector.mt5
    mt5.open_positions = [
        SimpleNamespace(magic=22077, ticket=901, type=0, volume=0.5),
        SimpleNamespace(magic=11111, ticket=902, type=0, volume=1.0),  # Foreign position.
    ]

    result = executor.close_symbol_positions("US30")

    assert result["closed_tickets"] == [901]
    assert len(mt5.requests) == 1
    assert mt5.requests[0]["position"] == 901
    assert executor.position_still_open("US30") is True  # Mock list unchanged.
