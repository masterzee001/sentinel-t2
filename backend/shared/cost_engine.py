"""Single cost modeling surface for replay, demo, paper, and live diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_COST_MODES = ("ideal", "normal", "stressed")


@dataclass(frozen=True)
class CostInput:
    """Execution-cost inputs common to all Sentinel modes."""

    spread_points: float = 0.0
    slippage_points: float = 0.0
    latency_ms: int = 0
    fill_probability: float = 1.0
    approval_delay_seconds: float = 0.0
    mode: str = "normal"


def estimate_execution_cost(cost: CostInput | dict[str, Any]) -> dict[str, Any]:
    """Return a normalized execution-cost estimate.

    This function centralizes cost terminology. Existing engines can adopt it
    incrementally without changing live execution behavior.
    """
    item = cost if isinstance(cost, CostInput) else CostInput(**{key: value for key, value in cost.items() if key in CostInput.__dataclass_fields__})
    mode = item.mode if item.mode in SUPPORTED_COST_MODES else "normal"
    stress_multiplier = {"ideal": 0.0, "normal": 1.0, "stressed": 1.5}[mode]
    latency_penalty = max(float(item.latency_ms), 0.0) / 1000.0
    approval_penalty = max(float(item.approval_delay_seconds), 0.0) / 60.0
    total_points = (
        max(float(item.spread_points), 0.0)
        + max(float(item.slippage_points), 0.0) * stress_multiplier
        + latency_penalty
        + approval_penalty
    )
    fill_probability = max(min(float(item.fill_probability), 1.0), 0.0)
    return {
        "mode": mode,
        "spread_points": round(float(item.spread_points), 4),
        "slippage_points": round(float(item.slippage_points), 4),
        "latency_ms": int(item.latency_ms),
        "approval_delay_seconds": round(float(item.approval_delay_seconds), 4),
        "fill_probability": round(fill_probability, 4),
        "total_cost_points": round(total_points, 4),
        "status": "PASS" if fill_probability > 0 else "UNFILLABLE",
    }


def cost_coverage_audit() -> dict[str, Any]:
    """Return cost-model coverage by mode, verified by source inspection.

    Every coverage claim is recomputed from the actual module sources so this
    audit can fail. Modes without cost-engine usage are reported NOT_ENFORCED
    instead of being asserted as covered.
    """
    from backend.shared.shared_decision_adapter import _source_contains

    def coverage_for(module_path: str, enforced_label: str) -> str:
        if _source_contains(module_path, None, "estimate_execution_cost"):
            return enforced_label
        return "NOT_ENFORCED"

    coverage = {
        "replay": coverage_for("backend.backtesting.trade_simulator", "ENFORCED_IN_TRADE_SIMULATOR"),
        "backtest": coverage_for("backend.backtesting.trade_simulator", "ENFORCED_IN_TRADE_SIMULATOR"),
        "demo": coverage_for("backend.execution_engine.assisted_execution_bridge", "ENFORCED_IN_ASSISTED_BRIDGE"),
        "paper": coverage_for("backend.live_paper.live_paper_runtime", "ENFORCED_IN_LIVE_PAPER_RUNTIME"),
        "live": coverage_for("backend.execution_engine.execution_engine", "ENFORCED_AS_VALIDATION_DIAGNOSTIC"),
    }
    violations = [
        {
            "severity": "MEDIUM",
            "file": mode,
            "function": "cost_coverage",
            "violation": f"Cost engine is not enforced in {mode} mode.",
        }
        for mode, label in coverage.items()
        if label == "NOT_ENFORCED"
    ]
    enforced_everywhere = not violations
    backtest_enforced = coverage["backtest"] != "NOT_ENFORCED"
    return {
        "status": "PASS" if enforced_everywhere else ("PARTIAL" if backtest_enforced else "FAIL"),
        "single_cost_engine_available": True,
        "single_cost_engine_enforced": enforced_everywhere,
        "coverage": coverage,
        "remaining_violations": violations,
    }
