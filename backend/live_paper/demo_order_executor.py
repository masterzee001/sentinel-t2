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

    def __init__(self, connector: Any, risk_governor: Any, kill_switch_path: str | Path, risk_percent: float = 0.5) -> None:
        self.connector = connector
        self.risk_governor = risk_governor
        self.kill_switch_path = Path(kill_switch_path)
        self.risk_percent = float(risk_percent)

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

    def kill_switch_active(self) -> bool:
        return self.kill_switch_path.exists()

    def open_allowed(self) -> tuple[bool, str]:
        if self.kill_switch_active():
            return False, "kill switch file present"
        risk = self.risk_governor.evaluate()
        permission = risk.get("permission", {})
        if not permission.get("trade_allowed", False):
            return False, "; ".join(permission.get("block_reasons", [])) or "risk governor blocked"
        return True, ""

    # ------------------------------------------------------------- sizing
    def lot_size(self, symbol: str, stop_distance: float, equity: float) -> float:
        """Risk-based lot size floored to broker constraints; 0.0 means skip."""
        info = self.connector.mt5.symbol_info(symbol)
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
            return 0.0  # Never round UP past the risk budget (Phase 0 rule).
        return round(min(stepped, volume_max), 2)

    # ------------------------------------------------------------- orders
    def open_position(self, position: dict[str, Any]) -> dict[str, Any]:
        """Submit a market order with server-side SL/TP for a champion signal."""
        allowed, reason = self.open_allowed()
        if not allowed:
            return {"submitted": False, "reason": reason}
        account = self.verify_demo_account()
        symbol = position["symbol"]
        mt5 = self.connector.mt5
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"submitted": False, "reason": "no tick data"}
        bullish = position["direction"] == "bullish"
        price = float(tick.ask if bullish else tick.bid)
        stop_distance = abs(position["entry"] - position["stop_loss"])
        lots = self.lot_size(symbol, stop_distance, float(account.get("equity", 0.0)))
        if lots <= 0:
            return {"submitted": False, "reason": "position below broker minimum for risk budget"}
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": mt5.ORDER_TYPE_BUY if bullish else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": float(position["stop_loss"]),
            "tp": float(position["take_profit"]),
            "deviation": DEVIATION_POINTS,
            "magic": MAGIC,
            "comment": COMMENT,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        retcode = getattr(result, "retcode", None) if result is not None else None
        if retcode != mt5.TRADE_RETCODE_DONE:
            comment = getattr(result, "comment", "no result") if result is not None else "order_send returned None"
            logger.warning("Demo order rejected for {}: retcode={} {}", symbol, retcode, comment)
            return {"submitted": False, "reason": f"retcode={retcode} {comment}"}
        return {
            "submitted": True,
            "order_ticket": int(getattr(result, "order", 0) or 0),
            "fill_price": float(getattr(result, "price", price) or price),
            "lots": lots,
        }

    def close_symbol_positions(self, symbol: str) -> dict[str, Any]:
        """Close any open sentinel-champion position on a symbol (timeout exit)."""
        mt5 = self.connector.mt5
        positions = mt5.positions_get(symbol=symbol) or []
        closed = []
        for open_position in positions:
            if int(getattr(open_position, "magic", 0)) != MAGIC:
                continue
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                continue
            is_long = int(open_position.type) == mt5.ORDER_TYPE_BUY
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(open_position.volume),
                "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
                "position": int(open_position.ticket),
                "price": float(tick.bid if is_long else tick.ask),
                "deviation": DEVIATION_POINTS,
                "magic": MAGIC,
                "comment": f"{COMMENT}-timeout",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is not None and getattr(result, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                closed.append(int(open_position.ticket))
        return {"closed_tickets": closed}

    def position_still_open(self, symbol: str) -> bool:
        """Return whether a sentinel-champion position remains open on the symbol."""
        positions = self.connector.mt5.positions_get(symbol=symbol) or []
        return any(int(getattr(item, "magic", 0)) == MAGIC for item in positions)
