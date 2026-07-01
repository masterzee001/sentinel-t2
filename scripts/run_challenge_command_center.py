"""Generate Master Sprint 12.2 Challenge Command Center reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.challenge_mode.challenge_command_center import ChallengeCommandCenter


REPORT_DIR = PROJECT_ROOT / "data" / "reports"
REPORT_PATH = REPORT_DIR / "challenge_command_center.json"
MARKDOWN_PATH = REPORT_DIR / "master_sprint_12_2_command_center.md"


def main() -> int:
    """Build and write Challenge Command Center reports."""
    report = build_challenge_command_center_report()
    write_challenge_command_center_reports(report)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_challenge_command_center_report() -> dict[str, Any]:
    """Return command-center report."""
    return ChallengeCommandCenter().build_report()


def write_challenge_command_center_reports(report: dict[str, Any]) -> None:
    """Write report artifacts."""
    write_json(REPORT_PATH, report)
    write_text(MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    checks = report["checks"]
    return "\n".join(
        [
            "CHALLENGE COMMAND CENTER",
            "",
            f"Dashboard: {pass_fail(checks['dashboard'])}",
            f"Telegram: {pass_fail(checks['telegram'])}",
            f"Governor: {pass_fail(checks['governor'])}",
            f"Recommendation Engine: {pass_fail(checks['recommendation_engine'])}",
            f"Challenge Progress Tracking: {pass_fail(checks['challenge_progress_tracking'])}",
            f"Production Baseline Preserved: {checks['production_baseline_preserved']}",
            f"Decision: {report['decision']}",
        ]
    )


def format_markdown(report: dict[str, Any]) -> str:
    """Return markdown report."""
    status = report["challenge_status"]
    profit = report["profit_progress"]
    risk = report["risk_buffer"]
    governor = report["governor_status"]
    performance = report["trading_performance"]
    recommendation = report["recommendation"]
    return "\n".join(
        [
            "# Master Sprint 12.2 - Challenge Command Center",
            "",
            f"Generated: {report['generated_at']}",
            "",
            "## Safety",
            "- Advisory + dashboard only: True",
            "- Challenge mode enabled: False",
            "- Broker orders: False",
            "- Autonomous execution: False",
            "- Live execution modified: False",
            "",
            "## Challenge Status",
            f"- Challenge Mode: {status['challenge_mode']}",
            f"- Profile: {status['profile']}",
            f"- Current Phase: {status['current_phase']}",
            f"- Status: {status['status']}",
            "",
            "## Profit Progress",
            f"- Starting Balance: {profit['starting_balance']}",
            f"- Current Balance: {profit['current_balance']}",
            f"- Current Equity: {profit['current_equity']}",
            f"- Net PnL: {profit['net_pnl']} ({profit['net_pnl_percent']}%)",
            f"- Remaining Target: {profit['remaining_target']} ({profit['remaining_target_percent']}%)",
            "",
            "## Risk Buffer",
            f"- Daily Used: {risk['daily_loss_limit']['current_used_percent']}%",
            f"- Total DD Used: {risk['total_drawdown_limit']['current_used_percent']}%",
            f"- State: {risk['color_state']}",
            "",
            "## Governor",
            f"- Risk Mode: {governor['risk_mode']}",
            f"- Current Risk: {governor['current_risk_percent']}%",
            f"- Loss Streak: {governor['loss_streak']}",
            f"- Alerts: {', '.join(governor['alerts']) or 'none'}",
            "",
            "## Trading Performance",
            f"- Trades: {performance['trades_taken']}",
            f"- WR: {performance['win_rate']}%",
            f"- PF: {performance['pf']}",
            f"- Avg RR: {performance['avg_rr']}",
            f"- Avg Loss: {performance['avg_loss']}",
            f"- DD: {performance['dd']}%",
            f"- EFDE Saves: {performance['efde_saves']}",
            f"- A+ Override Saves: {performance['a_plus_override_saves']}",
            "",
            "## Recommendation",
            f"- Recommendation: {recommendation['recommendation']}",
            f"- Confidence: {recommendation['confidence']}",
            "",
            f"Decision: {report['decision']}",
            "",
        ]
    )


def pass_fail(value: bool) -> str:
    return "PASS" if value else "FAIL"


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
