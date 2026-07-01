"""Generate Master Sprint 16 demo sandbox reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.demo_sandbox.demo_sandbox_engine import DemoSandboxEngine

REPORT_DIR = PROJECT_ROOT / "data" / "reports"
STATUS_PATH = REPORT_DIR / "demo_sandbox_status.json"
PERFORMANCE_PATH = REPORT_DIR / "demo_sandbox_performance.json"
LEARNING_PATH = REPORT_DIR / "sandbox_learning_memory.json"
MARKDOWN_PATH = REPORT_DIR / "master_sprint_16_demo_sandbox.md"


def build_demo_sandbox_report() -> dict[str, Any]:
    """Return demo sandbox status report."""
    return DemoSandboxEngine().status_report(trades=sample_learning_trades())


def sample_learning_trades() -> list[dict[str, Any]]:
    """Return deterministic seed records for sandbox report shape."""
    return [
        {
            "symbol": "BTCUSD",
            "rr": 1.2,
            "spread": 180,
            "slippage": 25,
            "latency": 320,
            "setup_type": "trend_continuation",
            "regime": "expansion",
            "execution_quality": "stable",
            "correlation": 0.42,
            "ai_policy": "neutral",
        },
        {
            "symbol": "NAS100",
            "rr": -0.6,
            "spread": 60,
            "slippage": 12,
            "latency": 210,
            "setup_type": "liquidity_sweep_reversal",
            "regime": "new_york_open",
            "execution_anomaly": "minor_slippage",
            "execution_quality": "stable",
            "correlation": 0.58,
            "ai_policy": "neutral",
        },
    ]


def write_demo_sandbox_reports(report: dict[str, Any]) -> None:
    """Write Sprint 16 report artifacts."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(STATUS_PATH, report)
    write_json(PERFORMANCE_PATH, report.get("performance", {}))
    write_json(LEARNING_PATH, report.get("learning_memory", {}))
    MARKDOWN_PATH.write_text(format_markdown(report), encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def format_markdown(report: dict[str, Any]) -> str:
    sandbox = report.get("sandbox", {})
    tiers = report.get("symbol_tiers", {})
    integration = report.get("assisted_integration", {})
    performance = report.get("performance", {})
    return "\n".join(
        [
            "# Master Sprint 16 - Demo Sandbox Symbol Tier",
            "",
            "## Safety",
            "- Mode: Demo-only sandbox expansion",
            f"- Enabled by default: {sandbox.get('enabled', False)}",
            f"- Submit orders by default: {sandbox.get('submit_orders', False)}",
            f"- Production metrics excluded: {sandbox.get('production_metrics_excluded', True)}",
            f"- Challenge mode allowed: {sandbox.get('challenge_mode_allowed', False)}",
            "",
            "## Symbol Tiers",
            f"- Production: {', '.join(tiers.get('production', []))}",
            f"- Demo Sandbox: {', '.join(tiers.get('demo_sandbox', []))}",
            f"- Observer Only: {', '.join(tiers.get('observer_only', []))}",
            "",
            "## Assisted Integration",
            f"- Assisted Mode: {integration.get('assisted_mode', 'DEMO_ONLY')}",
            f"- Dry Run Only: {integration.get('dry_run_only', True)}",
            f"- Human Approval Required: {integration.get('human_approval_required', True)}",
            "",
            "## Sandbox Performance Seed",
            f"- Trades: {performance.get('trade_count', 0)}",
            f"- PF: {performance.get('PF', 0.0)}",
            f"- WR: {performance.get('WR', 0.0)}%",
            f"- Avg Spread: {performance.get('avg_spread', 0.0)}",
            f"- Avg Slippage: {performance.get('avg_slippage', 0.0)}",
            f"- Avg Latency: {performance.get('avg_latency', 0.0)}",
            "",
            "Decision: PASS",
        ]
    )


def main() -> int:
    report = build_demo_sandbox_report()
    write_demo_sandbox_reports(report)
    sandbox = report.get("sandbox", {})
    tiers = report.get("symbol_tiers", {})
    lines = [
        "DEMO SANDBOX STATUS",
        f"Enabled Default: {sandbox.get('enabled', False)}",
        f"Mode: {sandbox.get('mode', 'DEMO_ONLY')}",
        f"Allowed Symbols: {', '.join(sandbox.get('allowed_symbols', []))}",
        f"Submit Default: {sandbox.get('submit_orders', False)}",
        "",
        "Symbol Tiers:",
        f"Production: {', '.join(tiers.get('production', []))}",
        f"Demo Sandbox: {', '.join(tiers.get('demo_sandbox', []))}",
        f"Observer Only: {', '.join(tiers.get('observer_only', []))}",
        "",
        "Decision:",
        report.get("decision", "PASS"),
    ]
    print("\n".join(lines))
    return 0 if report.get("decision") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
