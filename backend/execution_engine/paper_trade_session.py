"""Assisted paper-trade rehearsal for Project Sentinel."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from backend.alerts.alert_engine import AlertEngine
from backend.execution_engine.execution_engine import ExecutionEngine
from backend.execution_engine.position_manager import PositionManager
from backend.guardrails.strategy_guardrails import StrategyGuardrails
from backend.journal.journal_engine import JournalEngine


ApprovalCallback = Callable[[dict[str, Any]], bool]


class MockPaperMT5:
    """Small MT5 constant shim for paper-only order/request construction."""

    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def order_send(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return {"retcode": self.TRADE_RETCODE_DONE, "order": 900001, "comment": "paper mock"}

    def positions_get(self) -> list[dict[str, Any]]:
        return []

    def orders_get(self) -> list[dict[str, Any]]:
        return []


class PaperConnector:
    """Connector facade that satisfies readiness checks without touching MT5."""

    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.mt5 = MockPaperMT5()

    def is_initialized(self) -> bool:
        return self.connected

    def get_account_info(self) -> dict[str, Any]:
        return {
            "login": 123456,
            "server": "MetaQuotes-Demo",
            "account_mode": "demo",
            "balance": 2000.0,
            "equity": 2000.0,
            "currency": "USD",
        }

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        return {
            "volume_min": 0.01,
            "volume_max": 10.0,
            "volume_step": 0.01,
            "point": 0.01 if symbol.upper() != "US30" else 1.0,
            "trade_tick_value": 1.0,
            "trade_tick_size": 1.0,
        }

    def get_latest_tick(self, symbol: str) -> dict[str, Any]:
        return {"bid": 4010.0, "ask": 4010.2}


class PaperTradeSession:
    """Run a broker-safe assisted trade lifecycle drill."""

    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")
    SCENARIOS = {
        "A": [0.0, 1.0, 2.0, 3.2],
        "FULL_WIN": [0.0, 1.0, 2.0, 3.2],
        "B": [0.0, 1.0, 0.0],
        "BREAKEVEN": [0.0, 1.0, 0.0],
        "C": [0.0, -1.0],
        "STOP_LOSS": [0.0, -1.0],
    }

    def __init__(
        self,
        *,
        connector: PaperConnector | None = None,
        config_dir: str | Path | None = None,
        journal_engine: JournalEngine | None = None,
        alert_engine: AlertEngine | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or PaperConnector()
        self.journal_engine = journal_engine or JournalEngine(config_dir=self.config_dir, project_root=project_root)
        self.alert_engine = alert_engine or AlertEngine(config_dir=self.config_dir)
        self.execution_engine = ExecutionEngine(
            connector=self.connector,
            config_dir=self.config_dir,
            journal_engine=None,
        )
        self.position_manager = PositionManager(connector=self.connector, config_dir=self.config_dir)
        self.guardrails = StrategyGuardrails(config_dir=self.config_dir)
        self.telegram_messages: list[str] = []

    def run(
        self,
        *,
        scenario: str = "A",
        approval_callback: ApprovalCallback | None = None,
        send_telegram: bool = False,
        readiness_blocked: bool = False,
        approval_rejected: bool = False,
    ) -> dict[str, Any]:
        """Run one paper drill scenario and return the full session result."""
        scenario_key = self.normalize_scenario(scenario)
        if scenario_key == "READINESS_BLOCKED":
            readiness_blocked = True
            scenario_key = "A"
        if scenario_key == "APPROVAL_REJECTED":
            approval_rejected = True
            scenario_key = "A"

        session = self.build_session()
        trade_plan = self.build_trade_plan(session)
        confidence = self.build_confidence(trade_plan)
        guardrail = self.guardrails.evaluate(
            symbol=trade_plan["symbol"],
            total_confidence=confidence["total_confidence"],
            killzone=confidence["killzone"],
            smt=confidence["smt"],
            narrative_phase="expansion",
            risk_blocked=False,
            news_lock_active=False,
            mss_confirmed=True,
            rr_to_final=trade_plan["risk"]["rr_to_tp3"],
        )
        confidence["guardrail"] = guardrail
        confidence["guardrail_status"] = guardrail["status"]
        context = self.build_context(confidence=confidence, guardrail=guardrail, readiness_blocked=readiness_blocked)
        prepared = self.execution_engine.prepare_execution(trade_plan, context=context, mode="assisted")
        readiness = self.execution_engine.readiness_checker.check(
            trade_plan,
            context=context,
            execution_mode="assisted",
            manual_confirmation_required=True,
            connector=self.connector,
        )

        self.record_telegram("SIGNAL_DETECTED", session, send_telegram)
        if prepared["validation_status"] != "PASS" or not readiness["ready"]:
            return self.finish_blocked(
                session=session,
                trade_plan=trade_plan,
                confidence=confidence,
                prepared=prepared,
                readiness=readiness,
                approval_result="NOT_REQUESTED",
                reason="READINESS_BLOCKED",
            )

        approved = False if approval_rejected else self.request_approval(
            prepared,
            confidence=confidence,
            trade_plan=trade_plan,
            approval_callback=approval_callback,
        )
        if not approved:
            return self.finish_blocked(
                session=session,
                trade_plan=trade_plan,
                confidence=confidence,
                prepared=prepared,
                readiness=readiness,
                approval_result="NO",
                reason="APPROVAL_REJECTED",
            )

        mock_execution = self.mock_submit(prepared)
        session["status"] = "OPEN"
        session["approval_result"] = "YES"
        session["mock_execution"] = mock_execution
        self.record_telegram("ORDER_APPROVED", session, send_telegram)

        lifecycle = self.simulate_lifecycle(session, trade_plan, scenario_key, send_telegram=send_telegram)
        result = {
            "session": session,
            "signal": "DETECTED",
            "trade_plan": trade_plan,
            "confidence": confidence,
            "guardrail": guardrail,
            "prepared_execution": prepared,
            "readiness": readiness,
            "approval_result": "YES",
            "mock_execution": mock_execution,
            "lifecycle": lifecycle,
            "journal_recorded": self.record_journal(
                session=session,
                trade_plan=trade_plan,
                readiness=readiness,
                approval_result="YES",
                mock_execution=mock_execution,
            ),
            "telegram_messages": list(self.telegram_messages),
            "passed": session["status"] == "CLOSED",
        }
        result["terminal_output"] = self.format_terminal_result(result)
        return result

    def request_approval(
        self,
        prepared: dict[str, Any],
        *,
        confidence: dict[str, Any],
        trade_plan: dict[str, Any],
        approval_callback: ApprovalCallback | None,
    ) -> bool:
        """Ask for manual approval through callback or terminal prompt."""
        request = {
            **prepared,
            "confidence": confidence.get("total_confidence", 0),
            "rr": trade_plan.get("risk", {}).get("rr_to_tp3", 0.0),
        }
        if approval_callback:
            return bool(approval_callback(request))
        answer = input(self.format_paper_approval_prompt(request))
        return answer.strip().upper() == "Y"

    def simulate_lifecycle(
        self,
        session: dict[str, Any],
        trade_plan: dict[str, Any],
        scenario_key: str,
        *,
        send_telegram: bool,
    ) -> list[dict[str, Any]]:
        """Walk configured R multiples and record paper management actions."""
        lifecycle: list[dict[str, Any]] = []
        for current_r in self.SCENARIOS[scenario_key]:
            session["current_r"] = current_r
            position = self.position_from_session(session, trade_plan, current_r=current_r)
            result = self.position_manager.manage_position(
                position,
                context={"current_price": position["price_current"], "initial_stop_loss": session["initial_sl"]},
                mode="advisor",
            )
            actions = self.apply_lifecycle_actions(session, result.get("actions", []), current_r=current_r)
            event = {"current_r": current_r, "actions": actions, "manager_result": result}
            lifecycle.append(event)
            if current_r == 1.0:
                self.record_telegram("POSITION_1R", session, send_telegram)
            if current_r == 2.0:
                self.record_telegram("POSITION_2R", session, send_telegram)

        self.close_session(session, scenario_key)
        self.record_telegram("TRADE_CLOSED", session, send_telegram)
        lifecycle.append(
            {
                "current_r": session["current_r"],
                "actions": ["CLOSE_POSITION"],
                "outcome": session["outcome"],
                "realized_rr": session["realized_rr"],
            }
        )
        if "CLOSE_POSITION" not in session["actions_taken"]:
            session["actions_taken"].append("CLOSE_POSITION")
        return lifecycle

    def apply_lifecycle_actions(
        self,
        session: dict[str, Any],
        actions: list[dict[str, Any]],
        *,
        current_r: float,
    ) -> list[str]:
        """Mark recommended paper actions as applied once."""
        applied: list[str] = []
        for action in actions:
            action_type = str(action.get("type", ""))
            if not action_type or action_type in session["actions_taken"]:
                continue
            if action_type == "MOVE_SL_TO_BE":
                session["sl"] = session["entry"]
            if action_type == "PARTIAL_CLOSE":
                session["partial_closed"] = True
            session["actions_taken"].append(action_type)
            applied.append(action_type)
        if current_r >= 2.0 and "TRAIL_STRUCTURE" not in session["actions_taken"]:
            session["actions_taken"].append("TRAIL_STRUCTURE")
            applied.append("TRAIL_STRUCTURE")
        return applied

    def close_session(self, session: dict[str, Any], scenario_key: str) -> None:
        """Close paper position and set outcome metrics."""
        session["status"] = "CLOSED"
        if scenario_key in {"A", "FULL_WIN"}:
            session["outcome"] = "WIN"
            session["realized_rr"] = 3.2
        elif scenario_key in {"B", "BREAKEVEN"}:
            session["outcome"] = "BREAKEVEN"
            session["realized_rr"] = 0.0
        else:
            session["outcome"] = "LOSS"
            session["realized_rr"] = -1.0

    def finish_blocked(
        self,
        *,
        session: dict[str, Any],
        trade_plan: dict[str, Any],
        confidence: dict[str, Any],
        prepared: dict[str, Any],
        readiness: dict[str, Any],
        approval_result: str,
        reason: str,
    ) -> dict[str, Any]:
        """Return a blocked/cancelled paper session result."""
        session["status"] = "CANCELLED"
        session["outcome"] = reason
        session["approval_result"] = approval_result
        session["realized_rr"] = 0.0
        result = {
            "session": session,
            "signal": "DETECTED",
            "trade_plan": trade_plan,
            "confidence": confidence,
            "guardrail": confidence.get("guardrail", {}),
            "prepared_execution": prepared,
            "readiness": readiness,
            "approval_result": approval_result,
            "mock_execution": {"submitted": False, "result": reason},
            "lifecycle": [],
            "journal_recorded": self.record_journal(
                session=session,
                trade_plan=trade_plan,
                readiness=readiness,
                approval_result=approval_result,
                mock_execution={"submitted": False, "result": reason},
            ),
            "telegram_messages": list(self.telegram_messages),
            "passed": reason in {"READINESS_BLOCKED", "APPROVAL_REJECTED"},
        }
        result["terminal_output"] = self.format_terminal_result(result)
        return result

    def mock_submit(self, prepared: dict[str, Any]) -> dict[str, Any]:
        """Record a paper-only order submission."""
        request = dict(prepared.get("order_request", {}))
        return {
            "submitted": True,
            "result": "MOCK_SUBMITTED",
            "ticket": 900001,
            "order_request": request,
            "broker_message": "No broker order was submitted. Paper drill only.",
        }

    def record_journal(
        self,
        *,
        session: dict[str, Any],
        trade_plan: dict[str, Any],
        readiness: dict[str, Any],
        approval_result: str,
        mock_execution: dict[str, Any],
    ) -> bool:
        """Append the paper drill result to the local journal."""
        return self.journal_engine.append_record(
            {
                "type": "paper_trade_session",
                "timestamp": datetime.now(self.WAT_TIMEZONE).isoformat(),
                "session_id": session["session_id"],
                "symbol": session["symbol"],
                "trade_plan": trade_plan,
                "readiness_result": readiness,
                "approval_result": approval_result,
                "simulated_execution": mock_execution,
                "lifecycle_actions": session["actions_taken"],
                "final_outcome": session.get("outcome"),
                "realized_rr": session.get("realized_rr"),
            }
        )

    def record_telegram(self, event: str, session: dict[str, Any], send_telegram: bool) -> None:
        """Format and optionally send a paper lifecycle Telegram message."""
        message = AlertEngine.format_paper_lifecycle_message({"event": event, **session})
        self.telegram_messages.append(message)
        if send_telegram:
            self.alert_engine.send_telegram_alert(message)

    @classmethod
    def build_session(cls) -> dict[str, Any]:
        return {
            "session_id": f"paper-{uuid.uuid4().hex[:12]}",
            "symbol": "XAUUSD",
            "status": "SCANNING",
            "entry": 4010.0,
            "sl": 4028.0,
            "initial_sl": 4028.0,
            "tp1": 3992.0,
            "tp2": 3974.0,
            "tp3": 3952.0,
            "lot": 0.02,
            "current_r": 0.0,
            "actions_taken": [],
            "outcome": None,
            "realized_rr": None,
            "partial_closed": False,
        }

    @staticmethod
    def build_trade_plan(session: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": session["symbol"],
            "direction": "bearish",
            "confidence": 96,
            "execution_allowed": True,
            "entry": {"type": "limit", "price": session["entry"], "source": "OB_FVG_confluence"},
            "stop_loss": {"price": session["sl"], "distance": abs(session["entry"] - session["sl"]), "source": "liquidity_sweep"},
            "take_profit": {"tp1": session["tp1"], "tp2": session["tp2"], "tp3": session["tp3"]},
            "risk": {"lot_size": session["lot"], "rr_to_tp1": 1.0, "rr_to_tp2": 2.0, "rr_to_tp3": 3.2},
            "management": {
                "breakeven_at": "1R",
                "partial_profit_at": "2R",
                "partial_close_percent": 30,
                "trail_mode": "structure",
            },
            "plan_quality": "valid",
        }

    @staticmethod
    def build_confidence(trade_plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": trade_plan["symbol"],
            "direction": "bearish",
            "total_confidence": 96,
            "confidence_band": "EXECUTION_READY",
            "recommended_action": "Trade Allowed",
            "decision": "APPROVED",
            "rejection_reasons": [],
            "killzone": {"active_killzone": "new_york_open", "is_valid": True, "quality_score": 10},
            "smt": {"smt_detected": True, "available": True, "direction": "bearish", "pair_name": "XAUUSD/US30", "confidence": 5},
        }

    def build_context(
        self,
        *,
        confidence: dict[str, Any],
        guardrail: dict[str, Any],
        readiness_blocked: bool,
    ) -> dict[str, Any]:
        spread = 200 if readiness_blocked else 20
        return {
            "confidence": {
                **confidence,
                "guardrail_adjusted_confidence": guardrail.get("guardrail_adjusted_confidence", 96),
                "guardrail": guardrail,
            },
            "guardrail": guardrail,
            "risk": {"permission": {"status": "ALLOWED", "trade_allowed": True}},
            "news": {"status": "CLEAR", "lock_active": False},
            "killzone": confidence["killzone"],
            "account": self.connector.get_account_info(),
            "spread_points": spread,
            "max_spread_points": 80,
            "symbol_info": self.connector.get_symbol_info(str(confidence["symbol"])),
            "mt5_connected": True,
        }

    @staticmethod
    def position_from_session(
        session: dict[str, Any],
        trade_plan: dict[str, Any],
        *,
        current_r: float,
    ) -> dict[str, Any]:
        risk_distance = float(trade_plan["stop_loss"]["distance"])
        current_price = session["entry"] - (current_r * risk_distance)
        return {
            "ticket": 900001,
            "symbol": session["symbol"],
            "type": "SELL",
            "price_open": session["entry"],
            "price_current": round(current_price, 5),
            "sl": session["sl"],
            "initial_sl": session["initial_sl"],
            "tp": session["tp3"],
            "volume": session["lot"],
            "point": 0.01,
            "magic": 22001,
            "comment": "Project Sentinel paper drill",
            "sentinel_partial_closed": session.get("partial_closed", False),
        }

    @staticmethod
    def format_paper_approval_prompt(request: dict[str, Any]) -> str:
        return (
            "\nPROJECT SENTINEL PAPER EXECUTION REQUEST\n\n"
            f"Symbol: {request.get('symbol')}\n"
            f"Type: {request.get('order_type')}\n"
            f"Confidence: {request.get('confidence')}\n"
            f"RR: {request.get('rr')}\n\n"
            "Approve? [Y/N] "
        )

    @staticmethod
    def format_terminal_result(result: dict[str, Any]) -> str:
        session = result["session"]
        readiness = result["readiness"]
        approval = result["approval_result"]
        mock_execution = result["mock_execution"]
        lifecycle_lines = []
        for event in result.get("lifecycle", []):
            actions = event.get("actions", [])
            current_r = event.get("current_r")
            if "MOVE_SL_TO_BE" in actions:
                lifecycle_lines.append("1R reached -> BE moved")
            if "PARTIAL_CLOSE" in actions:
                lifecycle_lines.append("2R reached -> Partial closed")
            if "TRAIL_STRUCTURE" in actions:
                lifecycle_lines.append("2R reached -> Structure trail active")
            if "CLOSE_POSITION" in actions:
                if session.get("outcome") == "WIN":
                    lifecycle_lines.append("TP3 hit -> Closed")
                elif session.get("outcome") == "BREAKEVEN":
                    lifecycle_lines.append("BE stopout -> Closed")
                elif session.get("outcome") == "LOSS":
                    lifecycle_lines.append("SL hit -> Closed")
                else:
                    lifecycle_lines.append(f"{current_r}R -> Closed")

        lines = [
            "ASSISTED PAPER DRILL",
            "",
            f"Signal: {result.get('signal', 'DETECTED')}",
            "Plan: GENERATED",
            f"Guardrails: {result.get('guardrail', {}).get('status', 'PASS')}",
            f"Readiness: {'PASS' if readiness.get('ready') else 'BLOCKED'}",
            f"Approval: {approval}",
            f"Order: {mock_execution.get('result', 'NOT_SUBMITTED')}",
            f"Position: {session.get('status')}",
            "",
        ]
        lines.extend(lifecycle_lines or ["No lifecycle actions executed."])
        lines.extend(
            [
                "",
                "Outcome:",
                str(session.get("outcome")),
                f"Realized RR: {PaperTradeSession.format_rr(session.get('realized_rr'))}",
                "",
                "FINAL STATUS:",
                "PAPER DRILL PASSED" if result.get("passed") else "PAPER DRILL FAILED",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def format_rr(value: Any) -> str:
        if value is None:
            return "0"
        numeric = float(value)
        if numeric > 0:
            return f"+{numeric:g}"
        return f"{numeric:g}"

    @staticmethod
    def normalize_scenario(scenario: str) -> str:
        normalized = str(scenario or "A").upper().strip().replace("-", "_")
        aliases = {
            "SCENARIO_A": "A",
            "SCENARIO_B": "B",
            "SCENARIO_C": "C",
            "READINESS_BLOCK": "READINESS_BLOCKED",
            "BLOCKED": "READINESS_BLOCKED",
            "REJECTED": "APPROVAL_REJECTED",
        }
        normalized = aliases.get(normalized, normalized)
        valid = set(PaperTradeSession.SCENARIOS) | {"READINESS_BLOCKED", "APPROVAL_REJECTED"}
        if normalized not in valid:
            supported = ", ".join(sorted(valid))
            raise ValueError(f"Unsupported paper scenario '{scenario}'. Supported: {supported}.")
        return normalized
