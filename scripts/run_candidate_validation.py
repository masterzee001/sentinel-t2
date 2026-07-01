"""Generate Master Sprint 9.1 controlled candidate validation reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.candidate_validation.candidate_validation_engine import CandidateValidationEngine
from backend.guardrail_optimization.guardrail_optimization_engine import GuardrailOptimizationEngine


REPORT_DIR = PROJECT_ROOT / "data" / "reports"
CANDIDATE_VALIDATION_PATH = REPORT_DIR / "candidate_validation_report.json"
CANDIDATE_STRESS_PATH = REPORT_DIR / "candidate_stress_report.json"
CANDIDATE_CORRELATION_PATH = REPORT_DIR / "candidate_correlation_report.json"
MARKDOWN_PATH = REPORT_DIR / "master_sprint_9_1_candidate_validation.md"


def main() -> int:
    """Build and write Sprint 9.1 reports."""
    report = build_candidate_validation_report()
    write_candidate_validation_reports(report)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_candidate_validation_report() -> dict[str, Any]:
    """Return Sprint 9.1 report."""
    goe_report = GuardrailOptimizationEngine().build_report()
    return CandidateValidationEngine(goe_report=goe_report).build_report()


def write_candidate_validation_reports(report: dict[str, Any]) -> None:
    """Write Sprint 9.1 report artifacts."""
    write_json(
        CANDIDATE_VALIDATION_PATH,
        {
            "generated_at": report["generated_at"],
            "mode": report["mode"],
            "original_elite": report["original_elite"],
            "candidate_validation": report["candidate_validation"],
            "candidate_decisions": report["candidate_decisions"],
            "ranking": report["ranking"],
            "best_candidate": report["best_candidate"],
            "production_baseline_preserved": report["production_baseline_preserved"],
            "production_policy_changed": report["production_policy_changed"],
            "live_config_changed": report["live_config_changed"],
            "decision": report["decision"],
        },
    )
    write_json(
        CANDIDATE_STRESS_PATH,
        {
            "generated_at": report["generated_at"],
            "candidate_stress": report["candidate_stress"],
            "candidate_decisions": report["candidate_decisions"],
        },
    )
    write_json(
        CANDIDATE_CORRELATION_PATH,
        {
            "generated_at": report["generated_at"],
            "candidate_correlation": report["candidate_correlation"],
        },
    )
    write_text(MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    lines = [
        "CONTROLLED CANDIDATE VALIDATION",
        "",
    ]
    for candidate_id in ("candidate_1", "candidate_2", "candidate_3", "candidate_4"):
        validation = report["candidate_validation"][candidate_id]
        stress = report["candidate_stress"][candidate_id]
        lines.extend(
            [
                candidate_label(candidate_id) + ":",
                f"30D: {metrics_line(validation['30D'])}",
                f"90D: {metrics_line(validation['90D'])}",
                f"365D: {metrics_line(validation['365D'])}",
                f"Stress: {'PASS' if stress['pass'] else 'FAIL'} {','.join(stress.get('fail_reasons', []))}",
                f"PF/DD Efficiency: {validation['pf_dd_efficiency']}",
                f"Decision: {report['candidate_decisions'][candidate_id]}",
                "",
            ]
        )
    lines.extend(
        [
            "Ranking:",
            *[f"{row['rank']}. {candidate_label(row['candidate_id'])} - {row['decision']} - efficiency {row['pf_dd_efficiency']}" for row in report["ranking"]],
            "",
            f"Best Candidate: {candidate_label(report['best_candidate']['candidate_id'])}",
            f"Reason: {report['best_candidate']['reason']}",
            f"Production Baseline Preserved: {report['production_baseline_preserved']}",
            f"Decision: {report['decision']}",
        ]
    )
    return "\n".join(lines)


def format_markdown(report: dict[str, Any]) -> str:
    """Return markdown report."""
    lines = [
        "# Master Sprint 9.1 - Controlled Candidate Validation",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Safety",
        "- Advisory only: True",
        "- Production policy changed: False",
        "- Live config changed: False",
        "- Broker execution: False",
        "- Autonomous execution: False",
        "",
        "## Candidates",
    ]
    for candidate_id in ("candidate_1", "candidate_2", "candidate_3", "candidate_4"):
        validation = report["candidate_validation"][candidate_id]
        stress = report["candidate_stress"][candidate_id]
        lines.extend(
            [
                f"### {candidate_label(candidate_id)}",
                f"- 30D: {metrics_line(validation['30D'])}",
                f"- 90D: {metrics_line(validation['90D'])}",
                f"- 365D: {metrics_line(validation['365D'])}",
                f"- Stress: {'PASS' if stress['pass'] else 'FAIL'} {','.join(stress.get('fail_reasons', []))}",
                f"- PF/DD efficiency: {validation['pf_dd_efficiency']}",
                f"- Decision: {report['candidate_decisions'][candidate_id]}",
                "",
            ]
        )
    lines.extend(
        [
            "## Ranking",
            *[f"{row['rank']}. {candidate_label(row['candidate_id'])} - {row['decision']} - efficiency {row['pf_dd_efficiency']}" for row in report["ranking"]],
            "",
            f"Best Candidate: {candidate_label(report['best_candidate']['candidate_id'])}",
            f"Reason: {report['best_candidate']['reason']}",
            f"Decision: {report['decision']}",
            "",
        ]
    )
    return "\n".join(lines)


def candidate_label(candidate_id: str) -> str:
    return {
        "candidate_1": "Candidate 1 - Conditional Symbol Lock Relaxation",
        "candidate_2": "Candidate 2 - Institutional Continuation No-Trade Relaxation",
        "candidate_3": "Candidate 3 - A+ Override Layer",
        "candidate_4": "Candidate 4 - Combined Controlled Relaxation",
    }.get(candidate_id, candidate_id)


def metrics_line(metrics: dict[str, Any]) -> str:
    return f"PF {metrics['pf']}, WR {metrics['win_rate']}%, Trades {metrics['trades']}, DD {metrics['max_drawdown']}%, AvgRR {metrics.get('avg_rr', 'n/a')}, Tail {metrics.get('tail_risk', 'n/a')}"


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

