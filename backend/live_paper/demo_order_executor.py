"""Real order execution for the champion config — DEMO ACCOUNTS ONLY.

User-directed upgrade from paper to real order submission. Hard safety gates:

  1. DEMO GATE: refuses to start unless the connected account's server name
     contains 'demo'. Real-money accounts cannot execute through this path.
  2. RISK GATE: every open passes the RiskStateStore-backed Risk Governor
     (daily loss, drawdown, trade count, cooldown) — the Phase 0 limits.
  3. KILL SWITCH: creating data/live_paper/KILL_SWITCH blocks all new orders.
  4. Lot sizing floors DOWN to the broker step; too-small positions are
     skipped, never rounded up beyond the risk budget.

Every order carries SL and TP3 server-side; the 24-bar timeout is closed
actively by the executor.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from loguru import logger

MAGIC = 22077
COMMENT = "sentinel-champion"
DEVIATION_POINTS = 50


class DemoExecutionError(RuntimeError):
    """Raised when demo execution cannot proceed safely."""


class DemoOrderExecutor:
    """Submit champion trades to a DEMO MT5 account with hard gates."""

    def __init__(
        self,
        connector: Any,
        risk_governor: Any,
        kill_switch_path: str | Path,
        risk_percent: float = 0.5,
        magic: int = MAGIC,
        comment: str = COMMENT,
        max_lots_per_order: float = 5.0,
        min_lot_risk_cap_percent: float = 0.0,
        server_stop_loss: bool = True,
        max_tick_age_seconds: float = 0.0,
    ) -> None:
        self.connector = connector
        self.risk_governor = risk_governor
        self.kill_switch_path = Path(kill_switch_path)
        self.risk_percent = float(risk_percent)
        self.magic = int(magic)
        self.comment = str(comment)
        self.max_lots_per_order = float(max_lots_per_order)
        self.min_lot_risk_cap_percent = float(min_lot_risk_cap_percent)
        # False = size on the position's stop distance but do NOT place the
        # stop server-side (the audited mean-reversion book has no stop; its
        # forward test showed the 3-unit stop halves return — user-directed).
        self.server_stop_loss = bool(server_stop_loss)
        # >0: refuse orders when the freshest tick is older than this — MT5
        # keeps serving the LAST tick after a market closes (DE40 audit
        # finding), so "tick exists" does not mean "market open".
        self.max_tick_age_seconds = float(max_tick_age_seconds)
        self._count_failures = 0

    def _tick_age_seconds(self, tick: Any) -> float | None:
        """Age of a quote in real seconds, or None when it cannot be judged.

        MT5 encodes tick.time in BROKER SERVER time, so subtracting it from
        real UTC yields the broker's clock offset rather than an age — the
        first version of this guard did exactly that and could never fire
        (found 2026-08-11 during the Pepperstone migration). The broker's
        clock is measured, never assumed; if it is unknown the quote's age
        is genuinely unknowable and the check is skipped rather than
        guessed at.
        """
        tick_time = float(getattr(tick, "time", 0) or 0)
        if tick_time <= 0:
            return None
        offset_hours = getattr(self.connector, "_server_offset_hours", None)
        if offset_hours is None:
            measure = getattr(self.connector, "server_utc_offset_hours", None)
            offset_hours = measure() if measure else None
        if offset_hours is None:
            logger.debug("Broker clock offset unknown; skipping the stale-quote check.")
            return None
        broker_now = time.time() + float(offset_hours) * 3600.0
        return broker_now - tick_time

    def _broker_symbol(self, symbol: str) -> str:
        """Resolve the broker's tradable name (e.g. NAS100 -> USTEC)."""
        resolver = getattr(self.connector, "broker_symbol", None)
        if resolver is None:
            return symbol
        try:
            return resolver(symbol)
        except Exception:
            return symbol

    # ------------------------------------------------------------- gates
    def verify_demo_account(self) -> dict[str, Any]:
        """Return account info; raise unless it is unambiguously a demo account."""
        account = self.connector.get_account_info()
        server = str(account.get("server", ""))
        if "demo" not in server.lower():
            raise DemoExecutionError(
                f"Execution refused: account server '{server}' is not a demo server. "
                "Real-money execution is not enabled in this path."
            )
        return account

    def verify_autotrading_enabled(self) -> tuple[bool, str]:
        """Check the terminal's AutoTrading switch before the day's first order.

        It is a GUI toggle that defaults OFF; while it is off the broker
        refuses every order with retcode 10027 and the engine looks healthy
        the whole time. This turns that silent block into a loud one.
        """
        try:
            info = self.connector.mt5.terminal_info()
        except Exception as exc:  # noqa: BLE001
            return True, f"could not read terminal state ({exc})"
        if info is None:
            return True, "terminal info unavailable"
        if not getattr(info, "trade_allowed", True):
            return False, (
                "AutoTrading is DISABLED in the MT5 terminal - every order will be refused "
                "(retcode 10027). Enable the AutoTrading button (Ctrl+E)."
            )
        return True, ""

    def kill_switch_active(self) -> bool:
        return self.kill_switch_path.exists()

    def open_allowed(self) -> tuple[bool, str]:
        if self.kill_switch_active():
            return False, "kill switch file present"
        if self._count_failures >= 3:
            # Trade counting is part of the runaway-loop tripwire; if the
            # state file is persistently unwritable, fail closed.
            return False, "risk state store unwritable (trade counting disabled)"
        try:
            risk = self.risk_governor.evaluate()
        except Exception as exc:
            # A transient MT5 account-info failure must refuse THIS order,
            # not kill the engine process (fail closed, engine survives).
            logger.warning("Risk evaluation failed; refusing order: {}", exc)
            return False, f"risk evaluation failed: {exc}"
        permission = risk.get("permission", {})
        if not permission.get("trade_allowed", False):
            return False, "; ".join(permission.get("block_reasons", [])) or "risk governor blocked"
        return True, ""

    # ------------------------------------------------------------- sizing
    def lot_size(self, symbol: str, stop_distance: float, equity: float) -> float:
        """Risk-based lot size floored to broker constraints; 0.0 means skip."""
        info = self.connector.mt5.symbol_info(self._broker_symbol(symbol))
        if info is None or stop_distance <= 0:
            return 0.0
        tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
        tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
        volume_min = float(getattr(info, "volume_min", 0.01) or 0.01)
        volume_step = float(getattr(info, "volume_step", 0.01) or 0.01)
        volume_max = float(getattr(info, "volume_max", 100.0) or 100.0)
        if tick_value <= 0 or tick_size <= 0:
            return 0.0
        value_per_unit = tick_value / tick_size
        risk_amount = equity * (self.risk_percent / 100.0)
        raw_lot = risk_amount / (stop_distance * value_per_unit)
        stepped = int(raw_lot / volume_step) * volume_step
        if stepped < volume_min:
            # The broker minimum exceeds the risk budget. User mandate
            # (2026-08-11): signals must reach the account — accept the
            # minimum lot when its implied risk stays under the engine's
            # cap; only refuse when even the minimum would risk more than
            # the cap. Cap 0.0 keeps the strict floor-down behavior.
            if self.min_lot_risk_cap_percent > 0 and equity > 0:
                implied = volume_min * stop_distance * value_per_unit / equity * 100.0
                if implied <= self.min_lot_risk_cap_percent:
                    return round(min(volume_min, volume_max), 2)
            return 0.0
        return round(min(stepped, volume_max), 2)

    # ------------------------------------------------------------- orders
    def open_position(self, position: dict[str, Any]) -> dict[str, Any]:
        """Submit a market order with server-side SL/TP for a champion signal."""
        allowed, reason = self.open_allowed()
        if not allowed:
            return {"submitted": False, "reason": reason}
        try:
            account = self.verify_demo_account()
        except DemoExecutionError as exc:
            # Refuse quietly rather than crashing the trade loop: the account
            # is re-verified on every open, not just at startup.
            logger.warning(str(exc))
            return {"submitted": False, "reason": str(exc)}
        symbol = position["symbol"]
        trade_symbol = self._broker_symbol(symbol)
        mt5 = self.connector.mt5
        mt5.symbol_select(trade_symbol, True)
        tick = mt5.symbol_info_tick(trade_symbol)
        if tick is None:
            return {"submitted": False, "reason": "no tick data"}
        if self.max_tick_age_seconds > 0:
            tick_age = self._tick_age_seconds(tick)
            if tick_age is not None and tick_age > self.max_tick_age_seconds:
                logger.warning("Stale quote for {} ({}s old) — market likely closed; refusing order.", trade_symbol, int(tick_age))
                return {"submitted": False, "reason": f"stale quote ({int(tick_age)}s old) - market likely closed"}
        bullish = position["direction"] == "bullish"
        price = float(tick.ask if bullish else tick.bid)
        stop_distance = abs(position["entry"] - position["stop_loss"])
        # Sanity gates: a feed glitch shrinking the stop (or corrupt broker
        # tick metadata inflating the lot formula) must not produce a
        # position risking many times the budget. Both engines' real stops
        # are structural (hundreds of points); 0.05% of entry is far below
        # any legitimate stop.
        if stop_distance < float(position["entry"]) * 0.0005:
            logger.warning("Implausibly small stop for {}: {} — refusing order.", symbol, stop_distance)
            return {"submitted": False, "reason": f"implausibly small stop distance {stop_distance}"}
        lots = self.lot_size(symbol, stop_distance, float(account.get("equity", 0.0)))
        if lots <= 0:
            return {"submitted": False, "reason": "position below broker minimum for risk budget"}
        if lots > self.max_lots_per_order:
            logger.warning("Lot size {} exceeds sanity ceiling {} for {} — refusing order.", lots, self.max_lots_per_order, symbol)
            return {"submitted": False, "reason": f"lots {lots} exceed sanity ceiling {self.max_lots_per_order}"}
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": trade_symbol,
            "volume": lots,
            "type": mt5.ORDER_TYPE_BUY if bullish else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": float(position["stop_loss"]) if self.server_stop_loss else 0.0,
            "tp": float(position["take_profit"]),
            "deviation": DEVIATION_POINTS,
            "magic": self.magic,
            "comment": self.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        retcode = getattr(result, "retcode", None) if result is not None else None
        if retcode != mt5.TRADE_RETCODE_DONE:
            comment = getattr(result, "comment", "no result") if result is not None else "order_send returned None"
            logger.warning("Demo order rejected for {}: retcode={} {}", symbol, retcode, comment)
            return {"submitted": False, "reason": f"retcode={retcode} {comment}"}
        self._count_open()
        return {
            "submitted": True,
            "order_ticket": int(getattr(result, "order", 0) or 0),
            "fill_price": float(getattr(result, "price", price) or price),
            "lots": lots,
        }

    def _count_open(self) -> None:
        """Count a filled open in the risk state so the daily-trade tripwire is real.

        Planned risk is deliberately NOT reserved (0.0): nothing on the live
        path observes server-side SL/TP closes, so a reservation would never
        be released and would ratchet the portfolio limit shut over weeks.
        """
        store = getattr(self.risk_governor, "state_store", None)
        if store is None:
            return
        try:
            store.record_trade_opened(planned_risk_percent=0.0)
            self._count_failures = 0
        except Exception as exc:  # Counting must never kill a fill we already made —
            # but 3 straight failures fail the executor closed (see open_allowed).
            self._count_failures += 1
            logger.warning("Could not record opened trade in risk state ({} consecutive): {}", self._count_failures, exc)

    def close_symbol_positions(self, symbol: str) -> dict[str, Any]:
        """Close any open sentinel-champion position on a symbol (timeout exit)."""
        trade_symbol = self._broker_symbol(symbol)
        mt5 = self.connector.mt5
        positions = mt5.positions_get(symbol=trade_symbol) or []
        closed = []
        for open_position in positions:
            if int(getattr(open_position, "magic", 0)) != self.magic:
                continue
            tick = mt5.symbol_info_tick(trade_symbol)
            if tick is None:
                continue
            is_long = int(open_position.type) == mt5.ORDER_TYPE_BUY
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": trade_symbol,
                "volume": float(open_position.volume),
                "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
                "position": int(open_position.ticket),
                "price": float(tick.bid if is_long else tick.ask),
                "deviation": DEVIATION_POINTS,
                "magic": self.magic,
                "comment": f"{self.comment}-timeout",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is not None and getattr(result, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                closed.append(int(open_position.ticket))
        return {"closed_tickets": closed}

    def position_still_open(self, symbol: str) -> bool:
        """Return whether a sentinel-champion position remains open on the symbol."""
        positions = self.connector.mt5.positions_get(symbol=self._broker_symbol(symbol)) or []
        return any(int(getattr(item, "magic", 0)) == self.magic for item in positions)
