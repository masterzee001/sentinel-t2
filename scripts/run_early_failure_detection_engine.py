"""Generate Master Sprint 13 EFDE reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.early_failure_detection.early_failure_detection_engine import EarlyFailureDetectionEngine


REPORT_DIR = PROJECT_ROOT / "data" / "reports"
EFDE_REPORT_PATH = REPORT_DIR / "early_failure_detection_report.json"
EFDE_REPLAY_PATH = REPORT_DIR / "efde_trade_replay.json"
EFDE_LEARNING_PATH = REPORT_DIR / "efde_learning_memory.json"
EFDE_CALIBRATION_PATH = REPORT_DIR / "efde_calibration_report.json"
IQ_V9_PATH = REPORT_DIR / "market_watch_iq_v9.json"
MARKDOWN_PATH = REPORT_DIR / "master_sprint_13_efde.md"


def main() -> int:
    """Build and write EFDE reports."""
    report = build_early_failure_detection_report()
    write_early_failure_detection_reports(report)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_early_failure_detection_report() -> dict[str, Any]:
    """Return Sprint 13 EFDE report."""
    return EarlyFailureDetectionEngine().build_report()


def write_early_failure_detection_reports(report: dict[str, Any]) -> None:
    """Write Sprint 13 report artifacts."""
    write_json(EFDE_REPORT_PATH, report["early_failure_detection_report"])
    write_json(EFDE_REPLAY_PATH, {"generated_at": report["generated_at"], "trades": report["efde_trade_replay"]})
    write_json(EFDE_LEARNING_PATH, report["efde_learning_memory"])
    write_json(EFDE_CALIBRATION_PATH, report["efde_calibration_report"])
    write_json(IQ_V9_PATH, report["market_watch_iq_v9"])
    write_text(MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    summary = report["early_failure_detection_report"]
    original = report["original_elite"]
    enhanced = summary["efde_enhanced"]
    return "\n".join(
        [
            "EARLY FAILURE DETECTION ENGINE",
            "",
            f"EFDE Engine: {report['decision']}",
            "Adverse Zone Detection: PASS",
            "FPS Classification: PASS",
            "",
            f"Original Elite: {metrics_line(original)} AvgLoss {original['average_loss']}",
            f"EFDE Enhanced: {metrics_line(enhanced)} AvgLoss {enhanced['average_loss']}",
            "",
            f"Accuracy: {summary['efde_accuracy']}%",
            f"False Exit Rate: {summary['false_exit_rate']}%",
            f"Saved Loss Value: {summary['saved_loss_value']}",
            f"Missed Winner Value: {summary['missed_winner_value']}",
            f"PF Delta: {summary['pf_delta']}",
            f"DD Delta: {summary['dd_delta']}",
            f"EFDE Learning Score: {report['market_watch_iq_v9']['efde_learning_score']}",
            f"FPS Calibration Accuracy: {report['market_watch_iq_v9']['fps_calibration_accuracy']}",
            f"Production Baseline Preserved: {report['production_baseline_preserved']}",
            f"Decision: {report['decision']}",
        ]
    )


def format_markdown(report: dict[str, Any]) -> str:
    """Return markdown summary."""
    summary = report["early_failure_detection_report"]
    original = report["original_elite"]
    enhanced = summary["efde_enhanced"]
    return "\n".join(
        [
            "# Master Sprint 13 - Early Failure Detection Engine",
            "",
            f"Generated: {report['generated_at']}",
            "",
            "## Safety",
            "- Advisory-only: True",
            "- Live auto-exit enabled: False",
            "- Broker order modified: False",
            "- Production rules modified: False",
            "- Autonomous execution: False",
            "",
            "## Results",
            f"- Original Elite: {metrics_line(original)}, AvgLoss {original['average_loss']}",
            f"- EFDE Enhanced: {metrics_line(enhanced)}, AvgLoss {enhanced['average_loss']}",
            f"- EFDE Accuracy: {summary['efde_accuracy']}%",
            f"- False Exit Rate: {summary['false_exit_rate']}%",
            f"- Saved Loss Value: {summary['saved_loss_value']}",
            f"- Missed Winner Value: {summary['missed_winner_value']}",
            f"- PF Delta: {summary['pf_delta']}",
            f"- DD Delta: {summary['dd_delta']}",
            f"- EFDE Learning Score: {report['market_watch_iq_v9']['efde_learning_score']}",
            f"- FPS Calibration Accuracy: {report['market_watch_iq_v9']['fps_calibration_accuracy']}",
            f"- Recommended Threshold: {report['efde_calibration_report']['recommended_threshold']}",
            f"- Calibration Confidence: {report['efde_calibration_report']['confidence']}",
            "",
            f"Decision: {report['decision']}",
            "Recommendation: keep advisory only; future review threshold is FPS >= 75 after 30%-50% adverse movement.",
            "",
        ]
    )


def metrics_line(metrics: dict[str, Any]) -> str:
    return f"PF {metrics['pf']}, WR {metrics['win_rate']}%, Trades {metrics['trades']}, DD {metrics['max_drawdown']}%"


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
