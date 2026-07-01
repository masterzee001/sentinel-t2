"""Generate Master Sprint 18 controlled production-promotion reports."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.production_promotion.production_promotion_engine import (
    ProductionPromotionEngine,
    write_report_files,
)


def main() -> int:
    """Build reports and print Sprint 18 terminal summary."""
    report = build_production_promotion_report()
    write_report_files(report, project_root=PROJECT_ROOT)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_production_promotion_report() -> dict[str, Any]:
    """Return Sprint 18 production-promotion report."""
    return ProductionPromotionEngine(project_root=PROJECT_ROOT).build_report()


def format_terminal(report: dict[str, Any]) -> str:
    """Return requested Sprint 18 terminal summary."""
    windows = report["windows"]
    audit = report["audit"]
    safety = report["safety"]
    lines = [
        "CONTROLLED PRODUCTION PROMOTION",
        "",
        f"EFDE Promotion: {report['efde_promotion']['status']}",
        f"A+ Promotion: {report['a_plus_promotion']['status']}",
        f"Memory Promotion: {report['memory_promotion']['status']}",
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
            f"Audit Level: {audit['level']}",
            f"Conflicts: {'YES' if audit['conflicts_found'] else 'NO'}",
            f"Production Baseline Preserved: {'YES' if report['production_baseline_integrity'] else 'NO'}",
            f"Submit Orders: {safety['assisted_submit_orders']}",
            f"Autonomous Execution: {safety['autonomous_execution']}",
            f"Decision: {report['decision']}",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
