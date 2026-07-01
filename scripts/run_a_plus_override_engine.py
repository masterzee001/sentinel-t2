"""Generate Master Sprint 11 A+ Override Engine reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.a_plus_override.a_plus_override_engine import APlusOverrideEngine


REPORT_DIR = PROJECT_ROOT / "data" / "reports"
SEVERITY_PATH = REPORT_DIR / "block_severity_database.json"
BACKTEST_PATH = REPORT_DIR / "a_plus_override_backtest.json"
IQ_V8_PATH = REPORT_DIR / "market_watch_iq_v8.json"
MARKDOWN_PATH = REPORT_DIR / "master_sprint_11_a_plus_override.md"


def main() -> int:
    """Build and write Sprint 11 reports."""
    report = build_a_plus_override_report()
    write_a_plus_override_reports(report)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_a_plus_override_report() -> dict[str, Any]:
    """Return Sprint 11 report."""
    return APlusOverrideEngine().build_report()


def write_a_plus_override_reports(report: dict[str, Any]) -> None:
    """Write Sprint 11 report artifacts."""
    write_json(SEVERITY_PATH, report["block_severity_database"])
    write_json(BACKTEST_PATH, report["a_plus_override_backtest"])
    write_json(IQ_V8_PATH, report["market_watch_iq_v8"])
    write_text(MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    backtest = report["a_plus_override_backtest"]
    iq = report["market_watch_iq_v8"]
    original = report["original_elite"]
    enhanced = report["override_enhanced"]
    return "\n".join(
        [
            "A+ OVERRIDE ENGINE",
            "",
            f"Severity Engine: {'PASS' if report['block_severity_database']['classification_accuracy'] == 100.0 else 'FAIL'}",
            f"Override Eligible Count: {iq['eligible_count']}",
            f"Override Accuracy: {iq['override_accuracy']}%",
            f"False Override Rate: {iq['false_override_rate']}%",
            "",
            f"Recovered Trades: {backtest['recovered_trades']}",
            f"Allowed Overrides: {backtest['allowed_overrides']}",
            f"Denied Overrides: {backtest['denied_overrides']}",
            "",
            f"Original Elite: {metrics_line(original)}",
            f"Override Enhanced: {metrics_line(enhanced)}",
            f"Production Baseline Preserved: {report['production_baseline_preserved']}",
            f"Decision: {report['decision']}",
        ]
    )


def format_markdown(report: dict[str, Any]) -> str:
    """Return markdown report."""
    iq = report["market_watch_iq_v8"]
    return "\n".join(
        [
            "# Master Sprint 11 - A+ Override Engine",
            "",
            f"Generated: {report['generated_at']}",
            "",
            "## Safety",
            "- Advisory-only: True",
            "- Production rules modified: False",
            "- Live override enabled: False",
            "- Kill switch bypassed: False",
            "- Broker execution: False",
            "- Autonomous execution: False",
            "",
            "## Market Watch IQ V8",
            f"- Override Accuracy: {iq['override_accuracy']}%",
            f"- False Override Rate: {iq['false_override_rate']}%",
            f"- Override Benefit Score: {iq['override_benefit_score']}",
            f"- Severity Classification Accuracy: {iq['severity_classification_accuracy']}%",
            "",
            f"Original Elite: {metrics_line(report['original_elite'])}",
            f"Override Enhanced: {metrics_line(report['override_enhanced'])}",
            f"Decision: {report['decision']}",
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

