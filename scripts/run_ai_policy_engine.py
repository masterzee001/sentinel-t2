"""Generate Master Sprint 10 AI Policy Engine reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.ai_policy.ai_policy_engine import AIPolicyEngine


REPORT_DIR = PROJECT_ROOT / "data" / "reports"
AI_RECOMMENDATIONS_PATH = REPORT_DIR / "ai_policy_recommendations.json"
AI_MEMORY_PATH = REPORT_DIR / "ai_policy_memory.json"
IQ_V7_PATH = REPORT_DIR / "market_watch_iq_v7.json"
MASTER_MARKDOWN_PATH = REPORT_DIR / "master_sprint_10_ai_policy_engine.md"

INPUT_PATHS = {
    "setup_expectancy_database": REPORT_DIR / "setup_expectancy_database.json",
    "loss_memory_database": REPORT_DIR / "loss_memory_database.json",
    "shadow_learning_memory": REPORT_DIR / "shadow_learning_memory.json",
    "guardrail_leak_analysis": REPORT_DIR / "guardrail_leak_analysis.json",
    "regime_strategy_expectancy": REPORT_DIR / "regime_strategy_expectancy.json",
    "market_watch_iq_report": REPORT_DIR / "market_watch_iq_report.json",
    "market_watch_iq_v2": REPORT_DIR / "market_watch_iq_v2.json",
    "market_watch_iq_v3": REPORT_DIR / "market_watch_iq_v3.json",
    "market_watch_iq_v4": REPORT_DIR / "market_watch_iq_v4.json",
    "market_watch_iq_v5": REPORT_DIR / "market_watch_iq_v5.json",
    "market_watch_iq_v6": REPORT_DIR / "market_watch_iq_v6.json",
    "symbol_lock_optimization": REPORT_DIR / "symbol_lock_optimization.json",
    "no_trade_optimization": REPORT_DIR / "no_trade_optimization.json",
    "a_plus_override_simulation": REPORT_DIR / "a_plus_override_simulation.json",
    "shadow_enhanced_comparison": REPORT_DIR / "shadow_enhanced_comparison.json",
}


def main() -> int:
    """Build and write Sprint 10 reports."""
    report = build_ai_policy_report()
    write_ai_policy_reports(report)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_ai_policy_report() -> dict[str, Any]:
    """Return Sprint 10 AI policy report."""
    return AIPolicyEngine(load_memory()).build_report()


def load_memory() -> dict[str, Any]:
    """Load all available AI policy memory inputs."""
    memory: dict[str, Any] = {}
    for key, path in INPUT_PATHS.items():
        memory[key] = read_json(path)
    return memory


def write_ai_policy_reports(report: dict[str, Any]) -> None:
    """Write Sprint 10 report artifacts."""
    write_json(
        AI_RECOMMENDATIONS_PATH,
        {
            "generated_at": report["generated_at"],
            "mode": report["mode"],
            "recommendations": report["recommendations"],
            "recommendation_count": report["recommendation_count"],
            "recommendation_accuracy": report["recommendation_accuracy"],
            "false_recommendation_rate": report["false_recommendation_rate"],
            "safety": safety_block(report),
        },
    )
    write_json(AI_MEMORY_PATH, report["ai_policy_memory"])
    write_json(IQ_V7_PATH, report["market_watch_iq_v7"])
    write_text(MASTER_MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return requested terminal summary."""
    top = report["top_recommendations"]
    return "\n".join(
        [
            "AI POLICY ENGINE",
            "",
            f"AI Recommendation Count: {report['recommendation_count']}",
            f"Recommendation Accuracy: {report['recommendation_accuracy']}%",
            f"False Recommendation Rate: {report['false_recommendation_rate']}%",
            "",
            "Top Recommendations:",
            *[f"- {item['id']}: PCS {item['pcs']} ({item['strength']})" for item in top],
            "",
            "Market Watch IQ V7:",
            f"AI Policy IQ: {report['market_watch_iq_v7'].get('ai_policy_iq')}",
            f"Average PCS: {report['market_watch_iq_v7'].get('average_pcs')}",
            f"Strong Recommendations: {report['market_watch_iq_v7'].get('strong_recommendations')}",
            "",
            "Safety:",
            f"Production Logic Modified: {report.get('production_logic_modified')}",
            f"Broker Submission: {report.get('broker_order_submission')}",
            f"Autonomous Execution: {report.get('autonomous_execution')}",
            f"Human Approval Bypassed: {report.get('human_approval_bypassed')}",
            "",
            f"Decision: {report.get('decision')}",
        ]
    )


def format_markdown(report: dict[str, Any]) -> str:
    """Return Sprint 10 markdown report."""
    lines = [
        "# Master Sprint 10 - AI Policy Engine",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        "## Safety",
        "- Mode: advisory-only AI",
        "- Production logic modified: False",
        "- Broker submission: False",
        "- Autonomous execution: False",
        "- Human approval bypassed: False",
        "",
        "## Results",
        f"- AI Recommendation Count: {report['recommendation_count']}",
        f"- Recommendation Accuracy: {report['recommendation_accuracy']}%",
        f"- False Recommendation Rate: {report['false_recommendation_rate']}%",
        f"- AI Policy IQ: {report['market_watch_iq_v7'].get('ai_policy_iq')}",
        "",
        "## Top Recommendations",
    ]
    for item in report["top_recommendations"]:
        lines.append(f"- {item['id']}: PCS {item['pcs']} - {item['title']}")
    lines.extend(["", f"Decision: {report.get('decision')}", ""])
    return "\n".join(lines)


def safety_block(report: dict[str, Any]) -> dict[str, bool]:
    """Return safety flags for JSON output."""
    return {
        "production_logic_modified": bool(report.get("production_logic_modified")),
        "broker_order_submission": bool(report.get("broker_order_submission")),
        "autonomous_execution": bool(report.get("autonomous_execution")),
        "human_approval_bypassed": bool(report.get("human_approval_bypassed")),
    }


def read_json(path: Path) -> dict[str, Any]:
    """Return JSON object or an empty mapping if the input is absent."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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

