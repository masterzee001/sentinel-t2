"""The book must score itself on fills, not on the prices it asked for.

Found live 2026-08-12. The engine recorded the daily-close signal price as the
trade's entry and never used the fill it had already captured:

  DE40   signalled 26350.7   filled 26372.6   0.124R given away, unrecorded
  US30   signalled 53833.8   filled 53799.0   0.088R gained, unrecorded

The direction varies but the bias does not: the recorded entry is the bar's
CLOSE while a long fills at the ASK, so the spread is systematically excluded
from every result. That matters more here than in most books, because this one
exists to test whether an audited edge survives contact with a real account -
it publishes a replay_expectation to be measured against. Scoring the backtest
entry instead of the account fill makes it blind to exactly what it was built
to detect.

Both legs are pinned: the entry must re-anchor to the fill, and the exit must
be priced from the close fill rather than the signal that triggered it.
"""

from __future__ import annotations

import importlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SRC = (PROJECT_ROOT / "scripts" / "run_mean_reversion_live.py").read_text(encoding="utf-8")
executor_mod = importlib.import_module("backend.live_paper.demo_order_executor")


class _Result:
    def __init__(self, price: float, retcode: int) -> None:
        self.price = price
        self.retcode = retcode


class _Position:
    def __init__(self, ticket: int, volume: float, magic: int) -> None:
        self.ticket = ticket
        self.volume = volume
        self.magic = magic
        self.type = 0  # buy


class _MT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, ORDER_TIME_GTC, ORDER_FILLING_IOC = 1, 0, 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self, positions, fills):
        self._positions = positions
        self._fills = list(fills)

    def positions_get(self, symbol=None):
        return self._positions

    def symbol_info_tick(self, symbol):
        class T:
            bid, ask = 100.0, 100.5
        return T()

    def order_send(self, request):
        return _Result(self._fills.pop(0), self.TRADE_RETCODE_DONE)


def _executor(positions, fills, magic=22078):
    ex = executor_mod.DemoOrderExecutor.__new__(executor_mod.DemoOrderExecutor)
    ex.connector = type("C", (), {"mt5": _MT5(positions, fills)})()
    ex.magic = magic
    ex.comment = "test"
    ex._broker_symbol = lambda s: s
    return ex


def test_close_reports_the_price_the_account_actually_got():
    ex = _executor([_Position(1, 1.0, 22078)], [26372.6])
    out = ex.close_symbol_positions("DE40")
    assert out["closed_tickets"] == [1]
    assert out["fill_price"] == 26372.6


def test_a_partly_closed_position_reports_one_volume_weighted_price():
    """Two fills of different size must not average naively."""
    ex = _executor([_Position(1, 3.0, 22078), _Position(2, 1.0, 22078)], [100.0, 200.0])
    out = ex.close_symbol_positions("DE40")
    assert out["fill_price"] == (100.0 * 3.0 + 200.0 * 1.0) / 4.0


def test_close_with_nothing_open_reports_no_price_rather_than_zero():
    """A zero would be scored as a catastrophic exit; None means 'no fill'."""
    ex = _executor([], [])
    out = ex.close_symbol_positions("DE40")
    assert out["fill_price"] is None
    assert out["closed_tickets"] == []


def test_positions_from_another_engine_are_left_alone():
    ex = _executor([_Position(9, 1.0, 11111)], [])
    out = ex.close_symbol_positions("DE40")
    assert out["closed_tickets"] == []


def test_engine_reanchors_the_entry_to_the_fill():
    assert 'position["entry"] = float(fill)' in ENGINE_SRC
    assert '"signal_price": price' in ENGINE_SRC
    assert '"entry_slippage"' in ENGINE_SRC


def test_engine_scores_the_exit_from_the_close_fill():
    """rr must be computed from exit_price, and exit_price must prefer the
    fill. The old line divided the SIGNAL price by the risk unit."""
    assert 'rr = (exit_price - float(position["entry"])) / float(position["risk_unit"])' in ENGINE_SRC
    assert 'exit_price = float(demo_close["fill_price"])' in ENGINE_SRC
    assert 'rr = (price - float(position["entry"]))' not in ENGINE_SRC


def test_engine_closes_before_it_scores():
    """The close must be executed before rr is computed, or there is no fill
    to compute it from."""
    close_at = ENGINE_SRC.index("demo_close = executor.close_symbol_positions(symbol)")
    score_at = ENGINE_SRC.index('rr = (exit_price - float(position["entry"]))')
    assert close_at < score_at


def test_operator_is_told_the_fill_not_the_signal():
    assert "MEANREV OPEN {symbol} long @ {position['entry']}" in ENGINE_SRC
