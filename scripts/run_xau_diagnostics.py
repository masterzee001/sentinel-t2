"""Print XAUUSD diagnostic breakdowns from the cached 365D report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.symbols.symbol_diagnostics import SymbolDiagnostics


def main() -> int:
    """Load cached report and print XAU diagnostics."""
    args = parse_args()
    report = SymbolDiagnostics.load_report(args.report)
    diagnostics = SymbolDiagnostics.xau_diagnostics(report)
    print(format_report(diagnostics))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run XAUUSD deep diagnostics from cached backtest data.")
    parser.add_argument("--report", default=str(PROJECT_ROOT / "data" / "reports" / "backtest_365d_summary.json"))
    return parser.parse_args()


def format_report(diagnostics: dict) -> str:
    """Return terminal-friendly XAU report."""
    lines = [
        "XAU DIAGNOSTICS",
        f"Best Killzone: {diagnostics.get('best_killzone', 'none')}",
        f"Worst Killzone: {diagnostics.get('worst_killzone', 'none')}",
        f"SMT Dependency: {diagnostics.get('smt_dependency', 'UNKNOWN')}",
        "",
        "By Killzone:",
    ]
    for name, metrics in diagnostics.get("by_killzone", {}).items():
        lines.append(
            f"- {name}: trades={metrics.get('trades', metrics.get('trades_approved', 0))}, "
            f"PF={metrics.get('profit_factor', metrics.get('pf', 0.0))}, "
            f"WR={metrics.get('win_rate', metrics.get('winrate', 0.0))}, "
            f"avgRR={metrics.get('avg_rr', metrics.get('average_rr', 0.0))}, "
            f"DD={metrics.get('max_drawdown', 0.0)}"
        )
    lines.extend(["", "By Narrative:"])
    for name, metrics in diagnostics.get("by_narrative", {}).items():
        lines.append(
            f"- {name}: trades={metrics.get('trades', metrics.get('trades_approved', 0))}, "
            f"PF={metrics.get('profit_factor', metrics.get('pf', 0.0))}, "
            f"WR={metrics.get('win_rate', metrics.get('winrate', 0.0))}"
        )
    answers = diagnostics.get("answers", {})
    lines.extend(
        [
            "",
            "Answers:",
            f"1. XAU profitable only NY: {answers.get('xau_profitable_only_ny')}",
            f"2. SMT mandatory: {answers.get('smt_mandatory')}",
            f"3. Loss leaks: {answers.get('loss_leaks')}",
        ]
    )
    if diagnostics.get("fallback_summary_used"):
        lines.append("")
        lines.append("Note: cached report is using aggregate XAU diagnostics; rerun the 365D backtest to refresh trade-level splits.")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
