"""Generate Sprint 9 Guardrail Optimization Engine reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.guardrail_optimization.guardrail_optimization_engine import GuardrailOptimizationEngine


ATTRIBUTION_PATH = PROJECT_ROOT / "data" / "reports" / "guardrail_attribution.json"
LEAK_PATH = PROJECT_ROOT / "data" / "reports" / "guardrail_leak_analysis.json"
RELAXATION_PATH = PROJECT_ROOT / "data" / "reports" / "conditional_relaxation_simulation.json"
SYMBOL_LOCK_PATH = PROJECT_ROOT / "data" / "reports" / "symbol_lock_optimization.json"
NO_TRADE_PATH = PROJECT_ROOT / "data" / "reports" / "no_trade_optimization.json"
A_PLUS_PATH = PROJECT_ROOT / "data" / "reports" / "a_plus_override_simulation.json"
IQ_V6_PATH = PROJECT_ROOT / "data" / "reports" / "market_watch_iq_v6.json"
MASTER_MARKDOWN_PATH = PROJECT_ROOT / "data" / "reports" / "master_sprint_9_guardrail_optimization.md"


def main() -> int:
    """Build and write GOE reports."""
    report = build_guardrail_optimization_report()
    write_guardrail_optimization_reports(report)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_guardrail_optimization_report() -> dict[str, Any]:
    """Return Sprint 9 GOE report."""
    return GuardrailOptimizationEngine().build_report()


def write_guardrail_optimization_reports(report: dict[str, Any]) -> None:
    """Write Sprint 9 report artifacts."""
    write_json(ATTRIBUTION_PATH, {"generated_at": report["generated_at"], "attribution": report["guardrail_attribution"]})
    write_json(LEAK_PATH, report["guardrail_leak_analysis"])
    write_json(RELAXATION_PATH, report["conditional_relaxation"])
    write_json(SYMBOL_LOCK_PATH, report["symbol_lock_optimization"])
    write_json(NO_TRADE_PATH, report["no_trade_optimization"])
    write_json(A_PLUS_PATH, report["a_plus_override_simulation"])
    write_json(IQ_V6_PATH, report["market_watch_iq_v6"])
    write_text(MASTER_MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    leak = report["guardrail_leak_analysis"]
    scenarios = report["conditional_relaxation"]
    symbol = report["symbol_lock_optimization"]
    no_trade = report["no_trade_optimization"]
    a_plus = report["a_plus_override_simulation"]
    iq = report["market_watch_iq_v6"]
    original = report["original_elite"]
    optimized = report["optimized_hypothetical"]
    return "\n".join(
        [
            "GUARDRAIL OPTIMIZATION ENGINE",
            "",
            f"Best Guardrail: {leak.get('best_guardrail')}",
            f"Worst Guardrail: {leak.get('worst_guardrail')}",
            "",
            "Conditional Relaxation:",
            f"Scenario 1: {metrics_line(scenarios['scenario_1_relax_symbol_lock_conditionally']['metrics'])}",
            f"Scenario 2: {metrics_line(scenarios['scenario_2_relax_no_trade_conditionally']['metrics'])}",
            f"Scenario 3: {metrics_line(scenarios['scenario_3_a_plus_override_layer']['metrics'])}",
            f"Scenario 4: {metrics_line(scenarios['scenario_4_combined_controlled_relaxation']['metrics'])}",
            "",
            "Symbol Lock Optimization:",
            *[
                f"{name}: {data['recommendation']}"
                for name, data in sorted(symbol.items(), key=lambda item: int(item[1].get("rank", 999) or 999))
            ],
            "",
            f"No-Trade Optimization: {no_trade.get('pass')}",
            f"A+ Override: {a_plus.get('pass')}",
            "",
            "Market Watch IQ V6:",
            f"Guardrail Leak IQ: {iq.get('guardrail_leak_iq')}",
            f"Efficiency Score: {iq.get('guardrail_efficiency_score')}",
            f"Relaxation Benefit: {iq.get('relaxation_benefit_score')}",
            f"Safe Candidates: {', '.join(iq.get('safe_relaxation_candidates', []))}",
            "",
            f"Original Elite: {metrics_line(original)}",
            f"Optimized Hypothetical: {metrics_line(optimized)}",
            f"Production Baseline Preserved: {report.get('production_baseline_preserved')}",
            f"Decision: {report.get('decision')}",
        ]
    )


def format_markdown(report: dict[str, Any]) -> str:
    """Return Sprint 9 markdown report."""
    lines = [
        "# Master Sprint 9 - Guardrail Optimization Engine",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        "## Safety",
        "- Mode: advisory-only optimization",
        "- Broker submission: False",
        "- Autonomous execution: False",
        "- Live rules modified: False",
        "- Production metrics affected: False",
        "",
        "## Leak Analysis",
        f"- Best guardrail: {report['guardrail_leak_analysis'].get('best_guardrail')}",
        f"- Worst guardrail: {report['guardrail_leak_analysis'].get('worst_guardrail')}",
        "",
        "## Conditional Relaxation",
    ]
    for name, scenario in report["conditional_relaxation"].items():
        lines.append(f"- {name}: {metrics_line(scenario['metrics'])}")
    lines.extend(
        [
            "",
            "## Market Watch IQ V6",
            f"- Guardrail Leak IQ: {report['market_watch_iq_v6'].get('guardrail_leak_iq')}",
            f"- Efficiency Score: {report['market_watch_iq_v6'].get('guardrail_efficiency_score')}",
            f"- Relaxation Benefit: {report['market_watch_iq_v6'].get('relaxation_benefit_score')}",
            f"- Safe Candidates: {', '.join(report['market_watch_iq_v6'].get('safe_relaxation_candidates', []))}",
            "",
            f"Original Elite: {metrics_line(report['original_elite'])}",
            f"Optimized Hypothetical: {metrics_line(report['optimized_hypothetical'])}",
            f"Decision: {report.get('decision')}",
            "Recommendation: preserve killzone and grade lock; review symbol lock, no-trade, and A+ late block handling diagnostically.",
            "",
        ]
    )
    return "\n".join(lines)


def metrics_line(metrics: dict[str, Any]) -> str:
    return f"PF {metrics.get('pf')}, WR {metrics.get('win_rate')}%, Trades {metrics.get('trades')}, DD {metrics.get('max_drawdown')}%"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temp_path.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
