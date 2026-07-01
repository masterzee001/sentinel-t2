"""Strategy-specific diagnostic trade planners for Market Watch."""

from __future__ import annotations

from typing import Any


class StrategyPlanners:
    """Create diagnostic-only plans by selected strategy family."""

    def plan(
        self,
        *,
        symbol: str,
        selection: dict[str, Any],
        pattern: dict[str, Any],
        session: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a diagnostic plan for the selected strategy."""
        context = context or {}
        strategy = str(selection.get("selected_strategy", "no_trade"))
        if strategy == "ict_liquidity":
            plan = self.ict_liquidity_plan(symbol, context)
        elif strategy == "trend_following":
            plan = self.trend_following_plan(symbol, context)
        elif strategy == "mean_reversion":
            plan = self.mean_reversion_plan(symbol, context)
        else:
            plan = self.no_trade_plan(symbol, selection)
        return {
            **plan,
            "symbol": str(symbol).upper().strip(),
            "selected_strategy": strategy,
            "plan_status": "DIAGNOSTIC_PLAN_READY" if strategy != "no_trade" else "NO_TRADE",
            "plan_quality": "diagnostic_only",
            "execution_allowed": False,
            "advisory_only": True,
            "affects_production": False,
            "pattern": pattern.get("dominant_pattern", "no_clear_pattern"),
            "session_quality": session.get("session_quality", 0),
        }

    @staticmethod
    def ict_liquidity_plan(symbol: str, context: dict[str, Any]) -> dict[str, Any]:
        entry = context.get("fvg_midpoint") or context.get("order_block_midpoint") or context.get("sweep_entry") or 0.0
        return {
            "strategy_name": "ICT Liquidity",
            "entry_logic": "sweep entry, FVG midpoint, or OB midpoint",
            "entry": {"price": entry, "source": "sweep_fvg_ob"},
            "stop_loss": {"price": context.get("sweep_extreme", 0.0), "source": "beyond_sweep_or_ob"},
            "take_profit": {
                "tp1": context.get("internal_liquidity", 0.0),
                "tp2": context.get("external_liquidity", 0.0),
                "tp3": context.get("liquidity_ladder_final", 0.0),
                "source": "liquidity_ladder",
            },
            "reason": "Sweep, MSS, FVG/OB, and session alignment form ICT diagnostic plan",
        }

    @staticmethod
    def trend_following_plan(symbol: str, context: dict[str, Any]) -> dict[str, Any]:
        entry = context.get("pullback_structure") or context.get("pullback_fvg") or context.get("break_retest") or 0.0
        return {
            "strategy_name": "Trend Following",
            "entry_logic": "pullback to structure, pullback to FVG, or continuation break/retest",
            "entry": {"price": entry, "source": "pullback_or_retest"},
            "stop_loss": {"price": context.get("continuation_invalidation", 0.0), "source": "structure_invalidation"},
            "take_profit": {
                "tp1": context.get("prior_high_low", 0.0),
                "tp2": context.get("external_liquidity", 0.0),
                "tp3": context.get("measured_expansion", 0.0),
                "source": "continuation_targets",
            },
            "reason": "Trend strength and continuation structure form diagnostic plan",
        }

    @staticmethod
    def mean_reversion_plan(symbol: str, context: dict[str, Any]) -> dict[str, Any]:
        entry = context.get("failed_breakout_entry") or context.get("raid_rejection_entry") or context.get("fair_value_entry") or 0.0
        return {
            "strategy_name": "Mean Reversion",
            "entry_logic": "failed breakout, liquidity raid rejection, or exhaustion rejection",
            "entry": {"price": entry, "source": "failed_breakout_or_exhaustion"},
            "stop_loss": {"price": context.get("exhaustion_extreme", 0.0), "source": "beyond_exhaustion_extreme"},
            "take_profit": {
                "tp1": context.get("fair_value_midpoint", 0.0),
                "tp2": context.get("opposite_range_boundary", 0.0),
                "tp3": context.get("internal_liquidity", 0.0),
                "source": "fair_value_and_range",
            },
            "reason": "Overextension and rejection form diagnostic mean-reversion plan",
        }

    @staticmethod
    def no_trade_plan(symbol: str, selection: dict[str, Any]) -> dict[str, Any]:
        return {
            "strategy_name": "No Trade",
            "entry_logic": "No advisory edge",
            "entry": {"price": 0.0, "source": "none"},
            "stop_loss": {"price": 0.0, "source": "none"},
            "take_profit": {"tp1": 0.0, "tp2": 0.0, "tp3": 0.0, "source": "none"},
            "reason": selection.get("reason", "No strategy selected"),
        }
