"""Backtest summary cache helpers for Project Sentinel reporting."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "reports" / "latest_backtest_summary.json"
PHASE_3_QUALIFIED = "Phase 3 Qualified: Execution Automation Research"
PHASE_3_OPTIMIZE = "Continue Optimization"


def save_backtest_summary(summary: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """Normalize and atomically save the latest backtest summary cache."""
    cache_path = resolve_cache_path(path)
    payload = normalize_backtest_summary(summary)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(cache_path)
    return payload


def load_backtest_summary(path: str | Path | None = None) -> dict[str, Any] | None:
    """Load the latest backtest summary cache or return None when unavailable."""
    cache_path = resolve_cache_path(path)
    if not cache_path.exists() or not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return normalize_backtest_summary(data)
    except Exception as exc:
        logger.warning("Failed to load backtest summary cache {}: {}", cache_path, exc)
        return None


def normalize_backtest_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Return the stable latest_backtest_summary.json schema."""
    data = summary or {}
    generated_at = str(data.get("generated_at") or datetime.now(UTC).isoformat())
    days_30 = extract_window_metrics(data, "30")
    days_90 = extract_window_metrics(data, "90")
    days_365 = extract_window_metrics(data, "365")
    phase_decision = str(data.get("phase_decision") or build_phase_decision(days_90))
    production_portfolio = data.get("production_portfolio", {})
    xau_smt_split = data.get("xau_smt_split") or production_portfolio.get("xau_smt_split", {})
    return {
        "generated_at": generated_at,
        "adaptive_guardrails": bool(data.get("adaptive_guardrails", True)),
        "days_30": days_30,
        "days_90": days_90,
        "days_365": days_365,
        "phase_decision": phase_decision,
        "comparison": data.get("comparison", {}),
        "raw_baseline": data.get("raw_baseline", {}),
        "approved_robustness_baseline": data.get("approved_robustness_baseline", {}),
        "symbol_expansion": data.get("symbol_expansion", {}),
        "symbol_expansion_observer_only": data.get("symbol_expansion_observer_only", {}),
        "production_portfolio": production_portfolio,
        "production_recalculation_diagnostics": data.get("production_recalculation_diagnostics", {}),
        "symbol_breakdown": data.get("symbol_breakdown", {}),
        "observer_diagnostics": data.get("observer_diagnostics", {}),
        "xau_smt_split": xau_smt_split,
    }


def extract_window_metrics(data: dict[str, Any], days: str) -> dict[str, float | int]:
    """Extract one 30D/90D adaptive metrics block from known backtest shapes."""
    adaptive_section = data.get("adaptive_guardrails", {})
    if not isinstance(adaptive_section, dict):
        adaptive_section = {}
    candidates = [
        data.get(f"days_{days}", {}),
        data.get("production_portfolio", {}).get("metrics", {}) if days == "365" else {},
        data.get("global_metrics", {}) if days == "365" else {},
        data.get(days, {}).get("adaptive_guardrails", {}).get("overall", {}),
        data.get(int(days), {}).get("adaptive_guardrails", {}).get("overall", {}) if days.isdigit() else {},
        data.get(f"{days}_day", {}).get("adaptive_guardrails", {}).get("overall", {}),
        data.get(f"{days}d", {}).get("adaptive_guardrails", {}).get("overall", {}),
        data.get("long_horizon", {}).get(days, {}).get("adaptive_guardrails", {}).get("overall", {}),
        adaptive_section.get(days, {}).get("overall", {}),
    ]
    metrics = next((candidate for candidate in candidates if candidate), {})
    return {
        "pf": round_float(metrics.get("pf", metrics.get("profit_factor", 0.0))),
        "win_rate": round_float(metrics.get("win_rate", 0.0)),
        "trades": int(metrics.get("trades", metrics.get("trades_approved", 0)) or 0),
        "max_drawdown": round_float(metrics.get("max_drawdown", 0.0)),
        "net_rr": round_float(metrics.get("net_rr", 0.0)),
    }


def build_phase_decision(days_90: dict[str, Any]) -> str:
    """Return the cache phase decision from 90-day adaptive metrics."""
    if (
        float(days_90.get("pf", 0.0) or 0.0) > 1.5
        and float(days_90.get("win_rate", 0.0) or 0.0) > 50.0
        and float(days_90.get("max_drawdown", 0.0) or 0.0) < 6.0
    ):
        return PHASE_3_QUALIFIED
    return PHASE_3_OPTIMIZE


def short_phase_decision(summary: dict[str, Any]) -> str:
    """Return concise phase text for compact Telegram and dashboard views."""
    decision = str(summary.get("phase_decision", "") or "")
    if "Phase 3 Qualified" in decision or "qualifies for Phase 3" in decision:
        return "Phase 3 Qualified"
    if decision:
        return decision
    return "No phase decision available."


def round_float(value: Any) -> float:
    """Return a report-friendly float."""
    try:
        return round(float(value or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def resolve_cache_path(path: str | Path | None = None) -> Path:
    """Resolve cache paths relative to the repository root."""
    if path is None:
        return DEFAULT_CACHE_PATH
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate
