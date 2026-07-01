"""Telegram polling command bot for Project Sentinel Advisor Mode."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from loguru import logger

from backend.ai_coach.coach_analyzer import AICoachAnalyzer
from backend.analytics.monte_carlo_engine import MonteCarloEngine, MonteCarloEngineError
from backend.backtesting.report_cache import load_backtest_summary as load_cached_backtest_summary
from backend.backtesting.report_cache import normalize_backtest_summary, short_phase_decision
from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer
from backend.display.confidence_display import DEMO_SANDBOX_LABEL, OBSERVER_ONLY_LABEL, confidence_display_fields
from backend.demo_sandbox.demo_sandbox_engine import DemoSandboxEngine, sandbox_banner
from backend.execution_engine.assisted_execution_bridge import AssistedExecutionBridge, LockedTradeTicket, parse_datetime
from backend.execution_engine.readiness_checker import ReadinessChecker
from backend.ict_engine.ict_analyzer import ICTAnalyzer
from backend.journal.journal_engine import JournalEngine
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.live_data.live_data_collector import LiveDataCollector
from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer
from backend.market_data.mt5_connector import MT5Connector
from backend.news_filter.news_filter import NewsFilter
from backend.execution_engine.position_manager import PositionManager
from backend.observer.nas100_observer import NAS100Observer
from backend.observer.btc_observer import BTCObserver
from backend.risk_manager.risk_governor import RiskGovernor
from backend.shared.confidence_band_registry import observer_display_state, observer_state
from backend.smt_engine.smt_analyzer import SMTAnalyzer
from backend.symbols.symbol_registry import SymbolRegistry
from backend.trade_planner.trade_planner import TradePlanner
from backend.trend_engine.trend_analyzer import TrendAnalyzer


class TelegramCommandBotError(RuntimeError):
    """Raised when Telegram command bot configuration fails."""


SnapshotProvider = Callable[[], dict[str, Any]]


class TelegramCommandBot:
    """Lightweight mobile command center using Telegram getUpdates polling."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "polling_interval_seconds": 5,
        "allowed_commands": [
            "/start",
            "/help",
            "/status",
            "/summary",
            "/xauusd",
            "/us30",
            "/eurusd",
            "/gbpusd",
            "/btcusd",
            "/nas100",
            "/symbols",
            "/risk",
            "/news",
            "/coach",
            "/ping",
            "/positions",
            "/plans",
            "/journal",
            "/backtest",
            "/live_stats",
            "/stress",
            "/readiness",
            "/settings",
            "/validation",
            "/market_watch",
            "/paper_status",
            "/paper_trades",
            "/paper_stats",
            "/live_health",
            "/live_signals",
            "/live_mode",
            "/live_limits",
            "/live_killswitch",
            "/approve_trade",
            "/reject_trade",
            "/halt_live",
            "/resume_live",
            "/challenge_status",
            "/challenge_progress",
            "/challenge_risk",
            "/challenge_phase",
            "/challenge_governor",
            "/challenge_recommendation",
            "/activate_challenge_mode",
            "/deactivate_challenge_mode",
            "/assisted_status",
            "/assisted_ticket",
            "/assisted_approve",
            "/assisted_reject",
            "/assisted_dry_run",
            "/exec_approve",
            "/execute_approve",
            "/sandbox_status",
            "/sandbox_symbols",
            "/sandbox_ticket",
            "/sandbox_dry_run",
            "/sandbox_approve",
            "/sandbox_disable",
        ],
        "symbols": {
            "XAUUSD": "XAUUSD",
            "US30": "US30",
            "EURUSD": "EURUSD",
            "GBPUSD": "GBPUSD",
            "BTCUSD": "BTCUSD",
            "NAS100": "NAS100",
        },
        "advisor_mode_only": True,
        "telegram_settings": {
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        "max_message_chars": 3800,
        "backtest_summary_paths": [
            "data/reports/backtest_365d_summary.json",
            "data/reports/latest_backtest_summary.json",
            "data/backtest_summary.json",
            "data/backtesting/latest_summary.json",
            "data/reports/backtest_summary.json",
        ],
        "validation_report_path": "data/reports/backtest_365d_v2_summary.json",
        "market_watch_report_path": "data/reports/market_watch_365d_summary.json",
        "live_paper_report_path": "data/reports/live_paper_session.json",
        "emergency_live_report_path": "data/reports/emergency_live_status.json",
        "challenge_command_center_report_path": "data/reports/challenge_command_center.json",
        "assisted_execution_report_path": "data/reports/assisted_execution_status.json",
        "demo_sandbox_report_path": "data/reports/demo_sandbox_status.json",
    }
    TELEGRAM_API_TEMPLATE = "https://api.telegram.org/bot{token}/{method}"
    SENSITIVE_KEYS = AICoachAnalyzer.SENSITIVE_KEYS

    def __init__(
        self,
        *,
        connector: MT5Connector | None = None,
        config_dir: str | Path | None = None,
        project_root: str | Path | None = None,
        snapshot_provider: SnapshotProvider | None = None,
    ) -> None:
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        load_dotenv(self.project_root / ".env")
        self.config_dir = Path(config_dir) if config_dir else self.project_root / "config"
        self.config = self._load_config()
        self.connector = connector
        self.snapshot_provider = snapshot_provider
        self.symbol_registry = SymbolRegistry(config_dir=self.config_dir)
        self.offset: int | None = None
        self._stack: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_http_status: int | None = None

    def handle_command(self, command: str, chat_id: str | int, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the standardized command handling result."""
        normalized_command = self.normalize_command(command)
        authorized = self.is_authorized_chat(chat_id)
        if not authorized:
            return {
                "command": normalized_command,
                "authorized": False,
                "success": False,
                "response_text": "Unauthorized.",
                "error": "UNAUTHORIZED",
            }

        if normalized_command not in self.allowed_commands:
            return {
                "command": normalized_command,
                "authorized": True,
                "success": False,
                "response_text": "Unknown command. Send /help.",
                "error": "UNKNOWN_COMMAND",
            }

        safe_snapshot = {}
        if normalized_command not in {"/start", "/help", "/ping"}:
            safe_snapshot = self.sanitize_snapshot(snapshot or self.get_snapshot())
        if normalized_command == "/market_watch":
            parts = str(command or "").strip().split()
            if len(parts) > 1:
                safe_snapshot["_market_watch_symbol"] = parts[1].upper().strip()
        if normalized_command in {
            "/assisted_ticket",
            "/assisted_approve",
            "/assisted_reject",
            "/assisted_dry_run",
            "/exec_approve",
            "/execute_approve",
            "/sandbox_ticket",
            "/sandbox_dry_run",
            "/sandbox_approve",
        }:
            parts = str(command or "").strip().split()
            if len(parts) > 1:
                key = "_sandbox_ticket_id" if normalized_command.startswith("/sandbox") else "_assisted_ticket_id"
                safe_snapshot[key] = parts[1].upper().strip()
        response = self.truncate_response(self.response_for_command(normalized_command, safe_snapshot))
        return {
            "command": normalized_command,
            "authorized": True,
            "success": True,
            "response_text": response,
            "error": None,
        }

    def response_for_command(self, command: str, snapshot: dict[str, Any]) -> str:
        """Route a normalized command to its formatter."""
        if command == "/start":
            return "Project Sentinel connected. Advisor Mode only."
        if command == "/help":
            return self.format_help()
        if command == "/ping":
            return "Sentinel online."
        if command == "/status":
            return self.format_status(snapshot)
        if command == "/summary":
            return self.format_summary(snapshot)
        if command == "/risk":
            return self.format_risk(snapshot.get("risk", {}))
        if command == "/news":
            return self.format_news(snapshot.get("news", {}))
        if command == "/coach":
            return self.format_coach(snapshot.get("coach", {}))
        if command == "/positions":
            return self.format_positions(snapshot.get("positions", []))
        if command == "/plans":
            return self.format_plans(snapshot.get("symbols", {}))
        if command == "/journal":
            return self.format_journal(snapshot.get("journal", []))
        if command == "/backtest":
            return self.format_backtest(snapshot.get("backtest", {}))
        if command == "/live_stats":
            return self.format_live_stats(snapshot.get("live_data", {}))
        if command == "/stress":
            return self.format_stress(snapshot.get("stress", {}))
        if command == "/readiness":
            return self.format_readiness(snapshot.get("readiness", {}))
        if command == "/settings":
            return self.format_settings(snapshot.get("settings", {}))
        if command == "/validation":
            return self.format_validation(snapshot.get("validation", {}))
        if command == "/market_watch":
            return self.format_market_watch(snapshot.get("market_watch", {}), snapshot.get("_market_watch_symbol"))
        if command == "/paper_status":
            return self.format_paper_status(snapshot.get("live_paper", {}))
        if command == "/paper_trades":
            return self.format_paper_trades(snapshot.get("live_paper", {}))
        if command == "/paper_stats":
            return self.format_paper_stats(snapshot.get("live_paper", {}))
        if command == "/live_health":
            return self.format_live_health(snapshot.get("live_paper", {}))
        if command == "/live_signals":
            return self.format_live_signals(snapshot.get("live_paper", {}))
        if command == "/live_mode":
            return self.format_live_mode(snapshot.get("emergency_live", {}))
        if command == "/live_limits":
            return self.format_live_limits(snapshot.get("emergency_live", {}))
        if command == "/live_killswitch":
            return self.format_live_killswitch(snapshot.get("emergency_live", {}))
        if command == "/approve_trade":
            return self.format_live_approval_action(snapshot.get("emergency_live", {}), action="APPROVE")
        if command == "/reject_trade":
            return self.format_live_approval_action(snapshot.get("emergency_live", {}), action="REJECT")
        if command == "/halt_live":
            return self.format_live_control_action(snapshot.get("emergency_live", {}), action="HALT")
        if command == "/resume_live":
            return self.format_live_control_action(snapshot.get("emergency_live", {}), action="RESUME")
        if command == "/challenge_status":
            return self.format_challenge_status(snapshot.get("challenge_command_center", {}))
        if command == "/challenge_progress":
            return self.format_challenge_progress(snapshot.get("challenge_command_center", {}))
        if command == "/challenge_risk":
            return self.format_challenge_risk(snapshot.get("challenge_command_center", {}))
        if command == "/challenge_phase":
            return self.format_challenge_phase(snapshot.get("challenge_command_center", {}))
        if command == "/challenge_governor":
            return self.format_challenge_governor(snapshot.get("challenge_command_center", {}))
        if command == "/challenge_recommendation":
            return self.format_challenge_recommendation(snapshot.get("challenge_command_center", {}))
        if command == "/activate_challenge_mode":
            return self.format_challenge_activation_action(snapshot.get("challenge_command_center", {}), action="ACTIVATE")
        if command == "/deactivate_challenge_mode":
            return self.format_challenge_activation_action(snapshot.get("challenge_command_center", {}), action="DEACTIVATE")
        if command == "/assisted_status":
            return self.format_assisted_status(snapshot.get("assisted_execution", {}))
        if command == "/assisted_ticket":
            return self.format_assisted_ticket(snapshot.get("assisted_execution", {}), snapshot.get("_assisted_ticket_id"))
        if command == "/assisted_approve":
            return self.format_assisted_approval_action(snapshot.get("assisted_execution", {}), snapshot.get("_assisted_ticket_id"), action="APPROVE")
        if command == "/assisted_reject":
            return self.format_assisted_approval_action(snapshot.get("assisted_execution", {}), snapshot.get("_assisted_ticket_id"), action="REJECT")
        if command == "/assisted_dry_run":
            return self.format_assisted_dry_run(snapshot.get("assisted_execution", {}), snapshot.get("_assisted_ticket_id"))
        if command in {"/exec_approve", "/execute_approve"}:
            return self.format_execution_approval_command(
                snapshot.get("assisted_execution", {}),
                snapshot.get("_assisted_ticket_id"),
                command=command,
            )
        if command == "/sandbox_status":
            return self.format_sandbox_status(snapshot.get("demo_sandbox", {}))
        if command == "/sandbox_symbols":
            return self.format_sandbox_symbols(snapshot.get("demo_sandbox", {}))
        if command == "/sandbox_ticket":
            return self.format_sandbox_ticket(snapshot.get("demo_sandbox", {}), snapshot.get("_sandbox_ticket_id"))
        if command == "/sandbox_dry_run":
            return self.format_sandbox_dry_run(snapshot.get("demo_sandbox", {}), snapshot.get("_sandbox_ticket_id"))
        if command == "/sandbox_approve":
            return self.format_sandbox_approve(snapshot.get("demo_sandbox", {}), snapshot.get("_sandbox_ticket_id"))
        if command == "/sandbox_disable":
            return self.format_sandbox_disable(snapshot.get("demo_sandbox", {}))
        if command == "/symbols":
            return self.format_symbols(snapshot.get("symbol_registry", []))
        symbol_key = command.lstrip("/").upper()
        return self.format_symbol(symbol_key, snapshot.get("symbols", {}).get(symbol_key, {}))

    def get_snapshot(self) -> dict[str, Any]:
        """Return a Sentinel snapshot from a mock provider or live engine stack."""
        if self.snapshot_provider:
            return self.snapshot_provider()
        return self.build_live_snapshot()

    def build_live_snapshot(self) -> dict[str, Any]:
        """Build a live Advisor Mode snapshot from existing Sentinel engines."""
        stack = self.live_stack()
        risk = stack["risk_governor"].evaluate()
        news = stack["news_filter"].check()
        symbols: dict[str, Any] = {}
        for label, broker_symbol in self.symbols.items():
            if broker_symbol == BTCObserver.SYMBOL:
                symbols[label] = self.build_btc_symbol_snapshot(stack["btc_observer"].observe())
                continue
            if broker_symbol == NAS100Observer.SYMBOL:
                symbols[label] = self.build_nas100_symbol_snapshot(stack["nas100_observer"].observe())
                continue
            confidence = stack["confidence_analyzer"].analyze(broker_symbol, context={"risk_reward": 3.0, "news_status": news})
            trade_plan = stack["trade_planner"].analyze(broker_symbol, confidence_context={"news_status": news}, risk_state=risk)
            symbols[label] = self.build_symbol_snapshot(
                symbol=broker_symbol,
                confidence=confidence,
                trade_plan=trade_plan,
            )

        coach = stack["ai_coach"].analyze(use_synthetic_if_empty=False)
        journal_engine = stack["journal_engine"]
        journal_count = journal_engine.count_records()
        return {
            "risk": risk,
            "news": news,
            "symbols": symbols,
            "coach": coach,
            "journal_records": journal_count,
            "positions": self.get_sentinel_positions(stack["connector"]),
            "journal": journal_engine.read_last_records(5),
            "backtest": self.load_backtest_summary(),
            "live_data": self.load_live_data_summary(),
            "stress": self.load_stress_summary(),
            "readiness": self.load_readiness_summary(),
            "settings": self.build_settings_summary(),
            "symbol_registry": self.symbol_registry.rows(self.symbol_metrics_from_backtest(self.load_backtest_summary())),
            "validation": self.load_validation_summary(),
            "market_watch": self.load_market_watch_summary(),
            "live_paper": self.load_live_paper_summary(),
            "emergency_live": self.load_emergency_live_summary(),
            "challenge_command_center": self.load_challenge_command_center_summary(),
            "assisted_execution": self.load_assisted_execution_summary(),
            "demo_sandbox": self.load_demo_sandbox_summary(),
        }

    def live_stack(self) -> dict[str, Any]:
        """Create and cache the shared live Sentinel engine stack."""
        if self._stack is not None:
            return self._stack
        connector = self.connector or MT5Connector()
        liquidity_analyzer = LiquidityAnalyzer(connector=connector)
        ict_analyzer = ICTAnalyzer(connector=connector, liquidity_analyzer=liquidity_analyzer)
        trend_analyzer = TrendAnalyzer(connector=connector)
        news_filter = NewsFilter()
        killzone_analyzer = KillzoneAnalyzer()
        smt_analyzer = SMTAnalyzer(connector=connector)
        confidence_analyzer = ConfidenceAnalyzer(
            connector=connector,
            trend_analyzer=trend_analyzer,
            liquidity_analyzer=liquidity_analyzer,
            ict_analyzer=ict_analyzer,
            news_filter=news_filter,
            killzone_analyzer=killzone_analyzer,
            smt_analyzer=smt_analyzer,
        )
        risk_governor = RiskGovernor(connector=connector)
        trade_planner = TradePlanner(
            connector=connector,
            confidence_analyzer=confidence_analyzer,
            risk_governor=risk_governor,
            ict_analyzer=ict_analyzer,
            liquidity_analyzer=liquidity_analyzer,
        )
        self._stack = {
            "connector": connector,
            "news_filter": news_filter,
            "confidence_analyzer": confidence_analyzer,
            "risk_governor": risk_governor,
            "trade_planner": trade_planner,
            "btc_observer": BTCObserver(connector=connector, killzone_analyzer=killzone_analyzer, smt_analyzer=smt_analyzer),
            "nas100_observer": NAS100Observer(connector=connector, killzone_analyzer=killzone_analyzer, registry=self.symbol_registry),
            "ai_coach": AICoachAnalyzer(),
            "journal_engine": JournalEngine(),
        }
        return self._stack

    @classmethod
    def build_symbol_snapshot(
        cls,
        *,
        symbol: str,
        confidence: dict[str, Any],
        trade_plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Combine confidence and trade-plan data into bot display shape."""
        killzone = confidence.get("killzone", {})
        narrative = confidence.get("narrative", {})
        display = confidence_display_fields(confidence, fallback_state=str(confidence.get("confidence_band", "UNAVAILABLE")))
        return {
            "symbol": symbol,
            "display_symbol": symbol,
            "experimental": False,
            "state": confidence.get("confidence_band", "UNAVAILABLE"),
            "confidence": confidence.get("total_confidence", 0),
            "raw_confidence": display["raw_confidence"],
            "adjusted_confidence": display["adjusted_confidence"],
            "raw_band": display["raw_band"],
            "guardrail_penalty": display["guardrail_penalty"] if display["band_differs"] else "",
            "mode": "PRODUCTION",
            "decision": confidence.get("decision", "UNAVAILABLE"),
            "killzone": killzone.get("active_killzone", "none"),
            "narrative_summary": narrative.get("summary", narrative.get("market_phase", "none")),
            "smt": confidence.get("smt", {}),
            "entry": trade_plan.get("entry", {}).get("price", 0.0),
            "sl": trade_plan.get("stop_loss", {}).get("price", 0.0),
            "tp1": trade_plan.get("take_profit", {}).get("tp1", 0.0),
            "tp2": trade_plan.get("take_profit", {}).get("tp2", 0.0),
            "tp3": trade_plan.get("take_profit", {}).get("tp3", 0.0),
            "lot_size": trade_plan.get("risk", {}).get("lot_size", 0.0),
            "plan_quality": trade_plan.get("plan_quality", "unavailable"),
            "execution_allowed": bool(trade_plan.get("execution_allowed", False)),
            "rejection_reasons": confidence.get("rejection_reasons", trade_plan.get("rejection_reasons", [])),
            "tier": SymbolRegistry().display_tier_for(symbol),
        }

    @classmethod
    def build_btc_symbol_snapshot(cls, btc: dict[str, Any]) -> dict[str, Any]:
        """Return Telegram display shape for BTC observer mode."""
        confidence = btc.get("confidence", {})
        trade_plan = btc.get("trade_plan", {})
        narrative = btc.get("narrative", {})
        killzone = btc.get("killzone", {})
        canonical_state = observer_state(confidence.get("observer_state", confidence.get("confidence_band", btc.get("state", "UNAVAILABLE"))))
        return {
            "symbol": BTCObserver.SYMBOL,
            "display_symbol": BTCObserver.DISPLAY_SYMBOL,
            "experimental": True,
            "observer": True,
            "mode": DEMO_SANDBOX_LABEL,
            "state": confidence.get("confidence_band", btc.get("state", "UNAVAILABLE")),
            "observer_state": canonical_state,
            "display_state": observer_display_state(canonical_state),
            "state_kind": "OBSERVER_MOVEMENT",
            "confidence": confidence.get("total_confidence", btc.get("score", 0)),
            "raw_confidence": confidence.get("total_confidence", btc.get("score", 0)),
            "adjusted_confidence": confidence.get("total_confidence", btc.get("score", 0)),
            "raw_band": confidence.get("confidence_band", btc.get("state", "UNAVAILABLE")),
            "guardrail_penalty": "",
            "observer_note": "SANDBOX DEMO ONLY. Not production, not funded, not challenge.",
            "decision": confidence.get("decision", "REJECTED"),
            "killzone": killzone.get("active_killzone", "none"),
            "narrative_summary": narrative.get("summary", confidence.get("narrative", {}).get("summary", "BTCUSD observer mode.")),
            "smt": confidence.get("smt", btc.get("smt", {})),
            "entry": 0.0,
            "sl": 0.0,
            "tp1": 0.0,
            "tp2": 0.0,
            "tp3": 0.0,
            "lot_size": 0.0,
            "plan_quality": trade_plan.get("plan_quality", "observer_only"),
            "execution_allowed": False,
            "rejection_reasons": confidence.get("rejection_reasons", [BTCObserver.REJECTION_REASON]),
            "tier": SymbolRegistry().display_tier_for(BTCObserver.SYMBOL),
        }

    @classmethod
    def build_nas100_symbol_snapshot(cls, nas100: dict[str, Any]) -> dict[str, Any]:
        """Return Telegram display shape for NAS100 observer mode."""
        confidence = nas100.get("confidence", {})
        trade_plan = nas100.get("trade_plan", {})
        narrative = nas100.get("narrative", {})
        killzone = nas100.get("killzone", {})
        canonical_state = observer_state(confidence.get("observer_state", confidence.get("confidence_band", nas100.get("state", "UNAVAILABLE"))))
        return {
            "symbol": NAS100Observer.SYMBOL,
            "display_symbol": NAS100Observer.DISPLAY_SYMBOL,
            "experimental": False,
            "observer": True,
            "mode": DEMO_SANDBOX_LABEL,
            "state": confidence.get("confidence_band", nas100.get("state", "UNAVAILABLE")),
            "observer_state": canonical_state,
            "display_state": observer_display_state(canonical_state),
            "state_kind": "OBSERVER_MOVEMENT",
            "confidence": confidence.get("total_confidence", nas100.get("score", 0)),
            "raw_confidence": confidence.get("total_confidence", nas100.get("score", 0)),
            "adjusted_confidence": confidence.get("total_confidence", nas100.get("score", 0)),
            "raw_band": confidence.get("confidence_band", nas100.get("state", "UNAVAILABLE")),
            "guardrail_penalty": "",
            "observer_note": "SANDBOX DEMO ONLY. Not production, not funded, not challenge.",
            "decision": confidence.get("decision", "REJECTED"),
            "killzone": killzone.get("active_killzone", "none"),
            "narrative_summary": narrative.get("summary", "NAS100 observer mode."),
            "smt": confidence.get("smt", {"smt_detected": False}),
            "entry": 0.0,
            "sl": 0.0,
            "tp1": 0.0,
            "tp2": 0.0,
            "tp3": 0.0,
            "lot_size": 0.0,
            "plan_quality": trade_plan.get("plan_quality", "observer_only"),
            "execution_allowed": False,
            "rejection_reasons": confidence.get("rejection_reasons", [NAS100Observer.REJECTION_REASON]),
            "tier": SymbolRegistry().display_tier_for(NAS100Observer.SYMBOL),
        }

    def get_sentinel_positions(self, connector: MT5Connector | None = None) -> list[dict[str, Any]]:
        """Return current Sentinel positions and pending orders without modifying them."""
        connector = connector or self.connector
        if connector is None:
            return []
        mt5_module = getattr(connector, "mt5", None)
        if mt5_module is None:
            return []

        manager = PositionManager(connector=connector)
        items: list[dict[str, Any]] = []
        for raw_position in (mt5_module.positions_get() or []) if hasattr(mt5_module, "positions_get") else []:
            position = PositionManager.to_dict(raw_position)
            if not PositionManager.is_sentinel_order(position):
                continue
            items.append(
                {
                    "status": "OPEN",
                    "symbol": str(position.get("symbol", "")).upper().strip(),
                    "ticket": int(position.get("ticket", 0) or 0),
                    "type": manager.position_direction(position),
                    "volume": position.get("volume", 0.0),
                    "entry": position.get("price_open", position.get("entry_price", 0.0)),
                    "sl": position.get("sl", 0.0),
                    "tp": position.get("tp", 0.0),
                    "profit": position.get("profit", 0.0),
                    "current_r": manager.calculate_current_r(position),
                }
            )
        for raw_order in (mt5_module.orders_get() or []) if hasattr(mt5_module, "orders_get") else []:
            order = PositionManager.to_dict(raw_order)
            if not PositionManager.is_sentinel_order(order):
                continue
            items.append(
                {
                    "status": "PENDING",
                    "symbol": str(order.get("symbol", "")).upper().strip(),
                    "ticket": int(order.get("ticket", order.get("order", 0)) or 0),
                    "type": order.get("type", ""),
                    "volume": order.get("volume_current", order.get("volume_initial", order.get("volume", 0.0))),
                    "entry": order.get("price_open", order.get("price", 0.0)),
                    "sl": order.get("sl", 0.0),
                    "tp": order.get("tp", 0.0),
                    "profit": 0.0,
                    "current_r": 0.0,
                }
            )
        return items

    def load_backtest_summary(self) -> dict[str, Any]:
        """Load the latest cached backtest summary if one exists."""
        for configured_path in self.config.get("backtest_summary_paths", []):
            path = Path(str(configured_path))
            if not path.is_absolute():
                path = self.project_root / path
            summary = load_cached_backtest_summary(path)
            if summary:
                return {"available": True, "path": str(path), "data": summary}
        return {"available": False}

    def load_validation_summary(self) -> dict[str, Any]:
        """Load the latest validation checkpoint report."""
        path = Path(str(self.config.get("validation_report_path", "data/reports/backtest_365d_v2_summary.json")))
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            return {"available": False}
        try:
            return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
        except Exception as exc:
            logger.warning("Validation summary unavailable: {}", exc)
            return {"available": False, "reason": str(exc)}

    def load_market_watch_summary(self) -> dict[str, Any]:
        """Load the latest Market Watch report."""
        path = Path(str(self.config.get("market_watch_report_path", "data/reports/market_watch_365d_summary.json")))
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            return {"available": False}
        try:
            return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
        except Exception as exc:
            logger.warning("Market Watch summary unavailable: {}", exc)
            return {"available": False, "reason": str(exc)}

    def load_live_paper_summary(self) -> dict[str, Any]:
        """Load the latest live paper phase report."""
        path = Path(str(self.config.get("live_paper_report_path", "data/reports/live_paper_session.json")))
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            return {"available": False}
        try:
            return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
        except Exception as exc:
            logger.warning("Live paper summary unavailable: {}", exc)
            return {"available": False, "reason": str(exc)}

    def load_emergency_live_summary(self) -> dict[str, Any]:
        """Load the latest emergency live status report."""
        path = Path(str(self.config.get("emergency_live_report_path", "data/reports/emergency_live_status.json")))
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            return {"available": False}
        try:
            return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
        except Exception as exc:
            logger.warning("Emergency live summary unavailable: {}", exc)
            return {"available": False, "reason": str(exc)}

    def load_challenge_command_center_summary(self) -> dict[str, Any]:
        """Load the latest Challenge Command Center report."""
        path = Path(str(self.config.get("challenge_command_center_report_path", "data/reports/challenge_command_center.json")))
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            return {"available": False}
        try:
            return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
        except Exception as exc:
            logger.warning("Challenge Command Center summary unavailable: {}", exc)
            return {"available": False, "reason": str(exc)}

    def load_assisted_execution_summary(self) -> dict[str, Any]:
        """Load the latest assisted execution bridge report."""
        path = Path(str(self.config.get("assisted_execution_report_path", "data/reports/assisted_execution_status.json")))
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            return {"available": False}
        try:
            return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
        except Exception as exc:
            logger.warning("Assisted execution summary unavailable: {}", exc)
            return {"available": False, "reason": str(exc)}

    def load_demo_sandbox_summary(self) -> dict[str, Any]:
        """Load the latest demo sandbox report."""
        path = Path(str(self.config.get("demo_sandbox_report_path", "data/reports/demo_sandbox_status.json")))
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            return {"available": False}
        try:
            return {"available": True, "path": str(path), "data": json.loads(path.read_text(encoding="utf-8"))}
        except Exception as exc:
            logger.warning("Demo sandbox summary unavailable: {}", exc)
            return {"available": False, "reason": str(exc)}

    def load_live_data_summary(self) -> dict[str, Any]:
        """Load live-data collection stats for Telegram reporting."""
        collector = LiveDataCollector(config_dir=self.config_dir, project_root=self.project_root)
        return collector.summary()

    def load_stress_summary(self) -> dict[str, Any]:
        """Run Monte Carlo stress analytics from the cached 365D report."""
        try:
            return MonteCarloEngine(config_dir=self.config_dir, project_root=self.project_root).run_from_report()
        except MonteCarloEngineError as exc:
            logger.warning("Monte Carlo stress summary unavailable: {}", exc)
            return {"available": False, "reason": str(exc)}

    @staticmethod
    def load_readiness_summary() -> dict[str, Any]:
        """Return a safe checklist summary when no assisted plan is selected."""
        reason = "No assisted trade plan selected"
        results = [
            ReadinessChecker.result("mt5_connected", "FAIL", reason),
            ReadinessChecker.result("account_verified", "PASS", None),
            ReadinessChecker.result("risk_allowed", "PASS", None),
            ReadinessChecker.result("news_clear", "PASS", None),
            ReadinessChecker.result("killzone_valid", "FAIL", reason),
            ReadinessChecker.result("guardrails_pass", "FAIL", reason),
            ReadinessChecker.result("spread_acceptable", "FAIL", reason),
            ReadinessChecker.result("lot_valid", "FAIL", reason),
            ReadinessChecker.result("rr_validation", "FAIL", reason),
            ReadinessChecker.result("execution_mode_assisted", "FAIL", reason),
            ReadinessChecker.result("manual_confirmation_required", "PASS", None),
        ]
        blocking_reasons = sorted({str(item["reason"]) for item in results if item["status"] == "FAIL" and item.get("reason")})
        checks_passed = sum(1 for item in results if item["status"] == "PASS")
        checks_failed = sum(1 for item in results if item["status"] == "FAIL")
        return {
            "ready": False,
            "score": checks_passed,
            "checks_passed": checks_passed,
            "checks_failed": checks_failed,
            "results": results,
            "blocking_reasons": blocking_reasons,
        }

    def build_settings_summary(self) -> dict[str, Any]:
        """Return a safe config summary for Telegram display."""
        execution = self._load_yaml_file(self.config_dir / "execution.yaml")
        alerts = self._load_yaml_file(self.config_dir / "alerts.yaml")
        guardrails = self._load_yaml_file(self.config_dir / "strategy_guardrails.yaml")
        news = self._load_yaml_file(self.config_dir / "news_filter.yaml")
        journal = self._load_yaml_file(self.config_dir / "journal.yaml")
        return {
            "execution_mode": execution.get("execution_mode", "advisor"),
            "telegram_enabled": self.enabled,
            "alerts_enabled": bool(alerts.get("enabled", False)),
            "guardrails_enabled": bool(guardrails.get("enabled", False)),
            "news_filter_enabled": bool(news.get("enabled", False)),
            "journal_enabled": bool(journal.get("enabled", False)),
        }

    def poll_once(self) -> list[dict[str, Any]]:
        """Read Telegram updates once, respond to commands, and return results."""
        updates = self.get_updates()
        results = []
        for update in updates:
            update_id = int(update.get("update_id", 0))
            self.offset = max(self.offset or 0, update_id + 1)
            message = update.get("message", {}) or update.get("edited_message", {})
            chat = message.get("chat", {})
            chat_id = chat.get("id")
            text = str(message.get("text", "")).strip()
            if not text:
                continue
            command = text.split()[0]
            result = self.handle_command(command, chat_id)
            self.send_message(chat_id, result["response_text"])
            results.append(result)
        return results

    def run_polling(self) -> None:
        """Run getUpdates polling until interrupted."""
        while True:
            self.poll_once()
            time.sleep(self.polling_interval_seconds)

    def get_updates(self) -> list[dict[str, Any]]:
        """Fetch Telegram getUpdates results."""
        token = self.require_token()
        params = {
            "timeout": int(self.polling_interval_seconds),
        }
        if self.offset is not None:
            params["offset"] = self.offset
        url = self.telegram_url("getUpdates", token=token) + "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=int(self.polling_interval_seconds) + 10) as response:
                self.last_http_status = int(response.status)
                data = json.loads(response.read().decode("utf-8"))
                if not data.get("ok", False):
                    self.last_error = "Telegram getUpdates returned ok=false"
                    return []
                return list(data.get("result", []))
        except Exception as exc:
            self.last_error = "Telegram getUpdates failed"
            logger.warning("{}: {}", self.last_error, exc)
            return []

    def send_message(self, chat_id: str | int, text: str) -> bool:
        """Send a Telegram message with HTML parse mode."""
        token = self.require_token()
        settings = self.config.get("telegram_settings", {})
        payload = urllib.parse.urlencode(
            {
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": str(settings.get("parse_mode", "HTML")),
                "disable_web_page_preview": "true" if bool(settings.get("disable_web_page_preview", True)) else "false",
            }
        ).encode("utf-8")
        request = urllib.request.Request(self.telegram_url("sendMessage", token=token), data=payload, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                self.last_http_status = int(response.status)
                return 200 <= int(response.status) < 300
        except Exception as exc:
            self.last_error = "Telegram sendMessage failed"
            logger.warning("{}: {}", self.last_error, exc)
            return False

    def validate_runtime(self) -> dict[str, bool]:
        """Return runtime readiness without exposing credentials."""
        token_loaded = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
        chat_id_loaded = bool(os.getenv("TELEGRAM_CHAT_ID"))
        return {
            "enabled": self.enabled,
            "token_loaded": token_loaded,
            "chat_id_loaded": chat_id_loaded,
            "valid": self.enabled and token_loaded and chat_id_loaded,
        }

    def require_token(self) -> str:
        """Return Telegram bot token or raise without printing it."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise TelegramCommandBotError("TELEGRAM_BOT_TOKEN is required.")
        return token

    def is_authorized_chat(self, chat_id: str | int | None) -> bool:
        """Return whether the chat ID is allowed."""
        allowed = os.getenv("TELEGRAM_CHAT_ID")
        return bool(allowed) and str(chat_id) == str(allowed)

    def format_help(self) -> str:
        """Return command list."""
        commands = "\n".join(f"- {escape(command)}" for command in self.allowed_commands)
        return "\n".join(
            [
                "<b>Project Sentinel Commands</b>",
                "",
                commands,
                "",
                "Advisor Mode only. Assisted demo approval is gated; no live-funded or autonomous execution.",
            ]
        )

    def format_status(self, snapshot: dict[str, Any]) -> str:
        """Return risk, news, and symbol state summary."""
        risk = snapshot.get("risk", {})
        news = snapshot.get("news", {})
        account = risk.get("account", {})
        permission = risk.get("permission", {})
        lines = [
            "<b>Sentinel Status</b>",
            "",
            f"Balance: {self.html(account.get('balance', 0.0))} {self.html(account.get('currency', 'USD'))}",
            f"Equity: {self.html(account.get('equity', 0.0))} {self.html(account.get('currency', 'USD'))}",
            f"Risk Status: {self.html(permission.get('status', 'UNKNOWN'))}",
            f"News Status: {self.html(NewsFilter.format_status(news))}",
            "",
            "<b>Symbols</b>",
        ]
        for symbol in self.symbols:
            data = snapshot.get("symbols", {}).get(symbol, {})
            lines.append(
                f"{self.html(symbol)}: {self.html(data.get('state', 'UNAVAILABLE'))} / "
                f"{self.html(data.get('confidence', 0))} / {self.html(data.get('decision', 'UNAVAILABLE'))}"
            )
        lines.append("")
        lines.append("Advisor Mode only.")
        return "\n".join(lines)

    def format_summary(self, snapshot: dict[str, Any]) -> str:
        """Return short command center summary."""
        risk = snapshot.get("risk", {})
        news = snapshot.get("news", {})
        permission = risk.get("permission", {})
        lines = [
            "<b>Command Center Summary</b>",
            "",
            f"Risk: {self.html(permission.get('status', 'UNKNOWN'))}",
            f"News: {self.html(NewsFilter.format_status(news))}",
        ]
        for symbol in self.symbols:
            data = snapshot.get("symbols", {}).get(symbol, {})
            if data.get("mode") == DEMO_SANDBOX_LABEL:
                lines.append(
                    f"{self.html(symbol)} Sandbox {self.html(data.get('state', 'UNAVAILABLE'))} "
                    f"Score {self.html(data.get('confidence', 0))} - DEMO_SANDBOX"
                )
            elif data.get("observer") or data.get("experimental") or data.get("mode") == OBSERVER_ONLY_LABEL:
                lines.append(
                    f"{self.html(symbol)} Observer {self.html(data.get('state', 'UNAVAILABLE'))} "
                    f"Score {self.html(data.get('confidence', 0))} - OBSERVER_ONLY"
                )
            else:
                lines.append(
                    f"{self.html(symbol)} {self.html(data.get('state', 'UNAVAILABLE'))} "
                    f"Raw {self.html(data.get('raw_confidence', data.get('confidence', 0)))} / "
                    f"Adjusted {self.html(data.get('adjusted_confidence', data.get('confidence', 0)))} - "
                    f"{self.html(data.get('decision', 'UNAVAILABLE'))}"
                )
        coach_summary = snapshot.get("coach", {}).get("summary")
        if coach_summary:
            lines.extend(["", self.html(coach_summary)])
        lines.append("")
        lines.append("No trade execution.")
        return "\n".join(lines)

    def format_symbol(self, symbol: str, data: dict[str, Any]) -> str:
        """Return detailed symbol snapshot."""
        if not data:
            return f"{self.html(symbol)} snapshot unavailable."
        demo_sandbox = data.get("mode") == DEMO_SANDBOX_LABEL
        observer_only = bool(data.get("observer") or data.get("experimental") or data.get("mode") == OBSERVER_ONLY_LABEL)
        lines = [
            f"<b>{self.html(data.get('display_symbol', symbol))} Snapshot</b>",
            "",
            f"Tier: {self.html(data.get('tier', self.symbol_registry.display_tier_for(symbol)))}",
            f"Mode: {self.html(self.symbol_mode_label(data))}",
        ]
        if demo_sandbox:
            lines.extend(
                [
                    f"Sandbox State: {self.html(data.get('display_state', data.get('state', 'UNAVAILABLE')))}",
                    f"Observer State: {self.html(data.get('observer_state', observer_state(data.get('state', 'UNAVAILABLE'))))}",
                    f"Score: {self.html(data.get('confidence', 0))}",
                    "SANDBOX DEMO ONLY. NOT PRODUCTION. NOT FUNDED. NOT CHALLENGE.",
                ]
            )
        elif observer_only:
            lines.extend(
                [
                    f"Observer State: {self.html(data.get('observer_state', observer_state(data.get('state', 'UNAVAILABLE'))))}",
                    f"Movement State: {self.html(data.get('display_state', data.get('state', 'UNAVAILABLE')))}",
                    f"Score: {self.html(data.get('confidence', 0))}",
                    "OBSERVER_ONLY: Diagnostic only. No execution.",
                ]
            )
        else:
            lines.extend(
                [
                    f"State: {self.html(data.get('state', 'UNAVAILABLE'))}",
                    f"Raw Confidence: {self.html(data.get('raw_confidence', data.get('confidence', 0)))}",
                    f"Adjusted Confidence: {self.html(data.get('adjusted_confidence', data.get('confidence', 0)))}",
                    f"Raw Band: {self.html(data.get('raw_band', data.get('state', 'UNAVAILABLE')))}",
                    f"Guardrail Penalty: {self.html(data.get('guardrail_penalty') or 'none')}",
                ]
            )
        lines.extend(
            [
                f"Decision: {self.html(data.get('decision', 'UNAVAILABLE'))}",
                f"Killzone: {self.html(self.display_killzone(data.get('killzone', 'none')))}",
                f"Narrative: {self.html(data.get('narrative_summary', 'none'))}",
                f"SMT: {self.html(self.format_smt(data.get('smt', {})))}",
                "",
                f"Entry: {self.html(data.get('entry', 0.0))}",
                f"SL: {self.html(data.get('sl', 0.0))}",
                f"TP1: {self.html(data.get('tp1', 0.0))}",
                f"TP2: {self.html(data.get('tp2', 0.0))}",
                f"TP3: {self.html(data.get('tp3', 0.0))}",
                f"Lot Size: {self.html(data.get('lot_size', 0.0))}",
                "",
                f"Rejection Reasons: {self.html(self.format_list(data.get('rejection_reasons', [])))}",
                "",
                "Advisor Mode only.",
            ]
        )
        return "\n".join(lines)

    def format_symbols(self, rows: list[dict[str, Any]]) -> str:
        """Return symbol tier registry summary."""
        rows = rows or self.symbol_registry.rows()
        lines = ["<b>Symbol Registry</b>", ""]
        for row in rows:
            lines.extend(
                [
                    f"<b>{self.html(row.get('symbol', 'UNKNOWN'))}</b>",
                    f"Tier: {self.html(row.get('tier', 'Unregistered'))}",
                    f"PF: {self.html(row.get('pf', 0.0))}",
                    f"WR: {self.html(row.get('wr', 0.0))}%",
                    f"Trades: {self.html(row.get('trades', 0))}",
                    f"DD: {self.html(row.get('dd', 0.0))}%",
                    f"Status: {self.html(row.get('status', 'UNKNOWN'))}",
                    "",
                ]
            )
        lines.append("Advisor Mode only. Observer symbols cannot execute.")
        return "\n".join(lines).strip()

    def format_risk(self, risk: dict[str, Any]) -> str:
        """Return risk status without account login/server."""
        account = risk.get("account", {})
        risk_data = risk.get("risk", {})
        permission = risk.get("permission", {})
        return "\n".join(
            [
                "<b>Risk Governor</b>",
                "",
                f"Balance: {self.html(account.get('balance', 0.0))} {self.html(account.get('currency', 'USD'))}",
                f"Equity: {self.html(account.get('equity', 0.0))} {self.html(account.get('currency', 'USD'))}",
                f"Risk Amount: {self.html(risk_data.get('risk_amount', 0.0))} {self.html(account.get('currency', 'USD'))}",
                f"Risk Status: {self.html(permission.get('status', 'UNKNOWN'))}",
                f"Warnings: {self.html(self.format_list(permission.get('warnings', [])))}",
                f"Block Reasons: {self.html(self.format_list(permission.get('block_reasons', [])))}",
            ]
        )

    def format_news(self, news: dict[str, Any]) -> str:
        """Return news lock status."""
        return "\n".join(
            [
                "<b>News Filter</b>",
                "",
                f"News Status: {self.html(NewsFilter.format_status(news))}",
                f"Lock Active: {self.html(bool(news.get('lock_active', False)))}",
                f"Event: {self.html(news.get('event_name') or 'none')}",
                f"Reason: {self.html(news.get('reason', 'none') or 'none')}",
            ]
        )

    def format_coach(self, coach: dict[str, Any]) -> str:
        """Return coach summary and top recommendations."""
        recommendations = coach.get("recommendations", [])[:3]
        lines = [
            "<b>AI Coach</b>",
            "",
            self.html(coach.get("summary", "Coach summary unavailable.")),
            "",
            "<b>Top Recommendations</b>",
        ]
        if not recommendations:
            lines.append("- none")
        for item in recommendations:
            lines.append(
                f"- [{self.html(item.get('severity', 'INFO'))}] "
                f"{self.html(item.get('category', 'psychology'))}: {self.html(item.get('message', ''))}"
            )
        lines.extend(["", "Advisor Mode only."])
        return "\n".join(lines)

    def format_positions(self, positions: list[dict[str, Any]]) -> str:
        """Return current Sentinel position/order summary."""
        if not positions:
            return "No open Sentinel positions or pending orders."
        lines = ["<b>Sentinel Positions</b>", ""]
        for item in positions:
            lines.extend(
                [
                    f"<b>{self.html(item.get('symbol', 'UNKNOWN'))}</b> {self.html(item.get('status', 'OPEN'))}",
                    f"Ticket: {self.html(item.get('ticket', 0))}",
                    f"Type: {self.html(item.get('type', ''))}",
                    f"Volume: {self.html(item.get('volume', 0.0))}",
                    f"Entry: {self.html(item.get('entry', 0.0))}",
                    f"SL: {self.html(item.get('sl', 0.0))}",
                    f"TP: {self.html(item.get('tp', 0.0))}",
                    f"Profit: {self.html(item.get('profit', 0.0))}",
                    f"Current R: {self.html(item.get('current_r', 'n/a'))}",
                    "",
                ]
            )
        lines.append("Advisor Mode only.")
        return "\n".join(lines)

    def format_plans(self, symbols: dict[str, dict[str, Any]]) -> str:
        """Return current watched-symbol trade plans."""
        lines = ["<b>Trade Plans</b>", ""]
        for symbol in self.symbols:
            data = symbols.get(symbol, {})
            lines.extend(
                [
                    f"<b>{self.html(symbol)}</b>",
                    f"State: {self.html(data.get('state', 'UNAVAILABLE'))}",
                    f"Plan Quality: {self.html(data.get('plan_quality', 'unavailable'))}",
                    f"Entry: {self.html(data.get('entry', 0.0))}",
                    f"SL: {self.html(data.get('sl', 0.0))}",
                    f"TP1: {self.html(data.get('tp1', 0.0))}",
                    f"TP2: {self.html(data.get('tp2', 0.0))}",
                    f"TP3: {self.html(data.get('tp3', 0.0))}",
                    f"Lot Size: {self.html(data.get('lot_size', 0.0))}",
                    f"Execution Allowed: {self.html(bool(data.get('execution_allowed', False)))}",
                    f"Rejection Reasons: {self.html(self.format_list(data.get('rejection_reasons', [])))}",
                    "",
                ]
            )
        lines.append("No order placement from Telegram.")
        return "\n".join(lines)

    def format_journal(self, records: list[dict[str, Any]]) -> str:
        """Return the last five journal records."""
        if not records:
            return "No journal records available."
        lines = ["<b>Last Journal Records</b>", ""]
        for record in records[-5:]:
            trade_plan = record.get("trade_plan", {})
            reasons = record.get("rejection_reasons", []) or []
            top_reason = reasons[0] if reasons else "none"
            lines.extend(
                [
                    f"{self.html(record.get('timestamp', 'unknown'))}",
                    f"{self.html(record.get('symbol', 'UNKNOWN'))} / confidence {self.html(record.get('confidence', 0))} / {self.html(record.get('decision', 'UNAVAILABLE'))}",
                    f"Plan Quality: {self.html(trade_plan.get('plan_quality', 'unavailable'))}",
                    f"Top Rejection: {self.html(top_reason)}",
                    "",
                ]
            )
        return "\n".join(lines).strip()

    def format_backtest(self, backtest: dict[str, Any]) -> str:
        """Return cached long-horizon backtest summary."""
        if not backtest.get("available"):
            return "No backtest summary available. Run backtest first."
        data = normalize_backtest_summary(backtest.get("data", {}))
        thirty = data.get("days_30", {})
        ninety = data.get("days_90", {})
        phase_decision = short_phase_decision(data)
        return "\n".join(
            [
                "<b>Backtest Summary</b>",
                "",
                "30D:",
                f"PF: {self.html(thirty.get('pf', 0.0))}",
                f"WR: {self.html(thirty.get('win_rate', 0.0))}%",
                f"Trades: {self.html(thirty.get('trades', 0))}",
                "",
                "90D:",
                f"PF: {self.html(ninety.get('pf', 0.0))}",
                f"WR: {self.html(ninety.get('win_rate', 0.0))}%",
                f"Trades: {self.html(ninety.get('trades', 0))}",
                "",
                "Decision:",
                self.html(phase_decision),
            ]
        )

    def format_live_stats(self, live_data: dict[str, Any]) -> str:
        """Return cached live-data collection stats."""
        return LiveDataCollector.format_live_stats(live_data)

    def format_stress(self, stress: dict[str, Any]) -> str:
        """Return compact Monte Carlo stress report."""
        if not stress.get("available"):
            return "No Monte Carlo stress test available. Run scripts/test_monte_carlo.py or refresh the 365D backtest first."
        safe_risk = stress.get("safe_risk_percent", 0.0)
        safe_model = stress.get("risk_models", {}).get(f"{float(safe_risk):g}%", {})
        if not safe_model:
            safe_model = next(iter(stress.get("risk_models", {}).values()), {})
        drawdown = safe_model.get("drawdown", {})
        streaks = safe_model.get("streaks", {})
        ruin = safe_model.get("risk_of_ruin", {})
        autonomous = "RECOMMENDED" if stress.get("autonomous_mode_recommended") else "NOT RECOMMENDED"
        return "\n".join(
            [
                "<b>Monte Carlo Stress Test</b>",
                f"Safe Risk: {self.html(safe_risk)}%",
                f"95% DD: {self.html(drawdown.get('p95_dd', 0.0))}%",
                f"Worst Losing Streak: {self.html(streaks.get('worst_losing_streak', 0))}",
                f"4% Breach Probability: {self.html(ruin.get('breach_4_percent', 0.0))}%",
                f"Autonomous Mode: {self.html(autonomous)}",
                "",
                "Advisor Mode only.",
            ]
        )

    def format_readiness(self, readiness: dict[str, Any]) -> str:
        """Return compact assisted-execution readiness status."""
        status = "READY" if readiness.get("ready") else "BLOCKED"
        failed = readiness.get("checks_failed", 0)
        reasons = readiness.get("blocking_reasons", []) or ["No blocking reasons reported."]
        lines = [
            "<b>Readiness</b>",
            f"Status: {self.html(status)}",
            f"Failed Checks: {self.html(failed)}",
            "",
            "<b>Blocking Reasons</b>",
        ]
        lines.extend(f"- {self.html(reason)}" for reason in reasons[:5])
        lines.extend(["", "Advisor + Assisted mode only. No autonomous execution."])
        return "\n".join(lines)

    def format_settings(self, settings: dict[str, Any]) -> str:
        """Return safe configuration summary only."""
        return "\n".join(
            [
                "<b>Sentinel Settings</b>",
                "",
                f"execution_mode: {self.html(settings.get('execution_mode', 'advisor'))}",
                f"telegram enabled: {self.html(bool(settings.get('telegram_enabled', False)))}",
                f"alerts enabled: {self.html(bool(settings.get('alerts_enabled', False)))}",
                f"guardrails enabled: {self.html(bool(settings.get('guardrails_enabled', False)))}",
                f"news filter enabled: {self.html(bool(settings.get('news_filter_enabled', False)))}",
                f"journal enabled: {self.html(bool(settings.get('journal_enabled', False)))}",
            ]
        )

    def format_validation(self, validation: dict[str, Any]) -> str:
        """Return compact Master Sprint 3 validation summary."""
        if not validation.get("available"):
            return "No validation checkpoint available. Run scripts/run_validation_checkpoint.py first."
        data = validation.get("data", {})
        approved = data.get("approved_robustness_baseline", {})
        observer = data.get("symbol_expansion_observer_only", {})
        observer_diagnostics = data.get("observer_diagnostics", {})
        xau = data.get("xau_smt", {})
        non_invasive = bool(data.get("matches_approved_baseline", False))
        lines = [
            "<b>SENTINEL VALIDATION CHECKPOINT</b>",
            "",
            "<b>Production Baseline</b>",
            f"PF: {self.html(approved.get('pf', 0.0))}",
            f"WR: {self.html(approved.get('win_rate', 0.0))}%",
            f"Trades: {self.html(approved.get('trades', 0))}",
            f"DD: {self.html(approved.get('max_drawdown', 0.0))}%",
            "",
            "<b>Observer Mode</b>",
            f"Non-invasive: {self.html(str(non_invasive).upper())}",
            f"Observer PF: {self.html(observer.get('pf', 0.0))}",
            "",
            "<b>Observer Symbols</b>",
        ]
        for symbol in ("BTCUSD", "NAS100", "EURUSD", "GBPUSD"):
            item = observer_diagnostics.get(symbol, {})
            label = item.get("display_symbol") or ("BTC" if symbol == "BTCUSD" else ("NAS100/USTEC" if symbol == "NAS100" else symbol))
            lines.append(f"{self.html(label)}: {self.html(item.get('data_status', 'MISSING'))}")
        lines.extend(
            [
                "",
                "<b>XAU SMT</b>",
                f"Dependency: {self.html(xau.get('dependency', 'NO_SMT_SAMPLE'))}",
                f"Hard Block: {self.html(bool(xau.get('rule', {}).get('hard_block_enabled', False)))}",
                "",
                "<b>Decision</b>",
                self.html(data.get("decision", "FAIL")),
            ]
        )
        return "\n".join(lines)

    def format_market_watch(self, market_watch: dict[str, Any], symbol_filter: str | None = None) -> str:
        """Return compact Market Watch advisory summary."""
        if not market_watch.get("available"):
            return "No Market Watch report available. Run scripts/run_market_watch_backtest.py first."
        data = market_watch.get("data", {})
        diagnostics = data.get("strategy_diagnostics", {})
        aliases = {"BTC": "BTCUSD", "USTEC": "NAS100"}
        selected_symbol = aliases.get(str(symbol_filter or "").upper(), str(symbol_filter or "").upper())
        symbols = [selected_symbol] if selected_symbol else list(diagnostics.keys())
        lines = ["<b>SENTINEL MARKET WATCH</b>", ""]
        for symbol in symbols:
            item = diagnostics.get(symbol)
            if not item:
                continue
            scores = item.get("scores", {})
            lines.extend(
                [
                    f"<b>{self.html(symbol)}</b>",
                    f"Pattern: {self.html(self.title_text(item.get('dominant_pattern', 'no_clear_pattern')))}",
                    f"Session Quality: {self.html(item.get('session_quality', 0))}",
                    f"ICT: {self.html(scores.get('ict_liquidity', 0))}",
                    f"Trend: {self.html(scores.get('trend_following', 0))}",
                    f"Mean Reversion: {self.html(scores.get('mean_reversion', 0))}",
                    "",
                    "Selected:",
                    self.html(self.title_text(item.get("selected_strategy", "no_trade"))),
                    "",
                    "Mode:",
                    "Advisory Only",
                    "",
                    "Production Impact:",
                    self.html(bool(item.get("affects_production", False))),
                    "",
                ]
            )
        if len(lines) <= 2:
            return "Market Watch symbol unavailable."
        lines.extend(
            [
                f"Decision: {self.html(data.get('decision', 'FAIL'))}",
                f"Recommendation: {self.html(data.get('recommendation', 'Keep advisory only'))}",
            ]
        )
        return "\n".join(lines).strip()

    def format_paper_status(self, live_paper: dict[str, Any]) -> str:
        """Return live paper runtime status."""
        if not live_paper.get("available"):
            return "No live paper report available. Run scripts/run_live_paper_phase.py first."
        data = live_paper.get("data", {})
        health = data.get("live_feed_health", {})
        stats = data.get("paper_stats", {})
        return "\n".join(
            [
                "<b>LIVE PAPER STATUS</b>",
                "",
                "Mode: PAPER_ONLY",
                "Broker Orders: DISABLED",
                "Autonomous Execution: DISABLED",
                f"Runtime: {self.html('READY' if data.get('runtime_ready') else 'NOT READY')}",
                f"Feed: {self.html(health.get('classification', 'UNUSABLE'))} ({self.html(health.get('score', 0.0))})",
                f"Trades: {self.html(stats.get('trades', 0))}",
                f"PF: {self.html(stats.get('pf', 0.0))}",
                f"WR: {self.html(stats.get('win_rate', 0.0))}%",
            ]
        )

    def format_paper_trades(self, live_paper: dict[str, Any]) -> str:
        """Return recent paper trades."""
        if not live_paper.get("available"):
            return "No live paper report available. Run scripts/run_live_paper_phase.py first."
        trades = live_paper.get("data", {}).get("paper_trades", [])[-5:]
        lines = ["<b>LIVE PAPER TRADES</b>", ""]
        for trade in trades:
            lines.append(
                f"{self.html(trade.get('paper_trade_id'))} {self.html(trade.get('symbol'))} "
                f"{self.html(trade.get('state'))} RR {self.html(trade.get('rr', 0.0))}"
            )
        return "\n".join(lines) if trades else "No paper trades recorded."

    def format_paper_stats(self, live_paper: dict[str, Any]) -> str:
        """Return live paper performance stats."""
        if not live_paper.get("available"):
            return "No live paper report available. Run scripts/run_live_paper_phase.py first."
        stats = live_paper.get("data", {}).get("paper_stats", {})
        drift = live_paper.get("data", {}).get("drift", {})
        return "\n".join(
            [
                "<b>LIVE PAPER STATS</b>",
                "",
                f"PF: {self.html(stats.get('pf', 0.0))}",
                f"WR: {self.html(stats.get('win_rate', 0.0))}%",
                f"Trades: {self.html(stats.get('trades', 0))}",
                f"DD: {self.html(stats.get('max_drawdown', 0.0))}%",
                f"Avg RR: {self.html(stats.get('avg_rr', 0.0))}",
                f"Avg Spread: {self.html(stats.get('avg_spread', 0.0))}",
                f"Avg Slippage: {self.html(stats.get('avg_slippage', 0.0))}",
                f"Avg Latency: {self.html(stats.get('avg_latency', 0.0))}ms",
                "",
                f"Drift: {self.html(drift.get('classification', 'UNKNOWN'))}",
            ]
        )

    def format_live_health(self, live_paper: dict[str, Any]) -> str:
        """Return live feed health status."""
        if not live_paper.get("available"):
            return "No live paper report available. Run scripts/run_live_paper_phase.py first."
        health = live_paper.get("data", {}).get("live_feed_health", {})
        return "\n".join(
            [
                "<b>LIVE FEED HEALTH</b>",
                "",
                f"Score: {self.html(health.get('score', 0.0))}",
                f"Status: {self.html(health.get('classification', 'UNUSABLE'))}",
                f"Missing Candles: {self.html(health.get('missing_candles', 0))}",
                f"Delayed Candles: {self.html(health.get('delayed_candles', 0))}",
                f"Timestamp Issues: {self.html(health.get('inconsistent_timestamps', 0))}",
                f"Feed Interruptions: {self.html(health.get('symbol_feed_interruptions', 0))}",
                f"Spread Anomalies: {self.html(health.get('broker_spread_anomalies', 0))}",
            ]
        )

    def format_live_signals(self, live_paper: dict[str, Any]) -> str:
        """Return recent live signal telemetry."""
        if not live_paper.get("available"):
            return "No live paper report available. Run scripts/run_live_paper_phase.py first."
        trades = live_paper.get("data", {}).get("paper_trades", [])[-5:]
        lines = ["<b>LIVE PAPER SIGNALS</b>", ""]
        for trade in trades:
            lines.append(
                f"{self.html(trade.get('symbol'))}: {self.html(trade.get('micro_regime'))} / "
                f"{self.html(trade.get('strategy'))} / {self.html(trade.get('quality_grade'))} / "
                f"Conf {self.html(trade.get('confidence', 0))}"
            )
        return "\n".join(lines) if trades else "No live paper signals recorded."

    def format_live_mode(self, emergency_live: dict[str, Any]) -> str:
        """Return emergency live deployment mode."""
        if not emergency_live.get("available"):
            return "No emergency live report available. Run scripts/run_emergency_live_protocol.py first."
        data = emergency_live.get("data", {})
        return "\n".join(
            [
                "<b>EMERGENCY LIVE MODE</b>",
                "",
                "Mode: CONTROLLED ASSISTED LIVE",
                f"Status: {self.html(data.get('status', 'UNKNOWN'))}",
                "Autonomous Execution: DISABLED",
                "Broker Order Submission: DISABLED IN SENTINEL",
                "Human Approval: MANDATORY",
            ]
        )

    def format_live_limits(self, emergency_live: dict[str, Any]) -> str:
        """Return emergency live limits."""
        if not emergency_live.get("available"):
            return "No emergency live report available. Run scripts/run_emergency_live_protocol.py first."
        config = emergency_live.get("data", {}).get("config", {})
        return "\n".join(
            [
                "<b>EMERGENCY LIVE LIMITS</b>",
                "",
                f"Risk: {self.html(config.get('risk_percent', 0.1))}%",
                f"Max Risk: {self.html(config.get('max_risk_percent', 0.25))}%",
                f"Symbols: {self.html(', '.join(config.get('allowed_symbols', [])))}",
                f"Grades: {self.html(', '.join(config.get('allowed_grades', [])))}",
                f"Max Trades/Day: {self.html(config.get('max_trades_per_day', 2))}",
            ]
        )

    def format_live_killswitch(self, emergency_live: dict[str, Any]) -> str:
        """Return emergency kill switch status."""
        if not emergency_live.get("available"):
            return "No emergency live report available. Run scripts/run_emergency_live_protocol.py first."
        data = emergency_live.get("data", {})
        kill = data.get("config", {}).get("kill_switch", {})
        return "\n".join(
            [
                "<b>EMERGENCY KILL SWITCH</b>",
                "",
                f"Status: {self.html(data.get('status', 'UNKNOWN'))}",
                f"Reason: {self.html(data.get('halt_reason', '') or 'none')}",
                f"Daily Loss: {self.html(kill.get('daily_loss_r', -1))}R",
                f"Consecutive Losses: {self.html(kill.get('consecutive_losses', 3))}",
                f"Max DD: {self.html(kill.get('max_drawdown_percent', 2))}%",
                "Manual override required to resume after halt.",
            ]
        )

    def format_live_approval_action(self, emergency_live: dict[str, Any], *, action: str) -> str:
        """Return approval command response without executing orders."""
        if not emergency_live.get("available"):
            return "No emergency live report available. Run scripts/run_emergency_live_protocol.py first."
        queue = emergency_live.get("data", {}).get("approval_queue", [])
        pending = next((item for item in queue if item.get("status") == "PENDING" and self.approval_item_is_production_eligible(item)), None)
        if not pending:
            return f"{action} unavailable: no pending emergency-live approval request."
        return "\n".join(
            [
                f"<b>{self.html(action)} TRADE</b>",
                "",
                f"Approval ID: {self.html(pending.get('approval_id'))}",
                f"Symbol: {self.html(pending.get('proposal', {}).get('symbol', 'UNKNOWN'))}",
                "Human operator must execute outside Sentinel after manual confirmation.",
                "Sentinel broker order submission remains disabled.",
            ]
        )

    def approval_item_is_production_eligible(self, item: dict[str, Any]) -> bool:
        """Return whether an approval item is display-eligible for live approval commands."""
        symbol = str(item.get("proposal", {}).get("symbol", "")).upper().strip()
        validation = item.get("validation", {})
        return (
            self.symbol_registry.execution_allowed(symbol)
            and bool(validation.get("valid", True))
            and not bool(validation.get("broker_order_submission_allowed", False))
        )

    def format_live_control_action(self, emergency_live: dict[str, Any], *, action: str) -> str:
        """Return halt/resume command guidance without bypassing manual controls."""
        if not emergency_live.get("available"):
            return "No emergency live report available. Run scripts/run_emergency_live_protocol.py first."
        data = emergency_live.get("data", {})
        if action == "HALT":
            return "LIVE HALT requested. Set status LIVE_HALTED in emergency protocol and stop all assisted live proposals."
        return "\n".join(
            [
                "LIVE RESUME requested.",
                f"Current Status: {self.html(data.get('status', 'UNKNOWN'))}",
                "Manual override required before proposals resume.",
            ]
        )

    def format_challenge_status(self, challenge: dict[str, Any]) -> str:
        """Return Challenge Command Center status."""
        data = self.challenge_data_or_none(challenge)
        if data is None:
            return "No Challenge Command Center report available. Run scripts/run_challenge_command_center.py first."
        status = data.get("challenge_status", {})
        return "\n".join(
            [
                "<b>CHALLENGE STATUS</b>",
                "",
                f"Challenge Mode: {self.html(status.get('challenge_mode', 'DISABLED'))}",
                f"Profile: {self.html(status.get('profile', 'BALANCED'))}",
                f"Current Phase: {self.html(status.get('current_phase', 'PHASE_1'))}",
                f"Status: {self.html(status.get('status', 'PAUSED'))}",
                "Broker Orders: DISABLED",
                "Autonomous Execution: DISABLED",
            ]
        )

    def format_challenge_progress(self, challenge: dict[str, Any]) -> str:
        """Return challenge profit progress."""
        data = self.challenge_data_or_none(challenge)
        if data is None:
            return "No Challenge Command Center report available. Run scripts/run_challenge_command_center.py first."
        progress = data.get("profit_progress", {})
        return "\n".join(
            [
                "<b>CHALLENGE PROGRESS</b>",
                "",
                f"Starting Balance: {self.html(progress.get('starting_balance', 0.0))}",
                f"Current Balance: {self.html(progress.get('current_balance', 0.0))}",
                f"Current Equity: {self.html(progress.get('current_equity', 0.0))}",
                f"Net PnL: {self.html(progress.get('net_pnl', 0.0))} ({self.html(progress.get('net_pnl_percent', 0.0))}%)",
                f"Target Progress: {self.html(progress.get('progress_percent', 0.0))}%",
                f"Remaining Target: {self.html(progress.get('remaining_target', 0.0))} ({self.html(progress.get('remaining_target_percent', 0.0))}%)",
            ]
        )

    def format_challenge_risk(self, challenge: dict[str, Any]) -> str:
        """Return challenge risk buffer."""
        data = self.challenge_data_or_none(challenge)
        if data is None:
            return "No Challenge Command Center report available. Run scripts/run_challenge_command_center.py first."
        risk = data.get("risk_buffer", {})
        daily = risk.get("daily_loss_limit", {})
        total = risk.get("total_drawdown_limit", {})
        return "\n".join(
            [
                "<b>CHALLENGE RISK BUFFER</b>",
                "",
                f"State: {self.html(risk.get('color_state', 'SAFE'))}",
                f"Daily Limit: {self.html(daily.get('max_allowed_percent', 5))}%",
                f"Daily Used: {self.html(daily.get('current_used_percent', 0.0))}%",
                f"Daily Remaining: {self.html(daily.get('remaining_buffer_percent', 5.0))}%",
                f"Max Loss Limit: {self.html(total.get('max_allowed_percent', 10))}%",
                f"Total DD Used: {self.html(total.get('current_used_percent', 0.0))}%",
                f"Total DD Remaining: {self.html(total.get('remaining_buffer_percent', 10.0))}%",
            ]
        )

    def format_challenge_phase(self, challenge: dict[str, Any]) -> str:
        """Return current challenge phase."""
        data = self.challenge_data_or_none(challenge)
        if data is None:
            return "No Challenge Command Center report available. Run scripts/run_challenge_command_center.py first."
        status = data.get("challenge_status", {})
        progress = data.get("profit_progress", {})
        return "\n".join(
            [
                "<b>CHALLENGE PHASE</b>",
                "",
                f"Current Phase: {self.html(status.get('current_phase', 'PHASE_1'))}",
                f"Phase Target: {self.html(progress.get('target_percent', 10.0))}%",
                f"Progress: {self.html(progress.get('progress_percent', 0.0))}%",
                f"Remaining: {self.html(progress.get('remaining_target_percent', 0.0))}%",
            ]
        )

    def format_challenge_governor(self, challenge: dict[str, Any]) -> str:
        """Return challenge governor state."""
        data = self.challenge_data_or_none(challenge)
        if data is None:
            return "No Challenge Command Center report available. Run scripts/run_challenge_command_center.py first."
        governor = data.get("governor_status", {})
        return "\n".join(
            [
                "<b>CHALLENGE GOVERNOR</b>",
                "",
                f"Soft Stop: {self.html(governor.get('soft_stop_percent', 2))}%",
                f"Hard Stop: {self.html(governor.get('hard_stop_percent', 3))}%",
                f"Loss Streak: {self.html(governor.get('loss_streak', 0))}",
                f"Risk Mode: {self.html(governor.get('risk_mode', 'NORMAL'))}",
                f"Current Risk: {self.html(governor.get('current_risk_percent', 0.8))}%",
                f"Alerts: {self.html(', '.join(governor.get('alerts', [])) or 'none')}",
            ]
        )

    def format_challenge_recommendation(self, challenge: dict[str, Any]) -> str:
        """Return advisory challenge recommendation."""
        data = self.challenge_data_or_none(challenge)
        if data is None:
            return "No Challenge Command Center report available. Run scripts/run_challenge_command_center.py first."
        recommendation = data.get("recommendation", {})
        return "\n".join(
            [
                "<b>CHALLENGE RECOMMENDATION</b>",
                "",
                f"Recommendation: {self.html(recommendation.get('recommendation', 'No recommendation available.'))}",
                f"Confidence: {self.html(recommendation.get('confidence', 'LOW'))}",
                f"Rationale: {self.html(recommendation.get('rationale', 'Advisory only.'))}",
            ]
        )

    def format_challenge_activation_action(self, challenge: dict[str, Any], *, action: str) -> str:
        """Return confirmation-only activation/deactivation guidance."""
        data = self.challenge_data_or_none(challenge)
        status = data.get("challenge_status", {}) if data else {}
        return "\n".join(
            [
                f"<b>CHALLENGE {self.html(action)}</b>",
                "",
                "Confirmation-only command.",
                "No challenge mode state was changed.",
                "No broker orders can be placed by Sentinel.",
                "Autonomous execution remains disabled.",
                f"Current Challenge Mode: {self.html(status.get('challenge_mode', 'DISABLED'))}",
            ]
        )

    @staticmethod
    def challenge_data_or_none(challenge: dict[str, Any]) -> dict[str, Any] | None:
        """Return challenge report data when available."""
        if not challenge.get("available"):
            return None
        return challenge.get("data", {})

    @staticmethod
    def sandbox_data_or_none(sandbox: dict[str, Any]) -> dict[str, Any] | None:
        """Return sandbox report data when available."""
        if not sandbox.get("available"):
            return None
        return sandbox.get("data", {})

    def format_sandbox_status(self, sandbox_summary: dict[str, Any]) -> str:
        """Return demo sandbox status."""
        data = self.sandbox_data_or_none(sandbox_summary)
        if data is None:
            return "No demo sandbox report available. Run scripts/run_demo_sandbox_status.py first."
        sandbox = data.get("sandbox", {})
        integration = data.get("assisted_integration", {})
        return "\n".join(
            [
                "<b>DEMO SANDBOX STATUS</b>",
                "",
                sandbox_banner(),
                "",
                f"Enabled: {self.html(str(bool(sandbox.get('enabled', False))).upper())}",
                f"Mode: {self.html(sandbox.get('mode', 'DEMO_ONLY'))}",
                f"Allowed Symbols: {self.html(', '.join(sandbox.get('allowed_symbols', [])))}",
                f"Allowed Grades: {self.html(', '.join(sandbox.get('allowed_grades', [])))}",
                f"Default Risk: {self.html(sandbox.get('default_risk_percent', 0.05))}%",
                f"Max Risk: {self.html(sandbox.get('max_risk_percent', 0.10))}%",
                f"Submit Orders: {self.html(str(bool(sandbox.get('submit_orders', False))).upper())}",
                f"Dry Run Only: {self.html(str(bool(integration.get('dry_run_only', True))).upper())}",
                f"Production Excluded: {self.html(str(bool(sandbox.get('production_metrics_excluded', True))).upper())}",
                f"Challenge Mode: {self.html('BLOCKED' if not sandbox.get('challenge_mode_allowed', False) else 'REVIEW')}",
            ]
        )

    def format_sandbox_symbols(self, sandbox_summary: dict[str, Any]) -> str:
        """Return sandbox symbol tiers."""
        data = self.sandbox_data_or_none(sandbox_summary)
        if data is None:
            return "No demo sandbox report available. Run scripts/run_demo_sandbox_status.py first."
        tiers = data.get("symbol_tiers", {})
        return "\n".join(
            [
                "<b>DEMO SANDBOX SYMBOLS</b>",
                "",
                sandbox_banner(),
                "",
                f"Production: {self.html(', '.join(tiers.get('production', [])))}",
                f"Demo Sandbox: {self.html(', '.join(tiers.get('demo_sandbox', [])))}",
                f"Observer Only: {self.html(', '.join(tiers.get('observer_only', [])))}",
            ]
        )

    def format_sandbox_ticket(self, sandbox_summary: dict[str, Any], ticket_id: str | None = None) -> str:
        """Return current sandbox ticket."""
        data = self.sandbox_data_or_none(sandbox_summary)
        if data is None:
            return "No demo sandbox report available. Run scripts/run_demo_sandbox_status.py first."
        ticket = data.get("current_ticket", {})
        if ticket_id and str(ticket.get("ticket_id", "")).upper() != ticket_id:
            return f"Sandbox ticket {self.html(ticket_id)} unavailable in current report."
        return "\n".join(
            [
                "<b>SANDBOX TICKET</b>",
                "",
                sandbox_banner(),
                "",
                f"Ticket ID: {self.html(ticket.get('ticket_id', 'none'))}",
                f"Ticket Type: {self.html(ticket.get('ticket_type', 'SANDBOX_DEMO'))}",
                f"Status: {self.html(ticket.get('status', 'UNKNOWN'))}",
                f"Symbol: {self.html(ticket.get('symbol', 'UNKNOWN'))}",
                f"Side: {self.html(ticket.get('side', 'UNKNOWN'))}",
                f"Entry: {self.html(ticket.get('entry_price', 0.0))}",
                f"SL: {self.html(ticket.get('stop_loss', 0.0))}",
                f"TP: {self.html(ticket.get('take_profit', 0.0))}",
                f"Risk: {self.html(ticket.get('risk_percent', 0.0))}%",
                f"Grade: {self.html(ticket.get('grade', 'UNKNOWN'))}",
                f"Approval: /sandbox_approve {self.html(ticket.get('ticket_id', 'none'))}",
            ]
        )

    def format_sandbox_dry_run(self, sandbox_summary: dict[str, Any], ticket_id: str | None = None) -> str:
        """Return sandbox dry-run payload."""
        data = self.sandbox_data_or_none(sandbox_summary)
        if data is None:
            return "No demo sandbox report available. Run scripts/run_demo_sandbox_status.py first."
        ticket = data.get("current_ticket", {})
        if ticket_id and str(ticket.get("ticket_id", "")).upper() != ticket_id:
            return f"Sandbox ticket {self.html(ticket_id)} unavailable in current report."
        dry_run = data.get("dry_run", {})
        validation = dry_run.get("validation", {})
        return "\n".join(
            [
                "<b>SANDBOX DRY RUN</b>",
                "",
                sandbox_banner(),
                "",
                f"Ticket ID: {self.html(dry_run.get('ticket_id', ticket.get('ticket_id', 'none')))}",
                f"Symbol: {self.html(dry_run.get('symbol', ticket.get('symbol', 'UNKNOWN')))}",
                f"Entry: {self.html(dry_run.get('entry', 0.0))}",
                f"SL: {self.html(dry_run.get('sl', 0.0))}",
                f"TP: {self.html(dry_run.get('tp', 0.0))}",
                f"Lot Size: {self.html(dry_run.get('lot_size', 0.0))}",
                f"Risk %: {self.html(dry_run.get('risk_percent', 0.0))}",
                f"Validation: {self.html(validation.get('status', 'BLOCKED'))}",
                "Order Send: NOT CALLED",
            ]
        )

    def format_sandbox_approve(self, sandbox_summary: dict[str, Any], ticket_id: str | None = None) -> str:
        """Approve a sandbox ticket through demo-only sandbox gates."""
        data = self.sandbox_data_or_none(sandbox_summary)
        if data is None:
            return "No demo sandbox report available. Run scripts/run_demo_sandbox_status.py first."
        if not ticket_id:
            return self.format_sandbox_approval_result("INVALID_TICKET", {}, False, ["ticket_id is required"])
        ticket_payload = data.get("current_ticket", {})
        if str(ticket_payload.get("ticket_id", "")).upper() != ticket_id:
            return self.format_sandbox_approval_result("INVALID_TICKET", {"ticket_id": ticket_id}, False, ["ticket_id not found"])
        sandbox = data.get("sandbox", {})
        config = {
            "enabled": bool(sandbox.get("enabled", False)),
            "mode": sandbox.get("mode", "DEMO_ONLY"),
            "allowed_symbols": sandbox.get("allowed_symbols", []),
            "allowed_grades": sandbox.get("allowed_grades", []),
            "default_risk_percent": sandbox.get("default_risk_percent", 0.05),
            "max_risk_percent": sandbox.get("max_risk_percent", 0.10),
            "human_approval_required": True,
            "submit_orders": bool(sandbox.get("submit_orders", False)),
            "production_metrics_excluded": bool(sandbox.get("production_metrics_excluded", True)),
            "challenge_mode_allowed": bool(sandbox.get("challenge_mode_allowed", False)),
        }
        engine = DemoSandboxEngine(connector=self.connector, config_dir=self.config_dir, config=config)
        try:
            ticket = engine.create_ticket(**ticket_payload)
        except Exception as exc:
            return self.format_sandbox_approval_result("INVALID_TICKET", ticket_payload, bool(config["submit_orders"]), [f"invalid sandbox ticket: {exc}"])
        approved = engine.transition_ticket(ticket, "APPROVED")
        context = self.sandbox_approval_context(data)
        submit_orders = bool(config.get("submit_orders", False))
        if not submit_orders:
            dry_run = engine.dry_run(approved, context=context)
            validation = dry_run.get("validation", {})
            decision = "APPROVED_DRY_RUN" if validation.get("passed", False) else "BLOCKED"
            reasons = ["submit_orders is false: dry-run only"] if validation.get("passed", False) else validation.get("reasons", [])
            return self.format_sandbox_approval_result(decision, approved.to_dict(), submit_orders, reasons, dry_run.get("order_payload", {}))
        result = engine.submit_demo_order(approved, context=context, human_approved=True)
        decision = "SUBMITTED_DEMO" if result.get("status") == "SUBMITTED_DEMO" else "BLOCKED"
        reasons = result.get("validation", {}).get("reasons", [])
        if result.get("reason"):
            reasons = [result["reason"], *reasons]
        return self.format_sandbox_approval_result(decision, approved.to_dict(), submit_orders, reasons, result.get("order_payload", {}))

    def format_sandbox_disable(self, sandbox_summary: dict[str, Any]) -> str:
        """Return sandbox disable acknowledgement without editing config."""
        data = self.sandbox_data_or_none(sandbox_summary)
        enabled = bool(data.get("sandbox", {}).get("enabled", False)) if data else False
        return "\n".join(
            [
                "<b>SANDBOX DISABLE</b>",
                "",
                sandbox_banner(),
                "",
                f"Current Enabled State: {self.html(str(enabled).upper())}",
                "No config changed from Telegram.",
                "Sandbox remains controlled by config/demo_sandbox.yaml.",
            ]
        )

    def format_sandbox_approval_result(
        self,
        decision: str,
        ticket: dict[str, Any],
        submit_orders: bool,
        reasons: list[Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Return Telegram-safe sandbox approval result."""
        reasons = [str(reason) for reason in (reasons or []) if reason]
        payload = payload or {}
        lines = [
            "<b>SANDBOX APPROVAL RESULT</b>",
            "",
            sandbox_banner(),
            "",
            f"Ticket ID: {self.html(ticket.get('ticket_id', 'none'))}",
            f"Symbol: {self.html(ticket.get('symbol', 'UNKNOWN'))}",
            f"Side: {self.html(ticket.get('side', 'UNKNOWN'))}",
            f"Entry: {self.html(ticket.get('entry_price', 0.0))}",
            f"SL: {self.html(ticket.get('stop_loss', 0.0))}",
            f"TP: {self.html(ticket.get('take_profit', 0.0))}",
            f"Risk %: {self.html(ticket.get('risk_percent', 0.0))}",
            f"Submit Orders: {self.html(str(submit_orders).upper())}",
            f"Final Decision: {self.html(decision)}",
            f"Order Send: {self.html('CALLED' if decision == 'SUBMITTED_DEMO' and submit_orders else 'NOT CALLED')}",
            "Production Metrics: EXCLUDED",
        ]
        if reasons:
            lines.append(f"Reasons: {self.html('; '.join(dict.fromkeys(reasons)))}")
        if payload:
            lines.extend(["", "<b>Order Payload</b>"])
            for key, value in payload.items():
                lines.append(f"{self.html(key)}: {self.html(value)}")
        return "\n".join(lines)

    def sandbox_approval_context(self, data: dict[str, Any]) -> dict[str, Any]:
        """Build sandbox approval context from cached report data."""
        dry_run = data.get("dry_run", {})
        validation = dry_run.get("validation", {})
        context: dict[str, Any] = {
            "spread_points": validation.get("spread_points", 0.0),
            "slippage_points": validation.get("slippage_points", 0.0),
            "kill_switch_active": bool(data.get("kill_switch_active", False)),
            "challenge_mode_active": bool(data.get("challenge_mode_active", False)),
        }
        if self.connector is None:
            context["account"] = {"account_mode": validation.get("account_mode", "DEMO").lower(), "server": "MetaQuotes-Demo", "balance": 10000.0}
        return context

    def format_assisted_status(self, assisted: dict[str, Any]) -> str:
        """Return demo assisted execution bridge status."""
        data = self.assisted_data_or_none(assisted)
        if data is None:
            return "No assisted execution report available. Run scripts/run_assisted_execution_status.py first."
        validation = data.get("final_safety_status", {})
        safety = data.get("safety", {})
        broker_state = "DEMO GATED" if safety.get("broker_orders", False) else "BLOCKED"
        return "\n".join(
            [
                "<b>ASSISTED EXECUTION STATUS</b>",
                "",
                f"Assisted Execution: {self.html(data.get('assisted_execution', 'DISABLED'))}",
                f"Mode: {self.html(data.get('mode', 'DEMO_ONLY'))}",
                f"Account Mode: {self.html(data.get('account_mode', 'UNKNOWN'))}",
                f"Final Safety: {self.html(validation.get('status', 'BLOCKED'))}",
                f"Dry Run Only: {self.html(str(bool(safety.get('dry_run_only', True))).upper())}",
                f"Submit Orders: {self.html(str(bool(safety.get('broker_orders', False))).upper())}",
                f"Broker Orders: {self.html(broker_state)}",
                "Autonomous Execution: DISABLED",
                "Approval Command: /exec_approve &lt;ticket_id&gt;",
                "Alias: /execute_approve &lt;ticket_id&gt;",
            ]
        )

    def format_assisted_ticket(self, assisted: dict[str, Any], ticket_id: str | None = None) -> str:
        """Return current assisted execution ticket."""
        data = self.assisted_data_or_none(assisted)
        if data is None:
            return "No assisted execution report available. Run scripts/run_assisted_execution_status.py first."
        ticket = data.get("current_ticket", {})
        if ticket_id and str(ticket.get("ticket_id", "")).upper() != ticket_id:
            return f"Ticket {self.html(ticket_id)} unavailable in current assisted execution report."
        return "\n".join(
            [
                "<b>ASSISTED TICKET</b>",
                "",
                f"Ticket ID: {self.html(ticket.get('ticket_id', 'none'))}",
                f"Status: {self.html(ticket.get('status', 'UNKNOWN'))}",
                f"Symbol: {self.html(ticket.get('symbol', 'UNKNOWN'))}",
                f"Side: {self.html(ticket.get('side', 'UNKNOWN'))}",
                f"Entry Type: {self.html(ticket.get('entry_type', 'UNKNOWN'))}",
                f"Entry: {self.html(ticket.get('entry_price', 0.0))}",
                f"SL: {self.html(ticket.get('stop_loss', 0.0))}",
                f"TP: {self.html(ticket.get('take_profit', 0.0))}",
                f"Risk: {self.html(ticket.get('risk_percent', 0.0))}%",
                f"Lot: {self.html(ticket.get('lot_size', 0.0))}",
                f"Expires: {self.html(ticket.get('expires_at', 'unknown'))}",
                f"Approval Command: /exec_approve {self.html(ticket.get('ticket_id', 'none'))}",
            ]
        )

    def format_assisted_dry_run(self, assisted: dict[str, Any], ticket_id: str | None = None) -> str:
        """Return dry-run order payload without sending."""
        data = self.assisted_data_or_none(assisted)
        if data is None:
            return "No assisted execution report available. Run scripts/run_assisted_execution_status.py first."
        ticket = data.get("current_ticket", {})
        if ticket_id and str(ticket.get("ticket_id", "")).upper() != ticket_id:
            return f"Ticket {self.html(ticket_id)} unavailable in current assisted execution report."
        dry_run = data.get("dry_run", {})
        validation = dry_run.get("validation", {})
        return "\n".join(
            [
                "<b>ASSISTED DRY RUN</b>",
                "",
                "Mode: DEMO_ONLY",
                f"Symbol: {self.html(dry_run.get('symbol', 'UNKNOWN'))}",
                f"Order Type: {self.html(dry_run.get('order_type', 'UNKNOWN'))}",
                f"Lot Size: {self.html(dry_run.get('lot_size', 0.0))}",
                f"Entry: {self.html(dry_run.get('entry', 0.0))}",
                f"SL: {self.html(dry_run.get('sl', 0.0))}",
                f"TP: {self.html(dry_run.get('tp', 0.0))}",
                f"Risk Amount: {self.html(dry_run.get('risk_amount', 0.0))}",
                f"Expected Max Loss: {self.html(dry_run.get('expected_max_loss', 0.0))}",
                f"Validation: {self.html(validation.get('status', 'BLOCKED'))}",
                "Order Send: NOT CALLED",
            ]
        )

    def format_execution_approval_command(self, assisted: dict[str, Any], ticket_id: str | None = None, *, command: str) -> str:
        """Approve an assisted ticket through the demo-only bridge gates."""
        data = self.assisted_data_or_none(assisted)
        if data is None:
            return "No assisted execution report available. Run scripts/run_assisted_execution_status.py first."
        if not ticket_id:
            return self.format_execution_approval_result(
                decision="INVALID_TICKET",
                ticket={},
                mode="DEMO_ONLY",
                submit_orders=False,
                reasons=["ticket_id is required"],
                command=command,
            )
        ticket_payload = self.find_assisted_ticket_payload(data, ticket_id)
        if ticket_payload is None:
            return self.format_execution_approval_result(
                decision="INVALID_TICKET",
                ticket={"ticket_id": ticket_id},
                mode=str(data.get("mode", "DEMO_ONLY")),
                submit_orders=bool(data.get("config", {}).get("submit_orders", False)),
                reasons=["ticket_id not found"],
                command=command,
            )
        config = data.get("config", {})
        bridge = AssistedExecutionBridge(connector=self.connector, config_dir=self.config_dir, config=config)
        try:
            ticket = bridge.create_ticket(**ticket_payload)
        except Exception as exc:
            return self.format_execution_approval_result(
                decision="INVALID_TICKET",
                ticket=ticket_payload,
                mode=str(config.get("mode", data.get("mode", "DEMO_ONLY"))),
                submit_orders=bool(config.get("submit_orders", False)),
                reasons=[f"invalid locked ticket payload: {exc}"],
                command=command,
            )
        if ticket.status != "AWAITING_APPROVAL":
            return self.format_execution_approval_result(
                decision="BLOCKED",
                ticket=ticket.to_dict(),
                mode=str(config.get("mode", data.get("mode", "DEMO_ONLY"))),
                submit_orders=bool(config.get("submit_orders", False)),
                reasons=["ticket status must be AWAITING_APPROVAL"],
                command=command,
            )
        now = self.execution_approval_now(data)
        if now >= parse_datetime(ticket.expires_at):
            return self.format_execution_approval_result(
                decision="EXPIRED",
                ticket=ticket.to_dict(),
                mode=str(config.get("mode", data.get("mode", "DEMO_ONLY"))),
                submit_orders=bool(config.get("submit_orders", False)),
                reasons=["ticket expired"],
                command=command,
            )
        context = self.execution_approval_context(data, ticket)
        approved_ticket = bridge.transition_ticket(ticket, "APPROVED")
        submit_orders = bool(config.get("submit_orders", False))
        if not submit_orders:
            dry_run = bridge.dry_run(approved_ticket, context=context)
            validation = dry_run.get("validation", {})
            if not validation.get("passed", False):
                return self.format_execution_approval_result(
                    decision="BLOCKED",
                    ticket=ticket.to_dict(),
                    mode=str(config.get("mode", data.get("mode", "DEMO_ONLY"))),
                    submit_orders=submit_orders,
                    reasons=validation.get("reasons", ["final gate blocked"]),
                    command=command,
                    payload=dry_run.get("order_payload", {}),
                )
            return self.format_execution_approval_result(
                decision="APPROVED_DRY_RUN",
                ticket=approved_ticket.to_dict(),
                mode=str(config.get("mode", data.get("mode", "DEMO_ONLY"))),
                submit_orders=submit_orders,
                reasons=["submit_orders is false: dry-run only"],
                command=command,
                payload=dry_run.get("order_payload", {}),
            )
        submit_result = bridge.submit_demo_order(approved_ticket, context=context, human_approved=True)
        decision = "SUBMITTED_DEMO" if submit_result.get("status") == "SUBMITTED_DEMO" else "BLOCKED"
        reasons = submit_result.get("validation", {}).get("reasons", [])
        if submit_result.get("reason"):
            reasons = [submit_result["reason"], *reasons]
        return self.format_execution_approval_result(
            decision=decision,
            ticket=approved_ticket.to_dict(),
            mode=str(config.get("mode", data.get("mode", "DEMO_ONLY"))),
            submit_orders=submit_orders,
            reasons=reasons,
            command=command,
            payload=submit_result.get("order_payload", {}),
        )

    def format_execution_approval_result(
        self,
        *,
        decision: str,
        ticket: dict[str, Any],
        mode: str,
        submit_orders: bool,
        reasons: list[Any] | None = None,
        command: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Return Telegram-safe execution approval result text."""
        payload = payload or {}
        reasons = [str(reason) for reason in (reasons or []) if reason]
        lines = [
            "<b>EXECUTION APPROVAL RESULT</b>",
            "",
            f"Command: {self.html(command)}",
            f"Ticket ID: {self.html(ticket.get('ticket_id', 'none'))}",
            f"Symbol: {self.html(ticket.get('symbol', 'UNKNOWN'))}",
            f"Side: {self.html(ticket.get('side', 'UNKNOWN'))}",
            f"Entry: {self.html(ticket.get('entry_price', 0.0))}",
            f"SL: {self.html(ticket.get('stop_loss', 0.0))}",
            f"TP: {self.html(ticket.get('take_profit', 0.0))}",
            f"Lot Size: {self.html(ticket.get('lot_size', 0.0))}",
            f"Risk %: {self.html(ticket.get('risk_percent', 0.0))}",
            f"Mode: {self.html(mode)}",
            f"Submit Orders: {self.html(str(submit_orders).upper())}",
            f"Final Decision: {self.html(decision)}",
            f"Order Send: {self.html('CALLED' if decision == 'SUBMITTED_DEMO' and submit_orders else 'NOT CALLED')}",
            "Production Baseline Preserved: True",
            "No live-funded execution.",
            "Autonomous Execution: DISABLED",
        ]
        if reasons:
            lines.append(f"Reasons: {self.html('; '.join(dict.fromkeys(reasons)))}")
        if payload:
            lines.extend(["", "<b>Order Payload</b>"])
            for key, value in payload.items():
                lines.append(f"{self.html(key)}: {self.html(value)}")
        return "\n".join(lines)

    @staticmethod
    def find_assisted_ticket_payload(data: dict[str, Any], ticket_id: str) -> dict[str, Any] | None:
        """Find a locked assisted ticket payload in the report."""
        target = str(ticket_id or "").upper().strip()
        candidates: list[dict[str, Any]] = []
        current = data.get("current_ticket")
        if isinstance(current, dict):
            candidates.append(current)
        for key in ("tickets", "assisted_tickets", "approval_queue"):
            value = data.get(key, [])
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))
        for item in candidates:
            payload = item.get("ticket", item)
            if isinstance(payload, dict) and str(payload.get("ticket_id", "")).upper().strip() == target:
                return dict(payload)
        return None

    def execution_approval_context(self, data: dict[str, Any], ticket: LockedTradeTicket) -> dict[str, Any]:
        """Build final-gate context from report telemetry without starting services."""
        validation = data.get("final_safety_status", {})
        context: dict[str, Any] = {
            "spread_points": validation.get("spread_points", 0.0),
            "slippage_points": validation.get("slippage_points", 0.0),
            "expected_lot_size": ticket.lot_size,
            "kill_switch_active": bool(data.get("kill_switch_active", False)),
            "now": self.execution_approval_now(data).isoformat(),
        }
        if self.connector is None:
            account_mode = str(data.get("account_mode", validation.get("account_mode", "UNKNOWN"))).lower()
            context["account"] = {
                "account_mode": account_mode,
                "server": "MetaQuotes-Demo" if account_mode == "demo" else account_mode,
                "balance": data.get("balance", 10000.0),
            }
        return context

    @staticmethod
    def execution_approval_now(data: dict[str, Any]) -> Any:
        """Return report-supplied current time or UTC now for ticket freshness checks."""
        from datetime import UTC, datetime

        value = data.get("now") or data.get("approval_now")
        return parse_datetime(value) if value else datetime.now(UTC)

    def format_assisted_approval_action(self, assisted: dict[str, Any], ticket_id: str | None = None, *, action: str) -> str:
        """Return assisted approval/rejection response without submitting from Telegram."""
        data = self.assisted_data_or_none(assisted)
        if data is None:
            return "No assisted execution report available. Run scripts/run_assisted_execution_status.py first."
        ticket = data.get("current_ticket", {})
        if ticket_id and str(ticket.get("ticket_id", "")).upper() != ticket_id:
            return f"{self.html(action)} rejected: ticket {self.html(ticket_id)} unavailable."
        if action == "REJECT":
            return "\n".join(
                [
                    "<b>ASSISTED REJECT</b>",
                    "",
                    f"Ticket ID: {self.html(ticket.get('ticket_id', 'none'))}",
                    "Rejected by human operator. No broker order submitted.",
                ]
            )
        validation = data.get("final_safety_status", {})
        config = data.get("config", {})
        demo_confirmed = str(data.get("account_mode", "")).upper() == "DEMO"
        enabled = bool(config.get("enabled", False))
        submit_enabled = bool(config.get("submit_orders", False))
        gate_passed = bool(validation.get("passed", False))
        if not (enabled and demo_confirmed and gate_passed and submit_enabled):
            reasons = validation.get("reasons", [])
            if not enabled:
                reasons = ["assisted_execution.enabled is false", *reasons]
            if not demo_confirmed:
                reasons = ["MT5 demo account not confirmed", *reasons]
            if not submit_enabled:
                reasons = ["submit_orders is false: dry-run only", *reasons]
            return "\n".join(
                [
                    "<b>ASSISTED APPROVE BLOCKED</b>",
                    "",
                    "Mode: DEMO_ONLY",
                    "Dry Run Only: TRUE",
                    f"Ticket ID: {self.html(ticket.get('ticket_id', 'none'))}",
                    "No broker order submitted.",
                    f"Reasons: {self.html('; '.join(dict.fromkeys(reasons)) or 'final gate blocked')}",
                ]
            )
        return "\n".join(
            [
                "<b>ASSISTED APPROVE READY</b>",
                "",
                f"Ticket ID: {self.html(ticket.get('ticket_id', 'none'))}",
                "Demo account confirmed and final gate passed.",
                "Use the explicit demo bridge submit path; Telegram status formatter does not place broker orders.",
            ]
        )

    @staticmethod
    def assisted_data_or_none(assisted: dict[str, Any]) -> dict[str, Any] | None:
        """Return assisted report data when available."""
        if not assisted.get("available"):
            return None
        return assisted.get("data", {})

    @staticmethod
    def symbol_metrics_from_backtest(backtest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Extract per-symbol metrics from cached backtest summaries."""
        if not backtest.get("available"):
            return {}
        data = backtest.get("data", {})
        metrics_by_symbol: dict[str, dict[str, Any]] = {}
        for candidate in (
            data.get("production_portfolio", {}).get("symbol_breakdown", {}),
            data.get("symbol_breakdown", {}),
            data.get("by_symbol", {}),
            data.get("days_365", {}).get("by_symbol", {}) if isinstance(data.get("days_365"), dict) else {},
        ):
            if candidate:
                metrics_by_symbol.update({str(symbol).upper(): metrics for symbol, metrics in candidate.items()})
                break
        for symbol, diagnostics in data.get("observer_diagnostics", {}).items():
            symbol_key = str(symbol).upper()
            metrics = diagnostics.get("metrics", diagnostics) if isinstance(diagnostics, dict) else {}
            metrics_by_symbol[symbol_key] = metrics
        return metrics_by_symbol

    @staticmethod
    def symbol_mode_label(data: dict[str, Any]) -> str:
        """Return Telegram display mode for a symbol snapshot."""
        if data.get("mode") == DEMO_SANDBOX_LABEL:
            return DEMO_SANDBOX_LABEL
        if data.get("experimental") or data.get("observer") or str(data.get("plan_quality", "")).lower() == "observer_only":
            return OBSERVER_ONLY_LABEL
        return "Advisor"

    @staticmethod
    def extract_adaptive_backtest_metrics(data: dict[str, Any], days: str) -> dict[str, Any]:
        """Extract adaptive long-horizon metrics from common cache shapes."""
        candidates = [
            data.get(days, {}).get("adaptive_guardrails", {}).get("overall", {}),
            data.get(int(days), {}).get("adaptive_guardrails", {}).get("overall", {}) if days.isdigit() else {},
            data.get(f"{days}_day", {}).get("adaptive_guardrails", {}).get("overall", {}),
            data.get(f"{days}d", {}).get("adaptive_guardrails", {}).get("overall", {}),
            data.get("long_horizon", {}).get(days, {}).get("adaptive_guardrails", {}).get("overall", {}),
            data.get("adaptive_guardrails", {}).get(days, {}).get("overall", {}),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
        return {}

    @staticmethod
    def phase_decision(metrics: dict[str, Any]) -> str:
        """Return Phase 3 decision text from 90-day adaptive metrics."""
        if (
            float(metrics.get("profit_factor", 0.0) or 0.0) > 1.5
            and float(metrics.get("win_rate", 0.0) or 0.0) > 50.0
            and float(metrics.get("max_drawdown", 0.0) or 0.0) < 6.0
        ):
            return "Sentinel qualifies for Phase 3: Execution Automation Research"
        if metrics:
            return "Continue optimization."
        return "No phase decision available."

    @classmethod
    def sanitize_snapshot(cls, value: Any) -> Any:
        """Remove sensitive fields before formatting."""
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                if str(key).lower() in cls.SENSITIVE_KEYS:
                    continue
                sanitized[key] = cls.sanitize_snapshot(item)
            return sanitized
        if isinstance(value, list):
            return [cls.sanitize_snapshot(item) for item in value]
        return value

    @staticmethod
    def normalize_command(command: str) -> str:
        """Normalize Telegram command text."""
        value = str(command or "").strip().split()[0].lower()
        if "@" in value:
            value = value.split("@", 1)[0]
        return value

    @staticmethod
    def format_smt(smt: dict[str, Any] | str) -> str:
        """Return compact SMT text."""
        if isinstance(smt, str):
            return smt or "none"
        if not smt or not bool(smt.get("smt_detected", False)):
            return "none"
        direction = smt.get("direction", "divergence")
        pair = smt.get("pair_name", "")
        return f"{direction} {pair}".strip()

    @staticmethod
    def format_list(values: list[Any]) -> str:
        """Return comma-separated display text."""
        return ", ".join(str(value) for value in values if value) if values else "none"

    @staticmethod
    def display_killzone(name: Any) -> str:
        """Return readable killzone name."""
        value = str(name or "none")
        if value == "none":
            return "None"
        return KillzoneAnalyzer.display_name(value)

    @staticmethod
    def html(value: Any) -> str:
        """HTML-escape display values."""
        return escape(str(value), quote=False)

    @staticmethod
    def title_text(value: Any) -> str:
        """Return readable title text for internal labels."""
        return str(value or "").replace("_", " ").title()

    def truncate_response(self, response: str) -> str:
        """Keep Telegram output below configured limits."""
        maximum = int(self.config.get("max_message_chars", 3800))
        if len(response) <= maximum:
            return response
        suffix = "\n\nOutput truncated. Use specific command."
        return response[: max(0, maximum - len(suffix))].rstrip() + suffix

    def telegram_url(self, method: str, *, token: str) -> str:
        """Build Telegram API URL without logging it."""
        return self.TELEGRAM_API_TEMPLATE.format(token=token, method=method)

    @property
    def enabled(self) -> bool:
        """Return whether the command bot is enabled."""
        return bool(self.config.get("enabled", True))

    @property
    def polling_interval_seconds(self) -> int:
        """Return polling interval."""
        return int(self.config.get("polling_interval_seconds", 5))

    @property
    def allowed_commands(self) -> list[str]:
        """Return configured allowed commands."""
        return [str(command).lower().strip() for command in self.config.get("allowed_commands", [])]

    @property
    def symbols(self) -> dict[str, str]:
        """Return configured display-to-broker symbols."""
        return {str(key).upper().strip(): str(value).upper().strip() for key, value in self.config.get("symbols", {}).items()}

    @property
    def advisor_mode_only(self) -> bool:
        """Return advisor-only safety flag."""
        return bool(self.config.get("advisor_mode_only", True))

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "telegram_bot.yaml")
        merged = self._deep_merge(self.DEFAULT_CONFIG, config)
        alert_config = self._load_yaml_file(self.config_dir / "alerts.yaml")
        if "telegram_settings" in alert_config:
            merged["telegram_settings"] = self._deep_merge(
                merged.get("telegram_settings", {}),
                alert_config.get("telegram_settings", {}),
            )
        return merged

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using Telegram bot defaults.", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise TelegramCommandBotError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
