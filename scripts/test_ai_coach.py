"""Manual smoke test for the Project Sentinel AI Coach."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.ai_coach.coach_analyzer import AICoachAnalyzer, AICoachError


SAMPLE_BACKTEST_SUMMARY = {
    "overall": {
        "profit_factor": 1.75,
        "win_rate": 61.29,
        "trades_approved": 45,
        "max_drawdown": 1.0,
        "net_rr": 9.0,
    },
    "after_guardrails": {
        "profit_factor": 1.75,
        "win_rate": 61.29,
        "trades_approved": 45,
        "max_drawdown": 1.0,
        "net_rr": 9.0,
    },
    "by_symbol": {
        "XAUUSD": {"profit_factor": 2.2, "win_rate": 66.0, "trades_approved": 25, "average_rr": 0.42},
        "US30": {"profit_factor": 1.4, "win_rate": 57.0, "trades_approved": 20, "average_rr": 0.18},
        "GBPUSD": {"profit_factor": 0.8, "win_rate": 38.0, "trades_approved": 8, "average_rr": -0.2},
    },
    "by_killzone": {
        "new_york_open": {"profit_factor": 2.1, "win_rate": 68.0, "trades_approved": 22, "average_rr": 0.5},
        "london_open": {"profit_factor": 1.3, "win_rate": 54.0, "trades_approved": 18, "average_rr": 0.1},
        "london_continuation": {"profit_factor": 0.7, "win_rate": 35.0, "trades_approved": 5, "average_rr": -0.25},
    },
    "by_confidence_band": {
        "EXECUTION_READY": {"profit_factor": 2.0, "win_rate": 64.0, "trades_approved": 30, "average_rr": 0.4},
        "HOT": {"profit_factor": 1.1, "win_rate": 48.0, "trades_approved": 15, "average_rr": 0.05},
    },
    "by_narrative_phase": {
        "expansion": {"profit_factor": 2.0, "win_rate": 65.0, "trades_approved": 21, "average_rr": 0.45},
        "range": {"profit_factor": 0.75, "win_rate": 36.0, "trades_approved": 9, "average_rr": -0.15},
    },
    "guardrail_impact": {
        "before": {"trades": 52, "winrate": 55.0, "profit_factor": 1.3},
        "after": {"trades": 45, "winrate": 61.29, "profit_factor": 1.75},
        "trades_removed": 7,
        "worst_filtered_condition": "Range phase penalty",
    },
}


def main() -> int:
    """Print a clean AI Coach report."""
    logger.remove()
    try:
        analyzer = AICoachAnalyzer()
        records = analyzer.read_journal_records()
        source = "local journal"
        if not records:
            records = analyzer.synthetic_journal_records()
            source = "synthetic fallback"

        report = analyzer.analyze(
            journal_records=records,
            backtest_summary=SAMPLE_BACKTEST_SUMMARY,
            use_synthetic_if_empty=False,
        )
        print_report(report, source=source, record_count=len(records))
        return 0
    except AICoachError as exc:
        print(f"AI Coach test failed: {exc}")
        return 1


def print_report(report: dict[str, Any], *, source: str, record_count: int) -> None:
    """Print a clean terminal coach report."""
    print("AI COACH REPORT")
    print("---------------")
    print(f"Status: {report.get('coach_status', 'UNKNOWN')}")
    print(f"Journal Source: {source} ({record_count} records)")
    print(report.get("summary", "No summary available."))
    print("")
    print_items("Strengths", report.get("strengths", []))
    print_items("Weaknesses", report.get("weaknesses", []))
    print_items("Recommendations", report.get("recommendations", []))
    print_items("Risk Notes", report.get("risk_notes", []))
    print_items("Next Actions", report.get("next_actions", []))
    print("")
    print("Advisor Mode only: no live trade execution was taken.")


def print_items(title: str, items: list[dict[str, Any]]) -> None:
    """Print normalized coach items."""
    print(title.upper())
    print("-" * len(title))
    if not items:
        print("- none")
        print("")
        return
    for item in items:
        print(f"- [{item.get('severity', 'INFO')}] {item.get('category', 'psychology')}: {item.get('message', '')}")
    print("")


if __name__ == "__main__":
    raise SystemExit(main())

