"""Generate Master Sprint 18B portfolio intelligence diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.portfolio_intelligence.portfolio_intelligence_engine import (
    PortfolioIntelligenceEngine,
    write_report_files,
)


def main() -> int:
    """Build reports and print Sprint 18B terminal summary."""
    report = build_portfolio_intelligence_report()
    write_report_files(report, project_root=PROJECT_ROOT)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_portfolio_intelligence_report() -> dict[str, Any]:
    """Return Sprint 18B portfolio intelligence report."""
    return PortfolioIntelligenceEngine(project_root=PROJECT_ROOT).build_report()


def format_terminal(report: dict[str, Any]) -> str:
    """Return requested Sprint 18B terminal summary."""
    windows = report["windows"]
    pas = report["pas"]
    confluence = windows["365D"]["confluence"]
    safety = report["safety"]
    audit = report["audit"]
    lines = [
        "PORTFOLIO INTELLIGENCE UPGRADE",
        "",
        f"Portfolio Intelligence: {report['decision']}",
        f"Timeframe Confluence: {report['timeframe_confluence']['status']}",
        "",
    ]
    for window in ("30D", "90D", "365D"):
        after = windows[window]["after"]
        lines.extend(
            [
                f"{window}:",
                f"PF: {after['pf']}",
                f"WR: {after['win_rate']}%",
                f"Trades: {after['trades']}",
                f"DD: {after['max_drawdown']}%",
                "",
            ]
        )
    lines.extend(
        [
            "PAS:",
            f"Average: {pas['average']}",
            f"Allowed: {pas['allowed']}",
            f"Reduced Risk: {pas['reduced_risk']}",
            f"Suppressed: {pas['suppressed']}",
            "",
            "Confluence:",
            f"Full Stack: {confluence['FULL_STACK_CONFLUENCE']}",
            f"Structural: {confluence['STRUCTURAL_CONFLUENCE']}",
            f"Tactical: {confluence['TACTICAL_CONFLUENCE']}",
            f"Conflict: {confluence['CONFLICT']}",
            "",
            "Safety:",
            f"Production Symbols Only: {report['production_symbols']}",
            f"Sandbox Excluded: {report['sandbox_excluded']}",
            f"Observer Excluded: {report['observer_excluded']}",
            f"Broker Submission Disabled: {safety['broker_submission_disabled']}",
            f"Autonomous Execution Disabled: {safety['autonomous_execution'] is False}",
            f"submit_orders false: {safety['assisted_submit_orders'] is False}",
            "",
            f"Audit Level: {audit['level']}",
            f"Conflicts: {'YES' if audit['conflicts_found'] else 'NO'}",
            f"Production Baseline: {report['production_baseline']}",
            f"Decision: {report['decision']}",
            f"Recommendation: {report['recommendation']}",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
