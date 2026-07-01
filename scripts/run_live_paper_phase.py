"""Generate Sprint 7 live paper phase reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.live_paper.live_paper_runtime import LivePaperRuntime, paper_phase_classification


SESSION_PATH = PROJECT_ROOT / "data" / "reports" / "live_paper_session.json"
DRIFT_PATH = PROJECT_ROOT / "data" / "reports" / "live_execution_drift.json"
MARKDOWN_PATH = PROJECT_ROOT / "data" / "reports" / "master_sprint_7_live_paper.md"


def main() -> int:
    """Run the paper-only phase report generator."""
    report = LivePaperRuntime().run_sample_session()
    write_live_paper_reports(report)
    print(format_terminal(report))
    return 0 if report.get("runtime_ready") else 1


def write_live_paper_reports(report: dict[str, Any]) -> None:
    """Write Sprint 7 live paper reports."""
    write_json(SESSION_PATH, report)
    write_json(DRIFT_PATH, report.get("drift", {}))
    write_text(MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    health = report.get("live_feed_health", {})
    stats = report.get("paper_stats", {})
    drift = report.get("drift", {})
    return "\n".join(
        [
            "LIVE PAPER PHASE SUMMARY",
            "",
            "Mode: PAPER_ONLY",
            "Broker Orders: DISABLED",
            "Autonomous Execution: DISABLED",
            "",
            "Live Feed Health:",
            f"Score: {health.get('score', 0.0)}",
            f"Status: {health.get('classification', 'UNUSABLE')}",
            "",
            "Paper Stats:",
            f"PF: {stats.get('pf', 0.0)}",
            f"WR: {stats.get('win_rate', 0.0)}%",
            f"Trades: {stats.get('trades', 0)}",
            f"DD: {stats.get('max_drawdown', 0.0)}%",
            f"Avg RR: {stats.get('avg_rr', 0.0)}",
            f"Avg Spread: {stats.get('avg_spread', 0.0)}",
            f"Avg Slippage: {stats.get('avg_slippage', 0.0)}",
            f"Avg Latency: {stats.get('avg_latency', 0.0)}ms",
            "",
            "Drift:",
            f"PF Drift: {drift.get('pf_drift', 0.0)}%",
            f"WR Drift: {drift.get('wr_drift', 0.0)}%",
            f"Execution Drift: {drift.get('execution_drift', 0.0)}%",
            f"Classification: {drift.get('classification', 'MAJOR DRIFT')}",
            "",
            f"Paper Classification: {paper_phase_classification(stats)}",
            f"Paper Runtime: {'Ready' if report.get('runtime_ready') else 'Not Ready'}",
        ]
    )


def format_markdown(report: dict[str, Any]) -> str:
    """Return Sprint 7 markdown report."""
    health = report.get("live_feed_health", {})
    stats = report.get("paper_stats", {})
    drift = report.get("drift", {})
    return "\n".join(
        [
            "# Master Sprint 7 - Live Paper Phase Framework",
            "",
            f"Generated: {report.get('generated_at')}",
            "",
            "## Safety",
            "- Mode: PAPER_ONLY",
            "- Broker order submission: False",
            "- Autonomous execution: False",
            "- Guardrails required: True",
            "- Readiness required: True",
            "",
            "## Live Feed Health",
            f"- Score: {health.get('score', 0.0)}",
            f"- Classification: {health.get('classification', 'UNUSABLE')}",
            f"- Missing candles: {health.get('missing_candles', 0)}",
            f"- Delayed candles: {health.get('delayed_candles', 0)}",
            f"- Spread anomalies: {health.get('broker_spread_anomalies', 0)}",
            "",
            "## Paper Trade Stats",
            f"- PF: {stats.get('pf', 0.0)}",
            f"- WR: {stats.get('win_rate', 0.0)}%",
            f"- Trades: {stats.get('trades', 0)}",
            f"- DD: {stats.get('max_drawdown', 0.0)}%",
            f"- Avg RR: {stats.get('avg_rr', 0.0)}",
            f"- Avg spread: {stats.get('avg_spread', 0.0)}",
            f"- Avg slippage: {stats.get('avg_slippage', 0.0)}",
            f"- Avg latency: {stats.get('avg_latency', 0.0)}ms",
            "",
            "## Execution Drift",
            f"- PF drift: {drift.get('pf_drift', 0.0)}%",
            f"- WR drift: {drift.get('wr_drift', 0.0)}%",
            f"- Execution drift: {drift.get('execution_drift', 0.0)}%",
            f"- Classification: {drift.get('classification', 'MAJOR DRIFT')}",
            "",
            f"Paper classification: {paper_phase_classification(stats)}",
            f"Runtime ready: {report.get('runtime_ready')}",
            "",
        ]
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temp_path.replace(path)


def write_text(path: Path, text: str) -> None:
    """Write text atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
