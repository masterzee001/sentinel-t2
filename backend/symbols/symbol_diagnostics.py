"""Symbol diagnostic reporting from cached backtest data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SymbolDiagnostics:
    """Build symbol expansion diagnostics from backtest reports."""

    KILLZONES = ("london_open", "london_continuation", "new_york_open", "new_york_continuation")
    NARRATIVES = ("accumulation", "expansion", "distribution", "reversal")

    @classmethod
    def xau_diagnostics(cls, report: dict[str, Any]) -> dict[str, Any]:
        """Return XAU-focused diagnostics from full trade records when available, otherwise cached summaries."""
        trades = [trade for trade in report.get("trades", []) if str(trade.get("symbol", "")).upper() == "XAUUSD"]
        if trades:
            return cls.from_trades("XAUUSD", trades)
        return cls.from_cached_summary("XAUUSD", report)

    @classmethod
    def from_trades(cls, symbol: str, trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Build exact diagnostics from trade-level backtest rows."""
        by_killzone = {
            killzone: cls.metrics([trade for trade in trades if trade.get("killzone") == killzone])
            for killzone in cls.KILLZONES
        }
        by_narrative = {
            phase: cls.metrics([trade for trade in trades if trade.get("narrative_phase") == phase])
            for phase in cls.NARRATIVES
        }
        by_smt = {
            "present": cls.metrics([trade for trade in trades if bool(trade.get("smt_detected", False))]),
            "absent": cls.metrics([trade for trade in trades if not bool(trade.get("smt_detected", False))]),
        }
        losses = [trade for trade in trades if trade.get("simulation", {}).get("outcome") == "LOSS"]
        return cls.result(symbol, by_killzone, by_narrative, by_smt, losses)

    @classmethod
    def from_cached_summary(cls, symbol: str, report: dict[str, Any]) -> dict[str, Any]:
        """Build best-effort diagnostics from aggregate 365D cache fields."""
        symbol_metrics = report.get("symbol_breakdown", {}).get(symbol, {})
        smt_split = report.get("xau_smt_split") or report.get("production_portfolio", {}).get("xau_smt_split", {})
        by_killzone = {
            killzone: metrics
            for killzone, metrics in report.get("killzone_breakdown", {}).get("metrics", {}).items()
            if killzone in cls.KILLZONES
        }
        by_narrative = {
            phase: metrics
            for phase, metrics in report.get("narrative_breakdown", {}).items()
            if phase in cls.NARRATIVES
        }
        loss_clusters = [
            cluster for cluster in report.get("loss_clusters", [])
            if str(cluster.get("symbol", "")).upper() == symbol
        ]
        if smt_split:
            by_smt = {
                "present": smt_split.get("with_smt", {}),
                "absent": smt_split.get("without_smt", {}),
            }
            return cls.result(
                symbol,
                by_killzone,
                by_narrative,
                by_smt,
                loss_clusters,
                smt_dependency_override=str(smt_split.get("dependency", "NOT_PROVEN")),
            )
        by_smt = {
            "present": {"trades": 0, "profit_factor": 0.0, "win_rate": 0.0, "avg_rr": 0.0, "max_drawdown": 0.0},
            "absent": symbol_metrics,
        }
        return cls.result(symbol, by_killzone, by_narrative, by_smt, loss_clusters, fallback=True)

    @classmethod
    def result(
        cls,
        symbol: str,
        by_killzone: dict[str, dict[str, Any]],
        by_narrative: dict[str, dict[str, Any]],
        by_smt: dict[str, dict[str, Any]],
        losses: list[dict[str, Any]],
        *,
        fallback: bool = False,
        smt_dependency_override: str | None = None,
    ) -> dict[str, Any]:
        """Return normalized diagnostic report."""
        best_killzone = cls.best_bucket(by_killzone)
        worst_killzone = cls.worst_bucket(by_killzone)
        smt_present_pf = cls.metric(by_smt.get("present", {}), "profit_factor", "pf")
        smt_absent_pf = cls.metric(by_smt.get("absent", {}), "profit_factor", "pf")
        dependency = smt_dependency_override or cls.smt_dependency_from_split(by_smt, smt_present_pf, smt_absent_pf, fallback=fallback)
        return {
            "symbol": symbol,
            "by_killzone": by_killzone,
            "by_narrative": by_narrative,
            "by_smt": by_smt,
            "best_killzone": best_killzone,
            "worst_killzone": worst_killzone,
            "smt_dependency": dependency,
            "loss_clusters": losses,
            "answers": {
                "xau_profitable_only_ny": cls.xau_profitable_only_ny(by_killzone),
                "smt_mandatory": cls.smt_mandatory_answer(dependency, smt_present_pf, smt_absent_pf),
                "loss_leaks": cls.loss_leaks(losses),
            },
            "fallback_summary_used": fallback,
        }

    @staticmethod
    def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate compact metrics for diagnostic groups."""
        if not trades:
            return {"trades": 0, "profit_factor": 0.0, "win_rate": 0.0, "avg_rr": 0.0, "max_drawdown": 0.0}
        wins = sum(1 for trade in trades if trade.get("simulation", {}).get("outcome") == "WIN")
        losses = sum(1 for trade in trades if trade.get("simulation", {}).get("outcome") == "LOSS")
        gross_profit = sum(max(float(trade.get("pnl", 0.0)), 0.0) for trade in trades)
        gross_loss = abs(sum(min(float(trade.get("pnl", 0.0)), 0.0) for trade in trades))
        rr_values = [float(trade.get("rr", 0.0)) for trade in trades]
        return {
            "trades": len(trades),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0.0),
            "win_rate": round((wins / (wins + losses)) * 100, 2) if wins + losses else 0.0,
            "avg_rr": round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0,
            "max_drawdown": 0.0,
        }

    @classmethod
    def best_bucket(cls, buckets: dict[str, dict[str, Any]]) -> str:
        eligible = [(name, data) for name, data in buckets.items() if cls.trades(data) > 0]
        if not eligible:
            return "none"
        return max(eligible, key=lambda item: (cls.metric(item[1], "profit_factor", "pf"), cls.metric(item[1], "win_rate")))[0]

    @classmethod
    def worst_bucket(cls, buckets: dict[str, dict[str, Any]]) -> str:
        eligible = [(name, data) for name, data in buckets.items() if cls.trades(data) > 0]
        if not eligible:
            return "none"
        return min(eligible, key=lambda item: (cls.metric(item[1], "profit_factor", "pf"), cls.metric(item[1], "win_rate")))[0]

    @classmethod
    def xau_profitable_only_ny(cls, by_killzone: dict[str, dict[str, Any]]) -> bool | str:
        london = [cls.metric(by_killzone.get(name, {}), "profit_factor", "pf") for name in ("london_open", "london_continuation")]
        ny = [cls.metric(by_killzone.get(name, {}), "profit_factor", "pf") for name in ("new_york_open", "new_york_continuation")]
        if not any(london) and not any(ny):
            return "UNKNOWN"
        return max(ny or [0]) > 1.0 and max(london or [0]) <= 1.0

    @staticmethod
    def smt_dependency(present_pf: float, absent_pf: float, *, fallback: bool) -> str:
        if fallback:
            return "NO_TRADE_LEVEL_SMT_SPLIT"
        if present_pf > absent_pf and absent_pf < 1.0:
            return "MANDATORY"
        if present_pf > absent_pf:
            return "HELPFUL"
        return "NOT PROVEN"

    @classmethod
    def smt_dependency_from_split(
        cls,
        by_smt: dict[str, dict[str, Any]],
        present_pf: float,
        absent_pf: float,
        *,
        fallback: bool,
    ) -> str:
        """Classify SMT dependency from explicit present/absent samples."""
        if fallback:
            return "NO_TRADE_LEVEL_SMT_SPLIT"
        present_trades = cls.trades(by_smt.get("present", {}))
        absent_trades = cls.trades(by_smt.get("absent", {}))
        if present_trades <= 0 and absent_trades > 0:
            return "NO_SMT_SAMPLE"
        if present_trades > 0 and absent_trades <= 0:
            return "SMT_ONLY_SAMPLE"
        return cls.smt_dependency(present_pf, absent_pf, fallback=False)

    @staticmethod
    def smt_mandatory_answer(dependency: str, present_pf: float, absent_pf: float) -> bool | str:
        """Return an explicit SMT answer without using UNKNOWN."""
        if dependency in {"NO_SMT_SAMPLE", "NO_XAU_TRADES", "NO_TRADE_LEVEL_SMT_SPLIT", "SMT_ONLY_SAMPLE"}:
            return dependency
        if not present_pf:
            return "NOT_PROVEN"
        return present_pf > absent_pf and absent_pf < 1.0

    @staticmethod
    def loss_leaks(losses: list[dict[str, Any]]) -> str:
        if not losses:
            return "none"
        first = losses[0]
        return ", ".join(
            str(first.get(key, "unknown"))
            for key in ("killzone", "narrative", "smt_state", "confidence_band")
            if first.get(key) is not None
        )

    @staticmethod
    def metric(metrics: dict[str, Any], *keys: str) -> float:
        for key in keys:
            if key in metrics:
                try:
                    return round(float(metrics.get(key) or 0.0), 2)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    @classmethod
    def trades(cls, metrics: dict[str, Any]) -> int:
        return int(cls.metric(metrics, "trades", "trades_approved"))

    @staticmethod
    def load_report(path: str | Path) -> dict[str, Any]:
        report_path = Path(path)
        if not report_path.exists():
            return {}
        return json.loads(report_path.read_text(encoding="utf-8"))
