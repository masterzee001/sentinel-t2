from __future__ import annotations

from collections import namedtuple

import pytest
from pandas.api.types import is_datetime64_any_dtype

import backend.market_data.mt5_connector as mt5_connector_module
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError


AccountInfo = namedtuple("AccountInfo", ["login", "balance", "equity"])
SymbolInfo = namedtuple("SymbolInfo", ["name", "visible"])
TickInfo = namedtuple("TickInfo", ["bid", "ask", "time"])


class FakeMT5:
    TIMEFRAME_M15 = 15

    def __init__(self) -> None:
        self.initialized = False
        self.shutdown_called = False
        self.selected_symbols: list[str] = []

    def initialize(self, **kwargs):
        self.initialized = True
        self.credentials = kwargs
        return True

    def shutdown(self):
        self.shutdown_called = True
        self.initialized = False

    def terminal_info(self):
        return {"name": "Fake MT5"} if self.initialized else None

    def account_info(self):
        return AccountInfo(login=123456, balance=100000.0, equity=100250.0)

    def symbol_select(self, symbol, enable):
        self.selected_symbols.append(symbol)
        return enable

    def symbol_info(self, symbol):
        return SymbolInfo(name=symbol, visible=True)

    def symbol_info_tick(self, symbol):
        return TickInfo(bid=2350.10, ask=2350.30, time=1_700_000_000)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return [
            {
                "time": 1_700_000_000 + index * 900,
                "open": 2350.0 + index,
                "high": 2351.0 + index,
                "low": 2349.0 + index,
                "close": 2350.5 + index,
                "tick_volume": 100 + index,
                "spread": 10,
                "real_volume": 0,
            }
            for index in range(count)
        ]

    def last_error(self):
        return (0, "ok")


class AliasMT5(FakeMT5):
    def symbol_select(self, symbol, enable):
        self.selected_symbols.append(symbol)
        return symbol == "BTC"


def test_connect_and_shutdown_without_live_mt5(monkeypatch):
    fake_mt5 = FakeMT5()
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    monkeypatch.delenv("MT5_PASSWORD", raising=False)
    monkeypatch.delenv("MT5_SERVER", raising=False)

    connector = MT5Connector(mt5_module=fake_mt5)

    assert connector.connect() is True
    assert connector.is_initialized() is True

    connector.shutdown()

    assert fake_mt5.shutdown_called is True
    assert connector.is_initialized() is False


def test_get_latest_tick_validates_supported_symbols():
    connector = MT5Connector(mt5_module=FakeMT5())
    connector.connect()

    with pytest.raises(ValueError, match="Unsupported symbol"):
        connector.get_latest_tick("ETHUSD")

    connector.shutdown()


def test_btcusd_is_supported_for_observer_mode():
    connector = MT5Connector(mt5_module=FakeMT5())
    connector.connect()

    tick = connector.get_latest_tick("BTCUSD")

    assert tick["bid"] == 2350.10
    assert "BTCUSD" in connector.mt5.selected_symbols
    connector.shutdown()


def test_symbol_alias_fallback_selects_broker_symbol():
    connector = MT5Connector(mt5_module=AliasMT5())
    connector.symbol_aliases = {"BTCUSD": ["BTC"]}
    connector.connect()

    tick = connector.get_latest_tick("BTCUSD")

    assert tick["bid"] == 2350.10
    assert connector.mt5.selected_symbols[:2] == ["BTCUSD", "BTC"]
    connector.shutdown()


def test_get_historical_candles_returns_dataframe():
    connector = MT5Connector(mt5_module=FakeMT5())
    connector.connect()

    candles = connector.get_historical_candles("XAUUSD", "M15", count=3)

    assert list(candles.columns) == [
        "time",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    ]
    assert len(candles) == 3
    assert is_datetime64_any_dtype(candles["time"])
    assert candles["time"].dt.tz is not None

    connector.shutdown()


def test_requires_mt5_package_when_missing(monkeypatch):
    monkeypatch.setattr(mt5_connector_module, "mt5", None)
    connector = MT5Connector()

    with pytest.raises(MT5ConnectorError, match="MetaTrader5 package is not available"):
        connector.connect()
