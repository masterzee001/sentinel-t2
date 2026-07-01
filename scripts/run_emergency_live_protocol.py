"""Generate emergency live deployment protocol reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.emergency_live.emergency_live_protocol import EmergencyLiveProtocol, daily_report, emergency_ready


STATUS_PATH = PROJECT_ROOT / "data" / "reports" / "emergency_live_status.json"
DAILY_PATH = PROJECT_ROOT / "data" / "reports" / "emergency_live_daily_report.json"
MARKDOWN_PATH = PROJECT_ROOT / "data" / "reports" / "master_sprint_7_5_emergency_live.md"


def main() -> int:
    """Generate reports and print status."""
    protocol = EmergencyLiveProtocol(config_dir=PROJECT_ROOT / "config")
    sample = protocol.create_approval_request(sample_proposal())
    status = protocol.status_report()
    report = daily_report(status)
    write_json(STATUS_PATH, status)
    write_json(DAILY_PATH, report)
    write_text(MARKDOWN_PATH, format_markdown(status, report, sample))
    print(format_terminal(status, report))
    return 0 if emergency_ready(status) else 1


def sample_proposal() -> dict[str, Any]:
    """Return sample A+ approval proposal for queue readiness."""
    return {
        "symbol": "XAUUSD",
        "strategy": "trend_following",
        "quality_grade": "A+",
        "regime": "institutional_continuation",
        "entry": 4010.0,
        "sl": 4000.0,
        "tp": 4035.0,
        "risk_percent": 0.1,
        "expected_pf": 2.84,
        "expected_wr": 72.6,
    }


def format_terminal(status: dict[str, Any], report: dict[str, Any]) -> str:
    """Return terminal summary."""
    return "\n".join(
        [
            "EMERGENCY LIVE DEPLOYMENT PROTOCOL",
            "",
            f"Status: {status.get('status')}",
            f"Risk Lock: {status.get('risk_lock', {}).get('locked')}",
            f"Grade Lock: {status.get('grade_lock', {}).get('locked')}",
            f"Symbol Lock: {status.get('symbol_lock', {}).get('locked')}",
            f"Kill Switch: CONFIGURED",
            f"Human Approval Required: {status.get('config', {}).get('human_approval_required')}",
            f"Broker Orders: DISABLED",
            f"Autonomous Execution: DISABLED",
            f"Deployment Ready: {report.get('deployment_ready')}",
        ]
    )


def format_markdown(status: dict[str, Any], report: dict[str, Any], sample: dict[str, Any]) -> str:
    """Return Sprint 7.5 markdown."""
    config = status.get("config", {})
    return "\n".join(
        [
            "# Master Sprint 7.5 - Emergency Live Deployment Protocol",
            "",
            "## Mode",
            "- Controlled Assisted Live",
            "- No autonomous execution",
            "- Human approval mandatory",
            "- Broker order submission disabled in Sentinel",
            "",
            "## Restrictions",
            f"- Risk percent: {config.get('risk_percent')}%",
            f"- Max risk percent: {config.get('max_risk_percent')}%",
            f"- Allowed symbols: {', '.join(config.get('allowed_symbols', []))}",
            f"- Allowed grades: {', '.join(config.get('allowed_grades', []))}",
            f"- Max trades per day: {config.get('max_trades_per_day')}",
            "",
            "## Kill Switch",
            f"- Daily loss R: {config.get('kill_switch', {}).get('daily_loss_r')}",
            f"- Consecutive losses: {config.get('kill_switch', {}).get('consecutive_losses')}",
            f"- Max drawdown percent: {config.get('kill_switch', {}).get('max_drawdown_percent')}",
            "",
            "## Approval Queue",
            f"- Sample status: {sample.get('status')}",
            f"- Approval ID: {sample.get('approval_id')}",
            "",
            "## Deployment Ladder",
            "- Day 1-3: Intensive Demo Validation",
            "- Day 4-5: Shadow Live",
            "- Day 6-7: Micro-Risk Assisted Live",
            "",
            f"Deployment ready: {report.get('deployment_ready')}",
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
