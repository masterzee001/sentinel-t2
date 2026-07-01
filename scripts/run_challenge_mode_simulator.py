"""Generate Master Sprint 12 challenge mode simulation reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.challenge_mode.challenge_mode_engine import ChallengeModeEngine


REPORT_DIR = PROJECT_ROOT / "data" / "reports"
SIMULATION_PATH = REPORT_DIR / "challenge_mode_simulation.json"
COMPARISON_PATH = REPORT_DIR / "challenge_profile_comparison.json"
MONTHLY_PATH = REPORT_DIR / "challenge_monthly_windows.json"
ROLLING_PATH = REPORT_DIR / "challenge_rolling_2month_windows.json"
MARKDOWN_PATH = REPORT_DIR / "master_sprint_12_challenge_mode.md"


def main() -> int:
    """Build and write challenge mode reports."""
    report = build_challenge_mode_report()
    write_challenge_mode_reports(report)
    print(format_terminal(report))
    return 0 if report.get("challenge_verdict") == "PASSABLE" else 1


def build_challenge_mode_report() -> dict[str, Any]:
    """Return challenge mode report."""
    return ChallengeModeEngine(runs=500).build_report()


def write_challenge_mode_reports(report: dict[str, Any]) -> None:
    """Write challenge mode report artifacts."""
    write_json(SIMULATION_PATH, report)
    write_json(COMPARISON_PATH, report["profile_comparison"])
    write_json(MONTHLY_PATH, report["monthly_windows"])
    write_json(ROLLING_PATH, report["rolling_2month_windows"])
    write_text(MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    lines = ["CHALLENGE MODE SIMULATOR", ""]
    for profile_id in ("profile_0", "profile_1", "profile_2", "profile_3", "profile_4"):
        result = report["profiles"][profile_id]
        lines.extend(
            [
                f"{profile_label(profile_id)}:",
                f"Phase 1: {result['phase_1_pass_probability']}%",
                f"Phase 2: {result['phase_2_pass_probability']}%",
                f"Combined: {result['combined_pass_probability']}%",
                f"Avg Days: {result['average_days_to_pass']}",
                f"Avg Max DD: {result['average_max_dd']}%",
                f"Worst DD: {result['worst_dd']}%",
                f"Decision: {result['decision']}",
                "",
            ]
        )
    monthly = report["monthly_windows"]["summary"]
    rolling = report["rolling_2month_windows"]["summary"]
    lines.extend(
        [
            f"Best Profile: {profile_label(report['best_profile'])}",
            f"Why: {report['profile_comparison']['why']}",
            f"Challenge Verdict: {report['challenge_verdict']}",
            "",
            "Monthly Analysis:",
            f"Best Month: {monthly['best_window'].get('label')}",
            f"Worst Month: {monthly['worst_window'].get('label')}",
            f"Most Consistent Risk Profile: {profile_label(monthly['most_consistent_risk_profile'])}",
            f"Avoid Months: {', '.join(monthly['avoid_windows']) or 'None'}",
            "",
            "2-Month Rolling Analysis:",
            f"Best 2-Month Window: {rolling['best_window'].get('label')}",
            f"Worst 2-Month Window: {rolling['worst_window'].get('label')}",
            f"Most Reliable Risk Profile: {profile_label(rolling['most_consistent_risk_profile'])}",
        ]
    )
    return "\n".join(lines)


def format_markdown(report: dict[str, Any]) -> str:
    """Return markdown report."""
    lines = [
        "# Master Sprint 12 - Challenge Mode Simulator",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Safety",
        "- Simulation only: True",
        "- Production rules modified: False",
        "- Live config changed: False",
        "- Real challenge activation: False",
        "- Broker execution: False",
        "",
        "## Profiles",
    ]
    for profile_id, result in report["profiles"].items():
        lines.append(
            f"- {profile_label(profile_id)}: Phase1 {result['phase_1_pass_probability']}%, "
            f"Phase2 {result['phase_2_pass_probability']}%, Combined {result['combined_pass_probability']}%, "
            f"Avg DD {result['average_max_dd']}%, Worst DD {result['worst_dd']}%, {result['decision']}"
        )
    monthly = report["monthly_windows"]["summary"]
    rolling = report["rolling_2month_windows"]["summary"]
    lines.extend(
        [
            "",
            f"Best Profile: {profile_label(report['best_profile'])}",
            f"Challenge Verdict: {report['challenge_verdict']}",
            "",
            "## Monthly Analysis",
            f"- Best Month: {monthly['best_window'].get('label')}",
            f"- Worst Month: {monthly['worst_window'].get('label')}",
            f"- Most Consistent Risk Profile: {profile_label(monthly['most_consistent_risk_profile'])}",
            f"- Avoid Months: {', '.join(monthly['avoid_windows']) or 'None'}",
            "",
            "## Rolling 2-Month Analysis",
            f"- Best 2-Month Window: {rolling['best_window'].get('label')}",
            f"- Worst 2-Month Window: {rolling['worst_window'].get('label')}",
            f"- Most Reliable Risk Profile: {profile_label(rolling['most_consistent_risk_profile'])}",
            "",
        ]
    )
    return "\n".join(lines)


def profile_label(profile_id: str) -> str:
    return {
        "profile_0": "Profile 0 (0.25%)",
        "profile_1": "Profile 1 (0.50%)",
        "profile_2": "Profile 2 (0.80%)",
        "profile_3": "Profile 3 (1.00%)",
        "profile_4": "Profile 4 (1.20%)",
    }.get(profile_id, profile_id)


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

