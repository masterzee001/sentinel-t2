from __future__ import annotations

import pandas as pd

from backend.observer.nas100_observer import NAS100Observer


class FakeConnector:
    def get_latest_tick(self, symbol: str) -> dict:
        assert symbol == "NAS100"
        return {"bid": 18050.0}

    def get_historical_candles(self, symbol: str, timeframe: str, count: int = 80):
        assert symbol == "NAS100"
        return pd.DataFrame(
            {
                "close": [18000.0, 18030.0, 18050.0],
                "high": [18010.0, 18040.0, 18060.0],
                "low": [17990.0, 18020.0, 18040.0],
            }
        )


class FakeKillzone:
    def analyze(self, symbol: str):
        return {"symbol": symbol, "active_killzone": "new_york_open", "is_valid": True, "quality_score": 10}


def test_nas100_observer_blocks_execution():
    observer = NAS100Observer(connector=FakeConnector(), killzone_analyzer=FakeKillzone())

    result = observer.observe()

    assert result["symbol"] == "NAS100"
    assert result["display_symbol"] == "NAS100 (OBSERVER)"
    assert result["mode"] == "DEMO_SANDBOX"
    assert result["sandbox_mode"] is True
    assert result["available"] is True
    assert result["trade_plan"]["plan_quality"] == "observer_only"
    assert result["trade_plan"]["execution_allowed"] is False
    assert result["confidence"]["guardrail_status"] == "BLOCKED"
    assert result["confidence"]["mode"] == "DEMO_SANDBOX"
    assert result["confidence"]["rejection_reasons"] == ["NAS100 demo sandbox: production execution disabled"]
