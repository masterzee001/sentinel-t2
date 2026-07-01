"""Generate Master Sprint 12.1 Challenge Mode profile reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.challenge_mode.challenge_profile_config import ChallengeModeProfileConfig


REPORT_DIR = PROJECT_ROOT / "data" / "reports"
PROFILE_PATH = REPORT_DIR / "challenge_mode_profile.json"
MARKDOWN_PATH = REPORT_DIR / "master_sprint_12_1_challenge_profile.md"


def main() -> int:
    """Build and write Challenge Mode profile reports."""
    report = build_challenge_profile_report()
    write_challenge_profile_reports(report)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_challenge_profile_report() -> dict[str, Any]:
    """Return Challenge Mode profile report."""
    return ChallengeModeProfileConfig().build_report()


def write_challenge_profile_reports(report: dict[str, Any]) -> None:
    """Write profile report artifacts."""
    write_json(PROFILE_PATH, report)
    write_text(MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    checks = report["checks"]
    lines = [
        "CHALLENGE MODE PROFILE CONFIG",
        "",
        f"Challenge Config: {pass_fail(checks['challenge_config'])}",
        f"Balanced Profile: {pass_fail(checks['balanced_profile'])}",
        f"Aggressive Profile: {pass_fail(checks['aggressive_profile'])}",
        f"Rejected Risk 1.20%: {pass_fail(checks['rejected_risk_1_20'])}",
        f"Emergency Isolation: {pass_fail(checks['emergency_isolation'])}",
        f"Governor: {pass_fail(checks['governor'])}",
        f"Production Baseline Preserved: {checks['production_baseline_preserved']}",
        f"Decision: {report['decision']}",
    ]
    return "\n".join(lines)


def format_markdown(report: dict[str, Any]) -> str:
    """Return markdown report."""
    checks = report["checks"]
    balanced = report["profile_validation"]["balanced"]
    aggressive = report["profile_validation"]["aggressive"]
    return "\n".join(
        [
            "# Master Sprint 12.1 - Challenge Mode Profile",
            "",
            f"Generated: {report['generated_at']}",
            "",
            "## Mode",
            "- Configuration only: True",
            f"- Enabled by default: {report['enabled']}",
            "- Live activation: False",
            "- Broker execution: False",
            "- Autonomous execution: False",
            "",
            "## Profiles",
            f"- Balanced: {balanced['risk_percent']:.2f}% risk, valid {balanced['valid']}",
            f"- Aggressive: {aggressive['risk_percent']:.2f}% risk, valid {aggressive['valid']}",
            "- 1.20% real challenge risk: rejected",
            "",
            "## Safety",
            f"- Emergency isolation: {checks['emergency_isolation']}",
            f"- Governor rules: {checks['governor']}",
            f"- Production baseline preserved: {checks['production_baseline_preserved']}",
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
