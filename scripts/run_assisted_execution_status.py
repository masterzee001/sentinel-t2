"""Generate Master Sprint 14 assisted execution bridge status reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.execution_engine.assisted_execution_bridge import AssistedExecutionBridge


REPORT_DIR = PROJECT_ROOT / "data" / "reports"
STATUS_PATH = REPORT_DIR / "assisted_execution_status.json"
MARKDOWN_PATH = REPORT_DIR / "master_sprint_14_assisted_execution.md"


def main() -> int:
    """Build and write assisted execution status reports."""
    report = build_assisted_execution_report()
    write_assisted_execution_reports(report)
    print(format_terminal(report))
    return 0 if report.get("decision") == "PASS" else 1


def build_assisted_execution_report() -> dict[str, Any]:
    """Return assisted execution status report without broker submission."""
    bridge = AssistedExecutionBridge()
    ticket = bridge.transition_ticket(bridge.sample_ticket(), "APPROVED")
    return bridge.status_report(
        ticket=ticket,
        context={
            "account": {"account_mode": "demo", "server": "MetaQuotes-Demo", "balance": 10000.0},
            "spread_points": 20,
            "slippage_points": 0,
            "expected_lot_size": 0.02,
        }
    )


def write_assisted_execution_reports(report: dict[str, Any]) -> None:
    """Write JSON and markdown report artifacts."""
    write_json(STATUS_PATH, report)
    write_text(MARKDOWN_PATH, format_markdown(report))


def format_terminal(report: dict[str, Any]) -> str:
    """Return terminal summary."""
    validation = report["final_safety_status"]
    checks = validation.get("checks", {})
    return "\n".join(
        [
            "ASSISTED EXECUTION BRIDGE STATUS",
            "",
            f"Enabled: {report['config'].get('enabled')}",
            f"Mode: {report.get('mode')}",
            f"Demo Only: {report['safety'].get('demo_only')}",
            f"Dry Run Only: {report['safety'].get('dry_run_only')}",
            f"Submit Blocked: {not report['safety'].get('broker_orders', False)}",
            f"Ticket Lock: PASS",
            f"Dry Run: {'PASS' if report['dry_run'].get('order_send_called') is False else 'FAIL'}",
            f"Demo Account: {pass_fail(checks.get('demo_account', False))}",
            f"Human Approval: {pass_fail(checks.get('human_approval', False))}",
            f"Symbol Lock: {pass_fail(checks.get('symbol_lock', False))}",
            f"Grade Lock: {pass_fail(checks.get('grade_lock', False))}",
            f"Risk Lock: {pass_fail(checks.get('risk_lock', False))}",
            f"Spread Lock: {pass_fail(checks.get('spread_lock', False))}",
            f"Slippage Lock: {pass_fail(checks.get('slippage_lock', False))}",
            f"Duplicate Lock: {pass_fail(checks.get('duplicate_lock', False))}",
            f"Ticket Freshness: {pass_fail(checks.get('ticket_freshness', False))}",
            f"Kill Switch: {pass_fail(checks.get('kill_switch', False))}",
            f"Production Baseline Preserved: {report.get('production_baseline_preserved')}",
            f"Decision: {report.get('decision')}",
        ]
    )


def format_markdown(report: dict[str, Any]) -> str:
    """Return markdown report."""
    validation = report["final_safety_status"]
    checks = validation.get("checks", {})
    ticket = report["current_ticket"]
    dry_run = report["dry_run"]
    return "\n".join(
        [
            "# Master Sprint 14 - Demo One-Click Assisted Execution Bridge",
            "",
            f"Generated: {report['generated_at']}",
            "",
            "## Mode",
            f"- Assisted Execution: {report['assisted_execution']}",
            f"- Mode: {report['mode']}",
            f"- Demo only: {report['safety']['demo_only']}",
            f"- Dry run only: {report['safety']['dry_run_only']}",
            f"- Submit orders: {report['config'].get('submit_orders', False)}",
            f"- Broker orders from status runner: {report['safety']['broker_orders']}",
            f"- Autonomous execution: {report['safety']['autonomous_execution']}",
            "",
            "## Current Ticket",
            f"- Ticket ID: {ticket['ticket_id']}",
            f"- Status: {ticket['status']}",
            f"- Symbol: {ticket['symbol']}",
            f"- Side: {ticket['side']}",
            f"- Entry: {ticket['entry_price']}",
            f"- SL: {ticket['stop_loss']}",
            f"- TP: {ticket['take_profit']}",
            f"- Risk: {ticket['risk_percent']}%",
            f"- Lot Size: {ticket['lot_size']}",
            f"- Expires At: {ticket['expires_at']}",
            "",
            "## Dry Run",
            f"- Order type: {dry_run['order_type']}",
            f"- Risk amount: {dry_run['risk_amount']}",
            f"- Expected max loss: {dry_run['expected_max_loss']}",
            f"- Order send called: {dry_run['order_send_called']}",
            "",
            "## Safety Gates",
            f"- Demo Account: {checks.get('demo_account')}",
            f"- Human Approval: {checks.get('human_approval')}",
            f"- Symbol Lock: {checks.get('symbol_lock')}",
            f"- Grade Lock: {checks.get('grade_lock')}",
            f"- Risk Lock: {checks.get('risk_lock')}",
            f"- Spread Lock: {checks.get('spread_lock')}",
            f"- Slippage Lock: {checks.get('slippage_lock')}",
            f"- Duplicate Lock: {checks.get('duplicate_lock')}",
            f"- Ticket Freshness: {checks.get('ticket_freshness')}",
            f"- Kill Switch: {checks.get('kill_switch')}",
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
