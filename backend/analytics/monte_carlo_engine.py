"""Monte Carlo stress testing for Project Sentinel Advisor Mode."""

from __future__ import annotations

import json
import random
import statistics
from pathlib import Path
from typing import Any

import yaml
from loguru import logger


class MonteCarloEngineError(RuntimeError):
    """Raised when Monte Carlo stress testing cannot be completed."""


class MonteCarloEngine:
    """Randomize historical trade sequencing to estimate drawdown risk."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "simulations": 10000,
        "random_seed": 271828,
        "risk_models": [0.25, 0.5, 0.75, 1.0],
        "drawdown_limits": {
            "internal_limit": 4.0,
            "firm_limit": 6.0,
        },
        "starting_balance": 2000,
        "backtest_report_path": "data/reports/backtest_365d_summary.json",
    }

    def __init__(
        self,
        *,
        config_dir: str | Path | None = None,
        project_root: str | Path | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else self.project_root / "config"
        loaded = self._load_yaml_file(self.config_dir / "monte_carlo.yaml")
        self.config = self._deep_merge(self.DEFAULT_CONFIG, self._deep_merge(loaded, config or {}))

    def run_from_report(self, report_path: str | Path | None = None) -> dict[str, Any]:
        """Load 365D trade outcomes and run all configured risk models."""
        if not self.enabled:
            return {"available": False, "reason": "Monte Carlo stress testing disabled."}
        path = self.resolve_path(report_path or self.config.get("backtest_report_path"))
        report = self.load_report(path)
        trades = self.extract_trades(report)
        return self.run(trades=trades, source_path=path)

    def run(self, *, trades: list[dict[str, Any]], source_path: str | Path | None = None) -> dict[str, Any]:
        """Run Monte Carlo simulations for every configured risk model."""
        if not trades:
            raise MonteCarloEngineError("Monte Carlo requires at least one closed trade outcome.")
        simulations = int(self.config.get("simulations", 10000))
        starting_balance = float(self.config.get("starting_balance", 2000))
        internal_limit = float(self.config.get("drawdown_limits", {}).get("internal_limit", 4.0))
        firm_limit = float(self.config.get("drawdown_limits", {}).get("firm_limit", 6.0))
        seed = int(self.config.get("random_seed", 271828))
        rr_values = [float(trade.get("rr", 0.0) or 0.0) for trade in trades]

        risk_results: dict[str, Any] = {}
        for risk_percent in [float(value) for value in self.config.get("risk_models", [])]:
            risk_results[self.risk_key(risk_percent)] = self.run_risk_model(
                rr_values=rr_values,
                risk_percent=risk_percent,
                simulations=simulations,
                starting_balance=starting_balance,
                internal_limit=internal_limit,
                firm_limit=firm_limit,
                seed=seed,
            )

        recommendation = self.build_recommendation(risk_results)
        return {
            "available": True,
            "advisor_mode_only": True,
            "source_path": str(source_path or ""),
            "trades_used": len(trades),
            "total_simulations": simulations,
            "starting_balance": starting_balance,
            "drawdown_limits": {"internal_limit": internal_limit, "firm_limit": firm_limit},
            "risk_models": risk_results,
            **recommendation,
        }

    def run_risk_model(
        self,
        *,
        rr_values: list[float],
        risk_percent: float,
        simulations: int,
        starting_balance: float,
        internal_limit: float,
        firm_limit: float,
        seed: int,
    ) -> dict[str, Any]:
        """Run one risk model and return aggregate stress metrics."""
        rng = random.Random(seed + int(risk_percent * 10000))
        returns: list[float] = []
        drawdowns: list[float] = []
        losing_streaks: list[int] = []
        win_streaks: list[int] = []
        internal_breaches = 0
        firm_breaches = 0
        collapses = 0
        sample_balance_path: list[float] = []

        for index in range(simulations):
            sequence = list(rr_values)
            rng.shuffle(sequence)
            result = self.simulate_sequence(sequence, risk_percent=risk_percent, starting_balance=starting_balance)
            if index == 0:
                sample_balance_path = result["balance_path"]
            returns.append(result["return_percent"])
            drawdowns.append(result["max_drawdown"])
            losing_streaks.append(result["max_losing_streak"])
            win_streaks.append(result["max_win_streak"])
            if result["max_drawdown"] >= internal_limit:
                internal_breaches += 1
            if result["max_drawdown"] >= firm_limit:
                firm_breaches += 1
            if result["collapsed"]:
                collapses += 1

        return {
            "risk_percent": risk_percent,
            "total_simulations": simulations,
            "global": {
                "average_return": round(statistics.fmean(returns), 2),
                "median_return": round(statistics.median(returns), 2),
                "best_return": round(max(returns), 2),
                "worst_return": round(min(returns), 2),
            },
            "drawdown": {
                "average_dd": round(statistics.fmean(drawdowns), 2),
                "median_dd": round(statistics.median(drawdowns), 2),
                "p95_dd": round(self.percentile(drawdowns, 95), 2),
                "max_dd": round(max(drawdowns), 2),
                "histogram": self.histogram(drawdowns),
            },
            "streaks": {
                "average_max_losing_streak": round(statistics.fmean(losing_streaks), 2),
                "worst_losing_streak": int(max(losing_streaks)),
                "average_max_win_streak": round(statistics.fmean(win_streaks), 2),
            },
            "risk_of_ruin": {
                "breach_4_percent": round((internal_breaches / simulations) * 100, 2),
                "breach_6_percent": round((firm_breaches / simulations) * 100, 2),
                "account_collapse": round((collapses / simulations) * 100, 2),
            },
            "sample_balance_path": sample_balance_path,
        }

    @classmethod
    def simulate_sequence(cls, rr_values: list[float], *, risk_percent: float, starting_balance: float) -> dict[str, Any]:
        """Simulate one shuffled trade sequence."""
        balance = float(starting_balance)
        peak = balance
        max_drawdown = 0.0
        max_losing_streak = 0
        max_win_streak = 0
        current_losses = 0
        current_wins = 0
        balance_path = [round(balance, 2)]

        for rr in rr_values:
            risk_amount = balance * (float(risk_percent) / 100)
            balance += risk_amount * float(rr)
            peak = max(peak, balance)
            drawdown = ((peak - balance) / peak) * 100 if peak else 0.0
            max_drawdown = max(max_drawdown, drawdown)
            if rr < 0:
                current_losses += 1
                current_wins = 0
            elif rr > 0:
                current_wins += 1
                current_losses = 0
            else:
                current_losses = 0
                current_wins = 0
            max_losing_streak = max(max_losing_streak, current_losses)
            max_win_streak = max(max_win_streak, current_wins)
            balance_path.append(round(balance, 2))

        return {
            "final_balance": round(balance, 2),
            "return_percent": round(((balance - starting_balance) / starting_balance) * 100, 2) if starting_balance else 0.0,
            "balance_path": balance_path,
            "max_drawdown": round(max_drawdown, 2),
            "max_losing_streak": max_losing_streak,
            "max_win_streak": max_win_streak,
            "collapsed": balance <= 0,
        }

    def extract_trades(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract closed trade outcomes from detailed records or aggregate 365D metrics."""
        for key in ("trades", "trade_log", "closed_trades"):
            records = report.get(key, [])
            if isinstance(records, list) and records and isinstance(records[0], dict):
                extracted = [self.normalize_trade(record) for record in records]
                return [trade for trade in extracted if trade is not None]
        return self.reconstruct_trades_from_summary(report)

    @staticmethod
    def normalize_trade(record: dict[str, Any]) -> dict[str, Any] | None:
        """Return one normalized closed trade record."""
        rr = record.get("rr", record.get("outcome_rr", record.get("r_multiple")))
        if rr is None and isinstance(record.get("simulation"), dict):
            rr = record["simulation"].get("rr")
        if rr is None:
            return None
        rr_value = float(rr)
        if rr_value > 0:
            outcome = "WIN"
        elif rr_value < 0:
            outcome = "LOSS"
        else:
            outcome = "BREAKEVEN"
        return {
            "rr": rr_value,
            "outcome": record.get("outcome", outcome),
            "symbol": record.get("symbol", "UNKNOWN"),
            "killzone": record.get("killzone", "none"),
            "timestamp": record.get("timestamp", ""),
        }

    def reconstruct_trades_from_summary(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        """Build a conservative RR sample from aggregate 365D metrics."""
        metrics = report.get("days_365", report.get("global_metrics", {}))
        wins = int(metrics.get("wins", 0) or 0)
        losses = int(metrics.get("losses", 0) or 0)
        breakevens = int(metrics.get("breakevens", 0) or 0)
        gross_profit = float(metrics.get("gross_profit", 0.0) or 0.0)
        gross_loss = float(metrics.get("gross_loss", 0.0) or 0.0)
        if wins + losses + breakevens <= 0:
            raise MonteCarloEngineError("365D report does not contain enough trade outcome data.")
        loss_rr = -1.0
        if losses and gross_loss and gross_profit:
            total_win_rr = gross_profit / (gross_loss / losses)
        else:
            total_win_rr = max(float(metrics.get("net_rr", 0.0) or 0.0) + losses, 0.0)
        average_win_rr = total_win_rr / wins if wins else 0.0
        trades = [{"rr": round(average_win_rr, 4), "outcome": "WIN", "symbol": "SYNTHETIC", "killzone": "summary"} for _ in range(wins)]
        trades.extend({"rr": loss_rr, "outcome": "LOSS", "symbol": "SYNTHETIC", "killzone": "summary"} for _ in range(losses))
        trades.extend({"rr": 0.0, "outcome": "BREAKEVEN", "symbol": "SYNTHETIC", "killzone": "summary"} for _ in range(breakevens))
        return trades

    @staticmethod
    def build_recommendation(risk_results: dict[str, Any]) -> dict[str, Any]:
        """Build risk notes and safe risk recommendation."""
        eligible: list[float] = []
        risk_notes: list[str] = []
        recommendations: list[str] = []
        for key, result in risk_results.items():
            risk = float(result.get("risk_percent", 0.0))
            p95_dd = float(result.get("drawdown", {}).get("p95_dd", 0.0))
            breach_4 = float(result.get("risk_of_ruin", {}).get("breach_4_percent", 0.0))
            if p95_dd <= 4.0 and breach_4 <= 5.0:
                eligible.append(risk)
            if risk <= 0.25:
                recommendations.append("0.25% is extremely safe but slow growth.")
            elif risk == 0.5:
                recommendations.append("0.5% is the preferred balance of growth and drawdown control.")
            elif risk == 0.75:
                risk_notes.append("0.75% carries elevated drawdown risk under shuffled sequencing.")
            elif risk >= 1.0:
                risk_notes.append("1% is not recommended for prop-firm constraints.")
            if breach_4 > 5.0:
                risk_notes.append(f"{key} breaches the internal drawdown limit too often.")

        safe_risk = max(eligible) if eligible else min((float(item.get("risk_percent", 0.0)) for item in risk_results.values()), default=0.0)
        if safe_risk > 0.5:
            safe_risk = 0.5
        recommendations.append("Autonomous execution remains disabled until stress risk is lower and live sample size grows.")
        return {
            "safe_risk_percent": safe_risk,
            "autonomous_mode_recommended": False,
            "risk_notes": list(dict.fromkeys(risk_notes)),
            "recommendations": list(dict.fromkeys(recommendations)),
        }

    @staticmethod
    def percentile(values: list[float], percentile: float) -> float:
        """Return nearest-rank percentile."""
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1))))
        return ordered[index]

    @staticmethod
    def histogram(values: list[float], bucket_size: float = 1.0) -> dict[str, int]:
        """Return drawdown histogram buckets."""
        buckets: dict[str, int] = {}
        for value in values:
            lower = int(float(value) // bucket_size) * bucket_size
            upper = lower + bucket_size
            label = f"{round(lower, 2)}-{round(upper, 2)}%"
            buckets[label] = buckets.get(label, 0) + 1
        return buckets

    @staticmethod
    def risk_key(risk_percent: float) -> str:
        """Return stable risk model key."""
        return f"{risk_percent:g}%"

    def load_report(self, path: Path) -> dict[str, Any]:
        """Load a JSON report."""
        if not path.exists():
            raise MonteCarloEngineError(f"Backtest report not found: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise MonteCarloEngineError(f"Failed to load backtest report {path}: {exc}") from exc

    def resolve_path(self, path: str | Path | None) -> Path:
        """Resolve a path relative to the project root."""
        candidate = Path(path or self.config.get("backtest_report_path"))
        return candidate if candidate.is_absolute() else self.project_root / candidate

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = MonteCarloEngine._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using Monte Carlo defaults.", path)
            return {}
        with path.open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
