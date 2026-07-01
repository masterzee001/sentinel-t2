"""Demo-only sandbox controls for non-production symbols."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4

import yaml

from backend.execution_engine.assisted_execution_bridge import (
    AssistedExecutionBridge,
    LockedTradeTicket,
    account_mode,
    deep_merge,
    mt5_constant,
    normalized_set,
    parse_datetime,
    to_dict,
    valid_sl_tp,
)
from backend.symbols.symbol_registry import SymbolRegistry


DEFAULT_CONFIG = {
    "enabled": False,
    "mode": "DEMO_ONLY",
    "allowed_symbols": ["BTCUSD", "NAS100"],
    "allowed_grades": ["A+", "A"],
    "default_risk_percent": 0.05,
    "max_risk_percent": 0.10,
    "human_approval_required": True,
    "submit_orders": False,
    "production_metrics_excluded": True,
    "challenge_mode_allowed": False,
    "max_slippage_points": {"BTCUSD": 300, "NAS100": 120},
    "max_spread_points": {"BTCUSD": 500, "NAS100": 150},
    "duplicate_order_protection": True,
    "require_fresh_ticket_seconds": 120,
}


class DemoSandboxEngine:
    """Validate demo-sandbox tickets without changing production execution rules."""

    def __init__(
        self,
        *,
        connector: Any | None = None,
        config_dir: str | Path | None = None,
        config: dict[str, Any] | None = None,
        assisted_config: dict[str, Any] | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector
        self.config = deep_merge(DEFAULT_CONFIG, config if config is not None else self.load_config())
        self.assisted_config = assisted_config if assisted_config is not None else self.load_assisted_config()
        self.symbol_registry = SymbolRegistry(config_dir=self.config_dir)

    def load_config(self) -> dict[str, Any]:
        """Load config/demo_sandbox.yaml."""
        path = self.config_dir / "demo_sandbox.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def load_assisted_config(self) -> dict[str, Any]:
        """Load assisted execution config for DEMO_ONLY mode verification."""
        path = self.config_dir / "assisted_execution.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def create_ticket(self, **payload: Any) -> LockedTradeTicket:
        """Create a locked SANDBOX_DEMO ticket."""
        now = parse_datetime(payload.get("created_at")) if payload.get("created_at") else datetime.now(UTC)
        expiry_seconds = int(self.config.get("require_fresh_ticket_seconds", 120) or 120)
        expires_at = parse_datetime(payload.get("expires_at")) if payload.get("expires_at") else now + timedelta(seconds=expiry_seconds)
        symbol = str(payload.get("symbol", "BTCUSD")).upper().strip()
        return LockedTradeTicket(
            ticket_id=str(payload.get("ticket_id") or f"SBX-{uuid4().hex[:8].upper()}"),
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            symbol=symbol,
            side=str(payload.get("side", "BUY")),
            entry_type=str(payload.get("entry_type", "LIMIT")),
            entry_price=float(payload.get("entry_price", 65000.0 if symbol == "BTCUSD" else 18000.0)),
            stop_loss=float(payload.get("stop_loss", 64900.0 if symbol == "BTCUSD" else 17950.0)),
            take_profit=float(payload.get("take_profit", 65300.0 if symbol == "BTCUSD" else 18150.0)),
            risk_percent=float(payload.get("risk_percent", self.config.get("default_risk_percent", 0.05))),
            lot_size=float(payload.get("lot_size", 0.01)),
            grade=str(payload.get("grade", "A+")),
            confidence=float(payload.get("confidence", 90)),
            strategy=str(payload.get("strategy", "trend_following")),
            killzone=str(payload.get("killzone", "new_york_open")),
            rationale=str(payload.get("rationale", "Sandbox demo-only learning ticket.")),
            status=str(payload.get("status", "AWAITING_APPROVAL")),
        )

    @staticmethod
    def transition_ticket(ticket: LockedTradeTicket, status: str) -> LockedTradeTicket:
        """Return a ticket copy with updated status."""
        return replace(ticket, status=str(status).upper())

    def dry_run(self, ticket: LockedTradeTicket, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build sandbox order payload without calling order_send."""
        context = context or {}
        validation = self.sandbox_gate(ticket, context=context, human_approved=ticket.status == "APPROVED", for_submit=False)
        payload = self.build_order_payload(ticket, context=context)
        return {
            "ticket_id": ticket.ticket_id,
            "ticket_type": "SANDBOX_DEMO",
            "mode": "DEMO_ONLY",
            "dry_run": True,
            "order_send_called": False,
            "symbol": ticket.symbol,
            "risk_percent": ticket.risk_percent,
            "lot_size": ticket.lot_size,
            "entry": ticket.entry_price,
            "sl": ticket.stop_loss,
            "tp": ticket.take_profit,
            "validation": validation,
            "order_payload": payload["request"],
            "safety_banner": sandbox_banner(),
        }

    def submit_demo_order(
        self,
        ticket: LockedTradeTicket,
        *,
        context: dict[str, Any] | None = None,
        human_approved: bool = False,
    ) -> dict[str, Any]:
        """Submit a sandbox demo order only after all sandbox gates pass."""
        context = context or {}
        validation = self.sandbox_gate(ticket, context=context, human_approved=human_approved or ticket.status == "APPROVED", for_submit=True)
        if not validation["passed"]:
            return self.blocked_submit(ticket, validation)
        if not bool(self.config.get("submit_orders", False)):
            blocked = {
                **validation,
                "passed": False,
                "status": "BLOCKED",
                "reasons": [*validation["reasons"], "demo_sandbox.submit_orders is false: dry-run only"],
            }
            return self.blocked_submit(ticket, blocked)
        mt5_module = getattr(self.connector, "mt5", None)
        if mt5_module is None or not hasattr(mt5_module, "order_send"):
            blocked = {
                **validation,
                "passed": False,
                "status": "BLOCKED",
                "reasons": [*validation["reasons"], "MT5 order_send unavailable"],
            }
            return self.blocked_submit(ticket, blocked)
        payload = self.build_order_payload(ticket, context=context)
        response = mt5_module.order_send(payload["request"])
        response_dict = to_dict(response)
        success_code = mt5_constant(self.connector, "TRADE_RETCODE_DONE")
        submitted = response_dict.get("retcode") in {success_code, "TRADE_RETCODE_DONE", 10009}
        return {
            "ticket_id": ticket.ticket_id,
            "ticket_type": "SANDBOX_DEMO",
            "status": "SUBMITTED_DEMO" if submitted else "BLOCKED",
            "order_submitted": submitted,
            "order_send_called": True,
            "validation": validation,
            "order_payload": payload["request"],
            "broker_response": response_dict,
            "safety_banner": sandbox_banner(),
        }

    @staticmethod
    def blocked_submit(ticket: LockedTradeTicket, validation: dict[str, Any]) -> dict[str, Any]:
        """Return a standard blocked sandbox submit result."""
        return {
            "ticket_id": ticket.ticket_id,
            "ticket_type": "SANDBOX_DEMO",
            "status": "BLOCKED",
            "order_submitted": False,
            "order_send_called": False,
            "validation": validation,
            "reason": "; ".join(validation.get("reasons", [])),
            "safety_banner": sandbox_banner(),
        }

    def sandbox_gate(
        self,
        ticket: LockedTradeTicket,
        *,
        context: dict[str, Any] | None = None,
        human_approved: bool = False,
        for_submit: bool = True,
    ) -> dict[str, Any]:
        """Return sandbox gate status without consulting production execution permission."""
        context = context or {}
        now = parse_datetime(context.get("now")) if context.get("now") else datetime.now(UTC)
        account = self.account_info(context)
        spread_points = float(context.get("spread_points", 0.0) or 0.0)
        slippage_points = float(context.get("slippage_points", 0.0) or 0.0)
        checks = {
            "sandbox_enabled": bool(self.config.get("enabled", False)),
            "demo_only_mode": str(self.config.get("mode", "")).upper() == "DEMO_ONLY",
            "assisted_demo_only": str(context.get("assisted_execution_mode") or self.assisted_config.get("mode", "DEMO_ONLY")).upper()
            == "DEMO_ONLY",
            "demo_account": account_mode(account) == "demo",
            "human_approval": bool(human_approved) if self.config.get("human_approval_required", True) and for_submit else True,
            "ticket_status": ticket.status in {"AWAITING_APPROVAL", "APPROVED"},
            "ticket_not_expired": now < parse_datetime(ticket.expires_at),
            "ticket_freshness": (now - parse_datetime(ticket.created_at)).total_seconds() <= int(self.config.get("require_fresh_ticket_seconds", 120) or 120),
            "symbol_tier": self.symbol_registry.sandbox_execution_allowed(ticket.symbol)
            and ticket.symbol in normalized_set(self.config.get("allowed_symbols", [])),
            "grade_lock": ticket.grade in normalized_set(self.config.get("allowed_grades", [])),
            "risk_lock": ticket.risk_percent <= float(self.config.get("max_risk_percent", 0.10) or 0.10),
            "kill_switch": not bool(context.get("kill_switch_active", False)),
            "duplicate_lock": not self.duplicate_order_exists(ticket, context),
            "challenge_mode": not bool(context.get("challenge_mode_active", False)),
            "production_metrics_excluded": bool(self.config.get("production_metrics_excluded", True)),
            "challenge_mode_allowed_false": not bool(self.config.get("challenge_mode_allowed", False)),
            "spread_lock": spread_points <= symbol_limit(self.config.get("max_spread_points", {}), ticket.symbol),
            "slippage_lock": slippage_points <= symbol_limit(self.config.get("max_slippage_points", {}), ticket.symbol),
            "stop_loss_take_profit": valid_sl_tp(ticket),
        }
        reasons = [reason for key, reason in sandbox_rejection_reasons().items() if not checks.get(key, False)]
        return {
            "status": "PASS" if not reasons else "BLOCKED",
            "passed": not reasons,
            "reasons": reasons,
            "checks": checks,
            "account_mode": account_mode(account).upper() if account else "UNKNOWN",
            "spread_points": spread_points,
            "slippage_points": slippage_points,
        }

    def build_order_payload(self, ticket: LockedTradeTicket, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a sandbox demo order payload without submitting."""
        bridge = AssistedExecutionBridge(connector=self.connector, config_dir=self.config_dir, config=self.assisted_bridge_config())
        payload = bridge.build_order_payload(ticket, context=context or {})
        payload["request"]["comment"] = f"Sentinel sandbox demo {ticket.ticket_id}"
        return payload

    def assisted_bridge_config(self) -> dict[str, Any]:
        """Return assisted bridge-compatible sandbox config."""
        return {
            "enabled": bool(self.config.get("enabled", False)),
            "mode": "DEMO_ONLY",
            "submit_orders": bool(self.config.get("submit_orders", False)),
            "human_approval_required": bool(self.config.get("human_approval_required", True)),
            "allowed_account_mode": "demo",
            "allowed_symbols": self.config.get("allowed_symbols", []),
            "allowed_grades": self.config.get("allowed_grades", []),
            "max_risk_percent": float(self.config.get("max_risk_percent", 0.10) or 0.10),
            "default_risk_percent": float(self.config.get("default_risk_percent", 0.05) or 0.05),
            "max_slippage_points": self.config.get("max_slippage_points", {}),
            "max_spread_points": self.config.get("max_spread_points", {}),
            "duplicate_order_protection": bool(self.config.get("duplicate_order_protection", True)),
            "require_fresh_ticket_seconds": int(self.config.get("require_fresh_ticket_seconds", 120) or 120),
            "broker_submission_global_override": False,
        }

    def duplicate_order_exists(self, ticket: LockedTradeTicket, context: dict[str, Any]) -> bool:
        """Return whether duplicate protection detects an existing sandbox order."""
        if not bool(self.config.get("duplicate_order_protection", True)):
            return False
        if bool(context.get("duplicate_order", False)):
            return True
        for item in context.get("open_orders", []) or []:
            if str(item.get("ticket_id", "")).upper() == ticket.ticket_id.upper():
                return True
            if str(item.get("symbol", "")).upper() == ticket.symbol and str(item.get("side", "")).upper() == ticket.side:
                return True
        return False

    def account_info(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return account info from context or connector."""
        account = context.get("account")
        if isinstance(account, dict):
            return account
        if self.connector is None or not hasattr(self.connector, "get_account_info"):
            return {}
        try:
            return dict(self.connector.get_account_info())
        except Exception:
            return {}

    def status_report(self, *, trades: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return demo sandbox status/performance report."""
        context = context or {"account": {"account_mode": "demo", "server": "MetaQuotes-Demo", "balance": 10000.0}}
        ticket = self.sample_ticket()
        dry_run = self.dry_run(ticket, context=context)
        memory = SandboxLearningMemory.build(trades or [], symbols=self.allowed_symbols)
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "sandbox": {
                "enabled": bool(self.config.get("enabled", False)),
                "mode": str(self.config.get("mode", "DEMO_ONLY")),
                "allowed_symbols": self.allowed_symbols,
                "allowed_grades": self.allowed_grades,
                "default_risk_percent": float(self.config.get("default_risk_percent", 0.05) or 0.05),
                "max_risk_percent": float(self.config.get("max_risk_percent", 0.10) or 0.10),
                "submit_orders": bool(self.config.get("submit_orders", False)),
                "production_metrics_excluded": bool(self.config.get("production_metrics_excluded", True)),
                "challenge_mode_allowed": bool(self.config.get("challenge_mode_allowed", False)),
            },
            "symbol_tiers": {
                "production": self.symbol_registry.execution_symbols(),
                "demo_sandbox": self.symbol_registry.demo_sandbox_symbols(),
                "observer_only": self.symbol_registry.observer_only_symbols(),
            },
            "current_ticket": {**ticket.to_dict(), "ticket_type": "SANDBOX_DEMO"},
            "dry_run": dry_run,
            "learning_memory": memory,
            "performance": SandboxLearningMemory.performance_summary(memory),
            "assisted_integration": {
                "assisted_mode": str(self.assisted_config.get("mode", "DEMO_ONLY")),
                "human_approval_required": bool(self.config.get("human_approval_required", True)),
                "submit_default": bool(self.config.get("submit_orders", False)),
                "dry_run_only": not bool(self.config.get("submit_orders", False)),
                "order_send_blocked_by_default": not bool(self.config.get("submit_orders", False)),
            },
            "safety": {
                "sandbox_demo_only": True,
                "not_production": True,
                "not_funded": True,
                "not_challenge": not bool(self.config.get("challenge_mode_allowed", False)),
                "autonomous_execution": False,
            },
            "production_baseline_preserved": True,
            "decision": "PASS",
        }

    def sample_ticket(self) -> LockedTradeTicket:
        """Return a representative sandbox ticket."""
        return self.create_ticket(ticket_id="SBX-SAMPLE", symbol="BTCUSD", status="AWAITING_APPROVAL")

    @property
    def allowed_symbols(self) -> list[str]:
        return [str(symbol).upper().strip() for symbol in self.config.get("allowed_symbols", [])]

    @property
    def allowed_grades(self) -> list[str]:
        return [str(grade).upper().strip() for grade in self.config.get("allowed_grades", [])]


class SandboxLearningMemory:
    """Build symbol-level demo sandbox learning metrics."""

    @classmethod
    def build(cls, trades: list[dict[str, Any]], *, symbols: list[str]) -> dict[str, Any]:
        """Return per-symbol sandbox learning memory."""
        memory = {symbol: cls.symbol_memory(symbol, [trade for trade in trades if str(trade.get("symbol", "")).upper() == symbol]) for symbol in symbols}
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "production_metrics_excluded": True,
            "symbols": memory,
        }

    @classmethod
    def symbol_memory(cls, symbol: str, trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Return learning metrics for one sandbox symbol."""
        rr_values = [float(trade.get("rr", trade.get("realized_rr", 0.0)) or 0.0) for trade in trades]
        wins = [rr for rr in rr_values if rr > 0]
        losses = [rr for rr in rr_values if rr < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        setup_scores = bucket_average(trades, "setup_type")
        regime_scores = bucket_average(trades, "regime")
        return {
            "symbol": symbol,
            "trade_count": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "PF": round(gross_win / gross_loss, 2) if gross_loss else round(gross_win, 2),
            "WR": round((len(wins) / len(trades)) * 100.0, 2) if trades else 0.0,
            "avg_RR": round(mean(rr_values), 2) if rr_values else 0.0,
            "avg_spread": rounded_mean(trade.get("spread") for trade in trades),
            "avg_slippage": rounded_mean(trade.get("slippage") for trade in trades),
            "avg_latency": rounded_mean(trade.get("latency") for trade in trades),
            "best_setup_types": best_bucket_names(setup_scores),
            "worst_setup_types": worst_bucket_names(setup_scores),
            "best_regime": first_bucket_name(best_bucket_names(regime_scores)),
            "worst_regime": first_bucket_name(worst_bucket_names(regime_scores)),
            "execution_anomaly_clusters": dict(Counter(str(trade.get("execution_anomaly", "none")) for trade in trades if trade.get("execution_anomaly"))),
            "promotion_status": cls.promotion_status(
                {
                    "trades": len(trades),
                    "pf": round(gross_win / gross_loss, 2) if gross_loss else round(gross_win, 2),
                    "wr": round((len(wins) / len(trades)) * 100.0, 2) if trades else 0.0,
                    "dd": max((float(trade.get("drawdown", 0.0) or 0.0) for trade in trades), default=0.0),
                    "execution_quality": str(next((trade.get("execution_quality") for trade in trades if trade.get("execution_quality")), "stable")),
                    "correlation": max((float(trade.get("correlation", 0.0) or 0.0) for trade in trades), default=0.0),
                    "ai_policy": str(next((trade.get("ai_policy") for trade in trades if trade.get("ai_policy")), "neutral")),
                }
            ),
        }

    @staticmethod
    def promotion_status(metrics: dict[str, Any]) -> str:
        """Return manual-review promotion status."""
        passed = (
            int(metrics.get("trades", 0) or 0) >= 90
            and float(metrics.get("pf", 0.0) or 0.0) >= 2.5
            and float(metrics.get("wr", 0.0) or 0.0) >= 68.0
            and float(metrics.get("dd", 0.0) or 0.0) <= 4.0
            and str(metrics.get("execution_quality", "")).lower() in {"stable", "good"}
            and float(metrics.get("correlation", 1.0) or 0.0) <= 0.75
            and str(metrics.get("ai_policy", "")).lower() in {"positive", "approved", "strong"}
        )
        return "PRODUCTION_CANDIDATE" if passed else "DEMO_SANDBOX"

    @staticmethod
    def performance_summary(memory: dict[str, Any]) -> dict[str, Any]:
        """Return compact sandbox performance summary."""
        rows = list(memory.get("symbols", {}).values())
        total_trades = sum(int(row.get("trade_count", 0) or 0) for row in rows)
        wins = sum(int(row.get("wins", 0) or 0) for row in rows)
        losses = sum(int(row.get("losses", 0) or 0) for row in rows)
        return {
            "trade_count": total_trades,
            "wins": wins,
            "losses": losses,
            "WR": round((wins / total_trades) * 100.0, 2) if total_trades else 0.0,
            "PF": rounded_mean(row.get("PF") for row in rows if int(row.get("trade_count", 0) or 0) > 0),
            "avg_RR": rounded_mean(row.get("avg_RR") for row in rows if int(row.get("trade_count", 0) or 0) > 0),
            "avg_spread": rounded_mean(row.get("avg_spread") for row in rows if int(row.get("trade_count", 0) or 0) > 0),
            "avg_slippage": rounded_mean(row.get("avg_slippage") for row in rows if int(row.get("trade_count", 0) or 0) > 0),
            "avg_latency": rounded_mean(row.get("avg_latency") for row in rows if int(row.get("trade_count", 0) or 0) > 0),
        }


def sandbox_rejection_reasons() -> dict[str, str]:
    """Return sandbox gate rejection reasons."""
    return {
        "sandbox_enabled": "demo_sandbox.enabled is false",
        "demo_only_mode": "demo_sandbox.mode is not DEMO_ONLY",
        "assisted_demo_only": "assisted_execution.mode is not DEMO_ONLY",
        "demo_account": "MT5 account is not demo",
        "human_approval": "human approval not received",
        "ticket_status": "ticket status must be AWAITING_APPROVAL or APPROVED",
        "ticket_not_expired": "ticket expired",
        "ticket_freshness": "ticket is stale",
        "symbol_tier": "symbol is not DEMO_SANDBOX",
        "grade_lock": "sandbox allows only configured A/A+ grades",
        "risk_lock": "risk exceeds max sandbox risk",
        "kill_switch": "kill switch active",
        "duplicate_lock": "duplicate sandbox order protection blocked ticket",
        "challenge_mode": "sandbox is blocked while challenge mode is active",
        "production_metrics_excluded": "sandbox must remain excluded from production metrics",
        "challenge_mode_allowed_false": "sandbox cannot enter challenge mode",
        "spread_lock": "spread exceeds sandbox symbol limit",
        "slippage_lock": "slippage exceeds sandbox symbol limit",
        "stop_loss_take_profit": "invalid stop_loss/take_profit geometry",
    }


def symbol_limit(limits: dict[str, Any], symbol: str) -> float:
    return float(limits.get(str(symbol).upper(), 0.0) or 0.0)


def rounded_mean(values: Any) -> float:
    numbers: list[float] = []
    for value in values:
        if value is None:
            continue
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            continue
    return round(mean(numbers), 2) if numbers else 0.0


def bucket_average(trades: list[dict[str, Any]], key: str) -> dict[str, float]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        name = str(trade.get(key, "unknown") or "unknown")
        buckets[name].append(float(trade.get("rr", trade.get("realized_rr", 0.0)) or 0.0))
    return {name: round(mean(values), 2) for name, values in buckets.items()}


def best_bucket_names(scores: dict[str, float]) -> list[str]:
    if not scores:
        return []
    best = max(scores.values())
    return [name for name, score in scores.items() if score == best]


def worst_bucket_names(scores: dict[str, float]) -> list[str]:
    if not scores:
        return []
    worst = min(scores.values())
    return [name for name, score in scores.items() if score == worst]


def first_bucket_name(values: list[str]) -> str:
    return values[0] if values else "none"


def sandbox_banner() -> str:
    return "SANDBOX DEMO ONLY | NOT PRODUCTION | NOT FUNDED | NOT CHALLENGE"
