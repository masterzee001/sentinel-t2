from __future__ import annotations

from collections import namedtuple

from backend.observer.btc_observer import BTCObserver


TickInfo = namedtuple("TickInfo", ["bid", "ask", "time"])


class FakeConnector:
    def get_latest_tick(self, symbol: str):
        assert symbol == "BTCUSD"
        return {"bid": 100750.0, "ask": 100760.0, "time": 1_800_000_000}

    def get_historical_candles(self, symbol: str, timeframe: str, count: int = 80):
        import pandas as pd

        assert symbol == "BTCUSD"
        return pd.DataFrame(
            {
                "open": [100000.0 + index * 10 for index in range(count)],
                "high": [100100.0 + index * 10 for index in range(count)],
                "low": [99900.0 + index * 10 for index in range(count)],
                "close": [100000.0 + index * 10 for index in range(count)],
            }
        )


class FakeKillzone:
    def analyze(self, symbol: str):
        return {
            "symbol": symbol,
            "active_killzone": "new_york_open",
            "is_valid": True,
            "quality_score": 10,
            "commentary": "New York observer window.",
        }


class FakeSMT:
    def analyze_for_symbol(self, symbol: str, active_killzone=None):
        return {
            "pair_name": "none",
            "primary": symbol,
            "comparison": "none",
            "timeframe": "M15",
            "smt_detected": False,
            "direction": None,
            "confidence": 0,
        }


def test_btc_observer_loads_and_blocks_execution():
    observer = BTCObserver(connector=FakeConnector(), killzone_analyzer=FakeKillzone(), smt_analyzer=FakeSMT())

    result = observer.observe()

    assert result["symbol"] == "BTCUSD"
    assert result["display_symbol"] == "BTCUSD (EXPERIMENTAL)"
    assert result["mode"] == "DEMO_SANDBOX"
    assert result["sandbox_mode"] is True
    assert result["available"] is True
    assert result["confidence"]["total_confidence"] > 0
    assert result["confidence"]["mode"] == "DEMO_SANDBOX"
    assert result["confidence"]["killzone"]["active_killzone"] == "new_york_open"
    assert result["trade_plan"]["plan_quality"] == "observer_only"
    assert result["trade_plan"]["execution_allowed"] is False
    assert result["execution_allowed"] is False
    assert BTCObserver.REJECTION_REASON in result["confidence"]["rejection_reasons"]
