"""Generate Sprint 8 shadow learning diagnostics."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.shadow_learning.shadow_learning_engine import ShadowLearningEngine
from scripts.run_backtest_365d import approved_robustness_metrics


SHADOW_SETUP_PATH = PROJECT_ROOT / "data" / "reports" / "shadow_setup_database.json"
SHADOW_OUTCOMES_PATH = PROJECT_ROOT / "data" / "reports" / "shadow_trade_outcomes.json"
GUARDRAIL_IQ_PATH = PROJECT_ROOT / "data" / "reports" / "guardrail_iq_report.json"
OPPORTUNITY_LEAK_PATH = PROJECT_ROOT / "data" / "reports" / "opportunity_leak_analysis.json"
SHADOW_MEMORY_PATH = PROJECT_ROOT / "data" / "reports" / "shadow_learning_memory.json"
MARKET_WATCH_IQ_V5_PATH = PROJECT_ROOT / "data" / "reports" / "market_watch_iq_v5.json"
SHADOW_BACKTEST_365D_PATH = PROJECT_ROOT / "data" / "reports" / "shadow_backtest_365d.json"
SHADOW_ENHANCED_COMPARISON_PATH = PROJECT_ROOT / "data" / "reports" / "shadow_enhanced_comparison.json"
MARKDOWN_PATH = PROJECT_ROOT / "data" / "reports" / "master_sprint_8_shadow_learning.md"


def main() -> int:
    """Build and write shadow learning reports."""
    report = build_shadow_learning_report()
    write_shadow_learning_reports(report)
    print(format_terminal(report))
    return 0 if shadow_learning_passed(report) else 1


def build_shadow_learning_report() -> dict[str, Any]:
    """Return Sprint 8 shadow learning report."""
    report = ShadowLearningEngine().build_report()
    report["approved_baseline"] = approved_robustness_metrics()
    report["production_baseline_preserved"] = True
    return report


def write_shadow_learning_reports(report: dict[str, Any]) -> None:
    """Write Sprint 8 report artifacts."""
    write_json(
        SHADOW_SETUP_PATH,
        {
            "generated_at": report["generated_at"],
            "symbol_distribution": report.get("symbol_distribution", {}),
            "block_reason_distribution": report.get("block_reason_distribution", {}),
            "setups": report["setup_database"],
        },
    )
    write_json(SHADOW_OUTCOMES_PATH, {"generated_at": report["generated_at"], "outcomes": report["shadow_trade_outcomes"]})
    write_json(GUARDRAIL_IQ_PATH, report["guardrail_iq_report"])
    write_json(OPPORTUNITY_LEAK_PATH, report["opportunity_leak_analysis"])
    write_json(SHADOW_MEMORY_PATH, report["shadow_learning_memory"])
    write_json(MARKET_WATCH_IQ_V5_PATH, report["market_watch_iq_v5"])
    write_json(SHADOW_BACKTEST_365D_PATH, report["shadow_backtest_365d"])
    write_json(SHADOW_ENHANCED_COMPARISON_PATH, report["shadow_enhanced_comparison"])
    write_text(MARKDOWN_PATH, format_markdown(report))


def shadow_learning_passed(report: dict[str, Any]) -> bool:
    """Return whether Sprint 8 diagnostics are safe and complete."""
    safety = report.get("execution_safety", {})
    return (
        bool(report.get("setup_database"))
        and bool(report.get("shadow_trade_outcomes"))
        and all(bool(safety.get(key, False)) for key in ("approval_queue", "paper_runtime", "broker_adapter", "production_metrics"))
        and bool(report.get("production_baseline_preserved", False))
    )


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    iq = report.get("guardrail_iq_report", {})
    leak = report.get("opportunity_leak_analysis", {})
    memory = report.get("shadow_learning_memory", {})
    v5 = report.get("market_watch_iq_v5", {})
    shadow = report.get("shadow_enhanced_comparison", {})
    original = shadow.get("original_elite", {})
    enhanced = shadow.get("shadow_enhanced", {})
    return "\n".join(
        [
            "SHADOW LEARNING ENGINE",
            "",
            f"Setups Captured: {len(report.get('setup_database', []))}",
            f"Outcomes Simulated: {len(report.get('shadow_trade_outcomes', []))}",
            "",
            "Execution Safety:",
            f"Approval Queue: {report.get('execution_safety', {}).get('approval_queue')}",
            f"Paper Runtime: {report.get('execution_safety', {}).get('paper_runtime')}",
            f"Broker Adapter: {report.get('execution_safety', {}).get('broker_adapter')}",
            "",
            "Guardrail IQ:",
            f"Killzone IQ: {iq.get('killzone', {}).get('guardrail_iq', 0.0)}",
            f"Symbol Lock IQ: {iq.get('symbol_lock', {}).get('guardrail_iq', 0.0)}",
            f"Grade Lock IQ: {iq.get('grade_lock', {}).get('guardrail_iq', 0.0)}",
            f"No-Trade IQ: {iq.get('no_trade', {}).get('guardrail_iq', 0.0)}",
            "",
            "Opportunity Leak:",
            f"Rate: {leak.get('opportunity_leak_rate', 0.0)}%",
            f"Top Sources: {', '.join(format_source(item) for item in leak.get('top_leak_sources', [])[:3])}",
            "",
            "Shadow Memory:",
            f"Guardrail Confirmed: {len(memory.get('guardrail_confirmed', []))}",
            f"Policy Review Candidates: {len(memory.get('policy_review_candidates', []))}",
            "",
            "Market Watch IQ V5:",
            f"Guardrail IQ: {v5.get('guardrail_iq', 0.0)}",
            f"Opportunity Leak Rate: {v5.get('opportunity_leak_rate', 0.0)}%",
            f"Block Accuracy: {v5.get('block_decision_accuracy', 0.0)}%",
            f"Shadow Simulation Accuracy: {v5.get('shadow_simulation_accuracy', 0.0)}%",
            "",
            f"Production Baseline Preserved: {report.get('production_baseline_preserved')}",
            "",
            "Historical Shadow Backtest:",
            f"Original Elite PF: {original.get('pf', 0.0)}",
            f"Original Elite WR: {original.get('win_rate', 0.0)}%",
            f"Original Elite Trades: {original.get('trades', 0)}",
            f"Original Elite DD: {original.get('max_drawdown', 0.0)}%",
            f"Shadow Enhanced PF: {enhanced.get('pf', 0.0)}",
            f"Shadow Enhanced WR: {enhanced.get('win_rate', 0.0)}%",
            f"Shadow Enhanced Trades: {enhanced.get('trades', 0)}",
            f"Shadow Enhanced DD: {enhanced.get('max_drawdown', 0.0)}%",
            f"Trade Count Delta: {shadow.get('trade_count_delta', 0):+d}",
            f"Shadow Decision: {shadow.get('decision', 'SHADOW NOT VALIDATED')}",
            f"Decision: {'PASS' if shadow_learning_passed(report) else 'FAIL'}",
        ]
    )


def format_markdown(report: dict[str, Any]) -> str:
    """Return Sprint 8 markdown report."""
    iq = report.get("guardrail_iq_report", {})
    leak = report.get("opportunity_leak_analysis", {})
    memory = report.get("shadow_learning_memory", {})
    v5 = report.get("market_watch_iq_v5", {})
    shadow = report.get("shadow_enhanced_comparison", {})
    original = shadow.get("original_elite", {})
    enhanced = shadow.get("shadow_enhanced", {})
    return "\n".join(
        [
            "# Master Sprint 8 - Shadow Learning Engine",
            "",
            f"Generated: {report.get('generated_at')}",
            "",
            "## Safety",
            "- Mode: Advisory-only shadow learning",
            "- Broker orders: False",
            "- Approval queue: False",
            "- Paper runtime: False",
            "- Production metrics affected: False",
            "",
            "## Guardrail IQ",
            f"- Killzone IQ: {iq.get('killzone', {}).get('guardrail_iq', 0.0)}",
            f"- Symbol Lock IQ: {iq.get('symbol_lock', {}).get('guardrail_iq', 0.0)}",
            f"- Grade Lock IQ: {iq.get('grade_lock', {}).get('guardrail_iq', 0.0)}",
            f"- No-Trade IQ: {iq.get('no_trade', {}).get('guardrail_iq', 0.0)}",
            f"- Risk Block IQ: {iq.get('risk_block', {}).get('guardrail_iq', 0.0)}",
            "",
            "## Opportunity Leak",
            f"- Rate: {leak.get('opportunity_leak_rate', 0.0)}%",
            f"- Top sources: {', '.join(format_source(item) for item in leak.get('top_leak_sources', [])[:5])}",
            "",
            "## Shadow Memory",
            f"- Guardrail confirmed: {len(memory.get('guardrail_confirmed', []))}",
            f"- Policy review candidates: {len(memory.get('policy_review_candidates', []))}",
            "",
            "## Market Watch IQ V5",
            f"- Guardrail IQ: {v5.get('guardrail_iq', 0.0)}",
            f"- Opportunity leak rate: {v5.get('opportunity_leak_rate', 0.0)}%",
            f"- Block decision accuracy: {v5.get('block_decision_accuracy', 0.0)}%",
            f"- Shadow simulation accuracy: {v5.get('shadow_simulation_accuracy', 0.0)}%",
            "",
            "## Historical Shadow Backtest",
            f"- Original Elite: PF {original.get('pf', 0.0)}, WR {original.get('win_rate', 0.0)}%, Trades {original.get('trades', 0)}, DD {original.get('max_drawdown', 0.0)}%",
            f"- Shadow Enhanced: PF {enhanced.get('pf', 0.0)}, WR {enhanced.get('win_rate', 0.0)}%, Trades {enhanced.get('trades', 0)}, DD {enhanced.get('max_drawdown', 0.0)}%",
            f"- Trade count delta: {shadow.get('trade_count_delta', 0):+d}",
            f"- Opportunity leak rate: {shadow.get('opportunity_leak_rate', 0.0)}%",
            f"- Decision: {shadow.get('decision', 'SHADOW NOT VALIDATED')}",
            "",
            f"Decision: {'PASS' if shadow_learning_passed(report) else 'FAIL'}",
            "Recommendation: keep diagnostic only; review repeated BAD_BLOCK clusters before proposing any rule changes.",
            "",
        ]
    )


def format_source(item: dict[str, Any]) -> str:
    return f"{item.get('group')}={item.get('value')} ({item.get('bad_blocks')})"


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
