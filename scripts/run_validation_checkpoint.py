"""Run the Master Sprint 3 365D validation checkpoint."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.symbols.symbol_registry import SymbolRegistry
from scripts.run_backtest_365d import (
    OBSERVER_DIAGNOSTIC_SYMBOLS,
    approved_robustness_metrics,
    approved_xau_smt_split,
    metrics_within_tolerance,
    normalize_metrics,
    observer_display_name,
    raw_baseline_metrics,
)


SOURCE_REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "backtest_365d_summary.json"
V2_REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "backtest_365d_v2_summary.json"
OBSERVER_DIAGNOSTICS_PATH = PROJECT_ROOT / "data" / "reports" / "symbol_observer_diagnostics.json"
MARKDOWN_REPORT_PATH = PROJECT_ROOT / "data" / "reports" / "master_sprint_3_validation.md"
OBSERVER_SYMBOLS = tuple(OBSERVER_DIAGNOSTIC_SYMBOLS)
APPROVED_DD_LIMIT = 3.0


def main() -> int:
    """Build validation reports and return nonzero when any gate fails."""
    report = load_json(SOURCE_REPORT_PATH)
    if not report:
        print(f"Validation checkpoint failed: missing source report {SOURCE_REPORT_PATH.relative_to(PROJECT_ROOT)}")
        return 1
    checkpoint = build_validation_checkpoint(report, project_root=PROJECT_ROOT)
    write_validation_reports(checkpoint)
    print(format_checkpoint(checkpoint))
    return 0 if checkpoint.get("decision") == "PASS" else 1


def build_validation_checkpoint(
    report: dict[str, Any],
    *,
    project_root: Path | None = None,
    generated_at: str | None = None,
    registry: SymbolRegistry | None = None,
    guardrail_config: dict[str, Any] | None = None,
    execution_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return normalized validation checkpoint data and gate results."""
    root = Path(project_root) if project_root else PROJECT_ROOT
    registry = registry or SymbolRegistry(config_dir=root / "config")
    guardrail_config = guardrail_config if guardrail_config is not None else load_yaml(root / "config" / "strategy_guardrails.yaml")
    execution_config = execution_config if execution_config is not None else load_yaml(root / "config" / "execution.yaml")

    comparison = report.get("comparison", {}) if isinstance(report.get("comparison", {}), dict) else {}
    raw = normalize_metrics(comparison.get("raw_baseline") or report.get("raw_baseline") or raw_baseline_metrics())
    approved = normalize_metrics(
        comparison.get("approved_robustness_baseline")
        or report.get("approved_robustness_baseline")
        or approved_robustness_metrics()
    )
    observer_only = normalize_metrics(
        comparison.get("symbol_expansion_observer_only")
        or report.get("symbol_expansion_observer_only")
        or report.get("production_portfolio", {}).get("metrics", {})
        or report.get("global_metrics", {})
    )
    observer_diagnostics = normalize_observer_diagnostics(report.get("observer_diagnostics", {}))
    xau_smt = deepcopy(report.get("xau_smt_split") or report.get("production_portfolio", {}).get("xau_smt_split") or approved_xau_smt_split())
    gates = build_regression_gates(
        report=report,
        approved=approved,
        observer_only=observer_only,
        observer_diagnostics=observer_diagnostics,
        xau_smt=xau_smt,
        registry=registry,
        guardrail_config=guardrail_config,
        execution_config=execution_config,
    )
    decision = "PASS" if all(gate["pass"] for gate in gates) else "FAIL"
    return {
        "sprint": "Master Sprint 3 - 365D Validation Checkpoint",
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "decision": decision,
        "raw_baseline": raw,
        "approved_robustness_baseline": approved,
        "symbol_expansion_observer_only": observer_only,
        "matches_approved_baseline": metrics_within_tolerance(approved, observer_only),
        "observer_diagnostics": observer_diagnostics,
        "xau_smt": xau_smt,
        "gates": gates,
        "source_report": str(SOURCE_REPORT_PATH.relative_to(PROJECT_ROOT)),
        "next_roadmap_step": "Master Sprint 4 - Market Watch Strategy Intelligence",
    }


def build_regression_gates(
    *,
    report: dict[str, Any],
    approved: dict[str, Any],
    observer_only: dict[str, Any],
    observer_diagnostics: dict[str, dict[str, Any]],
    xau_smt: dict[str, Any],
    registry: SymbolRegistry,
    guardrail_config: dict[str, Any],
    execution_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate the formal Master Sprint 3 regression gates."""
    gates: list[dict[str, Any]] = []

    def add_gate(name: str, passed: bool, detail: str) -> None:
        gates.append({"name": name, "pass": bool(passed), "detail": detail})

    dd = float(observer_only.get("max_drawdown", 0.0))
    production_source = str(report.get("production_portfolio", {}).get("source", ""))
    reconciliation_status = str(report.get("reconciliation", {}).get("status", "MISSING"))

    # Phase 0 replaced the constant-equality gates (pf/wr/trade-count locked to a
    # stored baseline) with integrity gates: metrics must be recomputed from the
    # current scan and must reconcile against their own breakdowns. Drift from
    # the legacy baseline is reported in the comparison block, never gated on.
    add_gate(
        "production_metrics_recomputed_from_scan",
        production_source == "current_backtest_scan",
        f"source={production_source or 'missing'}",
    )
    add_gate(
        "production_reconciles_internally",
        reconciliation_status == "PASS",
        f"reconciliation={reconciliation_status}",
    )
    add_gate("production_dd_below_3", dd <= APPROVED_DD_LIMIT, f"dd={dd:.2f}")
    add_gate(
        "observer_symbols_non_invasive",
        observer_symbols_excluded_from_production(report),
        "observer symbols excluded from production portfolio",
    )
    add_gate(
        "observer_symbols_execution_disabled",
        observer_symbols_execution_disabled(registry, observer_diagnostics),
        "BTC/NAS100/EURUSD/GBPUSD execution disabled",
    )
    add_gate(
        "xau_smt_hard_block_disabled_below_sample",
        xau_smt_hard_block_disabled_below_sample(xau_smt, guardrail_config),
        "SMT hard block disabled while sample < 10",
    )
    add_gate(
        "autonomous_execution_disabled",
        autonomous_execution_disabled(registry, execution_config),
        f"execution_mode={execution_config.get('execution_mode', 'advisor')}",
    )
    return gates


def normalize_observer_diagnostics(raw_diagnostics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return observer diagnostics for all required observer symbols."""
    diagnostics: dict[str, dict[str, Any]] = {}
    for symbol in OBSERVER_SYMBOLS:
        data = raw_diagnostics.get(symbol, {}) if isinstance(raw_diagnostics, dict) else {}
        diagnostics[symbol] = {
            "symbol": symbol,
            "display_symbol": observer_display_name(symbol),
            "data_status": data.get("data_status", "MISSING"),
            "trades": int(data.get("trades", data.get("metrics", {}).get("trades", 0)) or 0),
            "pf": float(data.get("pf", data.get("profit_factor", data.get("metrics", {}).get("profit_factor", 0.0))) or 0.0),
            "wr": float(data.get("wr", data.get("win_rate", data.get("metrics", {}).get("win_rate", 0.0))) or 0.0),
            "execution_allowed": bool(data.get("execution_allowed", False)),
            "production_excluded": bool(data.get("production_excluded", True)),
            "historical_data": bool(data.get("historical_data", False)),
            "candles_available": bool(data.get("candles_available", False)),
            "setups_forming": bool(data.get("setups_forming", False)),
            "guardrails_blocking_all_opportunities": bool(data.get("guardrails_blocking_all_opportunities", False)),
        }
    return diagnostics


def observer_symbols_excluded_from_production(report: dict[str, Any]) -> bool:
    """Return whether observer symbols are absent from production portfolio results."""
    observer_set = set(OBSERVER_SYMBOLS)
    production_symbols = {str(symbol).upper() for symbol in report.get("production_symbols", [])}
    breakdown_symbols = {str(symbol).upper() for symbol in report.get("symbol_breakdown", {}).keys()}
    production_breakdown = {
        str(symbol).upper()
        for symbol in report.get("production_portfolio", {}).get("symbol_breakdown", {}).keys()
    }
    return (
        not (observer_set & production_symbols)
        and not (observer_set & breakdown_symbols)
        and not (observer_set & production_breakdown)
    )


def observer_symbols_execution_disabled(registry: SymbolRegistry, diagnostics: dict[str, dict[str, Any]]) -> bool:
    """Return whether every observer/disabled symbol remains non-executable."""
    for symbol in OBSERVER_SYMBOLS:
        if registry.execution_allowed(symbol):
            return False
        if diagnostics.get(symbol, {}).get("execution_allowed"):
            return False
    return True


def xau_smt_hard_block_disabled_below_sample(xau_smt: dict[str, Any], guardrail_config: dict[str, Any]) -> bool:
    """Return whether XAU SMT hard blocking is disabled before enough SMT samples exist."""
    robustness = guardrail_config.get("robustness_365d", {}) if isinstance(guardrail_config, dict) else {}
    minimum = int(robustness.get("xauusd_smt_min_sample", xau_smt.get("rule", {}).get("minimum_smt_sample", 10)) or 10)
    sample = int(robustness.get("xauusd_smt_sample_trades", xau_smt.get("rule", {}).get("current_smt_sample", 0)) or 0)
    hard_block_enabled = bool(xau_smt.get("rule", {}).get("hard_block_enabled", sample >= minimum))
    return sample >= minimum or not hard_block_enabled


def autonomous_execution_disabled(registry: SymbolRegistry, execution_config: dict[str, Any]) -> bool:
    """Return whether current config prevents autonomous execution."""
    registry_autonomous = bool(registry.config.get("execution", {}).get("autonomous_execution_allowed", False))
    execution_mode = str(execution_config.get("execution_mode", "advisor")).lower().strip()
    return execution_mode != "autonomous" and not registry_autonomous


def write_validation_reports(checkpoint: dict[str, Any]) -> None:
    """Write all Master Sprint 3 report artifacts."""
    write_json(V2_REPORT_PATH, checkpoint)
    write_json(
        OBSERVER_DIAGNOSTICS_PATH,
        {
            "generated_at": checkpoint.get("generated_at"),
            "decision": checkpoint.get("decision"),
            "observer_diagnostics": checkpoint.get("observer_diagnostics", {}),
        },
    )
    write_text(MARKDOWN_REPORT_PATH, format_markdown_report(checkpoint))


def format_checkpoint(checkpoint: dict[str, Any]) -> str:
    """Return terminal output for the validation checkpoint."""
    raw = checkpoint.get("raw_baseline", {})
    approved = checkpoint.get("approved_robustness_baseline", {})
    observer = checkpoint.get("symbol_expansion_observer_only", {})
    lines = [
        "SENTINEL 365D VALIDATION CHECKPOINT",
        "",
        "A. Raw Baseline:",
        f"PF: {raw.get('pf', 0.0)}",
        f"WR: {raw.get('win_rate', 0.0)}%",
        f"Trades: {raw.get('trades', 0)}",
        f"DD: {raw.get('max_drawdown', 0.0)}%",
        "",
        "B. Approved Robustness Baseline:",
        f"PF: {approved.get('pf', 0.0)}",
        f"WR: {approved.get('win_rate', 0.0)}%",
        f"Trades: {approved.get('trades', 0)}",
        f"DD: {approved.get('max_drawdown', 0.0)}%",
        "",
        "C. Observer-Only Production Comparison:",
        f"PF: {observer.get('pf', 0.0)}",
        f"WR: {observer.get('win_rate', 0.0)}%",
        f"Trades: {observer.get('trades', 0)}",
        f"DD: {observer.get('max_drawdown', 0.0)}%",
        f"Matches Approved Baseline: {checkpoint.get('matches_approved_baseline', False)}",
        "",
        "D. Observer Diagnostics:",
    ]
    for symbol in OBSERVER_SYMBOLS:
        data = checkpoint.get("observer_diagnostics", {}).get(symbol, {})
        lines.extend(
            [
                f"{data.get('display_symbol', symbol)}:",
                f"Data Status: {data.get('data_status', 'MISSING')}",
                f"Trades: {data.get('trades', 0)}",
                f"PF: {data.get('pf', 0.0)}",
                f"WR: {data.get('wr', 0.0)}%",
            ]
        )
    xau = checkpoint.get("xau_smt", {})
    lines.extend(
        [
            "",
            "XAU SMT:",
            f"With SMT: trades {xau.get('with_smt', {}).get('trades_approved', 0)}, PF {xau.get('with_smt', {}).get('profit_factor', 0.0)}, WR {xau.get('with_smt', {}).get('win_rate', 0.0)}%",
            f"Without SMT: trades {xau.get('without_smt', {}).get('trades_approved', 0)}, PF {xau.get('without_smt', {}).get('profit_factor', 0.0)}, WR {xau.get('without_smt', {}).get('win_rate', 0.0)}%",
            f"SMT Dependency: {xau.get('dependency', 'NO_SMT_SAMPLE')}",
            f"Hard SMT Block: {xau.get('rule', {}).get('hard_block_enabled', False)}",
            "",
            "Decision:",
            str(checkpoint.get("decision", "FAIL")),
        ]
    )
    return "\n".join(lines)


def format_markdown_report(checkpoint: dict[str, Any]) -> str:
    """Return the markdown validation report."""
    raw = checkpoint.get("raw_baseline", {})
    approved = checkpoint.get("approved_robustness_baseline", {})
    observer = checkpoint.get("symbol_expansion_observer_only", {})
    xau = checkpoint.get("xau_smt", {})
    lines = [
        "# Master Sprint 3 - 365D Validation Checkpoint",
        "",
        f"Date/time: {checkpoint.get('generated_at')}",
        "",
        "## Approved Baseline",
        f"- PF: {approved.get('pf', 0.0)}",
        f"- WR: {approved.get('win_rate', 0.0)}%",
        f"- Trades: {approved.get('trades', 0)}",
        f"- DD: {approved.get('max_drawdown', 0.0)}%",
        "",
        "## Raw Baseline",
        f"- PF: {raw.get('pf', 0.0)}",
        f"- WR: {raw.get('win_rate', 0.0)}%",
        f"- Trades: {raw.get('trades', 0)}",
        f"- DD: {raw.get('max_drawdown', 0.0)}%",
        "",
        "## Observer-Only Comparison",
        f"- PF: {observer.get('pf', 0.0)}",
        f"- WR: {observer.get('win_rate', 0.0)}%",
        f"- Trades: {observer.get('trades', 0)}",
        f"- DD: {observer.get('max_drawdown', 0.0)}%",
        f"- Matches approved baseline: {checkpoint.get('matches_approved_baseline', False)}",
        "",
        "## Observer Diagnostics",
    ]
    for symbol in OBSERVER_SYMBOLS:
        data = checkpoint.get("observer_diagnostics", {}).get(symbol, {})
        lines.append(f"- {data.get('display_symbol', symbol)}: {data.get('data_status', 'MISSING')}, trades {data.get('trades', 0)}, PF {data.get('pf', 0.0)}, WR {data.get('wr', 0.0)}%")
    lines.extend(
        [
            "",
            "## XAU SMT Status",
            f"- With SMT: trades {xau.get('with_smt', {}).get('trades_approved', 0)}, PF {xau.get('with_smt', {}).get('profit_factor', 0.0)}, WR {xau.get('with_smt', {}).get('win_rate', 0.0)}%",
            f"- Without SMT: trades {xau.get('without_smt', {}).get('trades_approved', 0)}, PF {xau.get('without_smt', {}).get('profit_factor', 0.0)}, WR {xau.get('without_smt', {}).get('win_rate', 0.0)}%",
            f"- SMT dependency: {xau.get('dependency', 'NO_SMT_SAMPLE')}",
            f"- Hard SMT block: {xau.get('rule', {}).get('hard_block_enabled', False)}",
            "",
            "## Regression Gates",
        ]
    )
    for gate in checkpoint.get("gates", []):
        status = "PASS" if gate.get("pass") else "FAIL"
        lines.append(f"- {gate.get('name')}: {status} ({gate.get('detail', '')})")
    lines.extend(
        [
            "",
            f"## Decision: {checkpoint.get('decision', 'FAIL')}",
            "",
            "## Next Roadmap Step",
            checkpoint.get("next_roadmap_step", "Master Sprint 4 - Market Watch Strategy Intelligence"),
            "",
        ]
    )
    return "\n".join(lines)


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file or return empty config."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file or return empty data."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically enough for local report generation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temp_path.replace(path)


def write_text(path: Path, text: str) -> None:
    """Write text report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
