"""Offline smoke test for live data collection reporting."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.live_data.live_data_collector import LiveDataCollector


def write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "live_data.yaml").write_text(
        """
enabled: true
symbols:
  - XAUUSD
  - US30
  - BTCUSD
capture_interval_seconds: 180
record_states:
  - COLD
  - WARM
  - HOT
  - EXECUTION_READY
storage:
  format: jsonl
  path: data/live_data/live_signals.jsonl
retention:
  max_records: 1000
""",
        encoding="utf-8",
    )


def scan_record(symbol: str, state: str, confidence: int) -> dict:
    return {
        "symbol": symbol,
        "available": True,
        "state": state,
        "score": confidence,
        "action": "TRADE CANDIDATE" if state == "EXECUTION_READY" else "MONITOR",
        "trend": {"daily_bias": "bearish", "overall_bias": "bearish"},
        "killzone": {"active_killzone": "london_open", "quality_score": 10},
        "confidence": {
            "confidence_band": state,
            "total_confidence": confidence,
            "decision": "APPROVED" if state == "EXECUTION_READY" else "REJECTED",
            "narrative": {"bias": "bearish", "phase": "distribution"},
            "smt": {"smt_detected": symbol != "US30", "direction": "bearish" if symbol != "US30" else None},
            "guardrail": {"guardrail_adjusted_confidence": max(0, confidence - 5)},
            "rejection_reasons": [] if state == "EXECUTION_READY" else ["No SMT confirmation"],
        },
        "trade_plan": {
            "execution_allowed": state == "EXECUTION_READY",
            "rejection_reasons": [],
        },
    }


def scan(symbols: list[dict]) -> dict:
    return {
        "risk_status": "ALLOWED",
        "news_status": "CLEAR",
        "risk": {"permission": {"status": "ALLOWED"}},
        "news": {"status": "CLEAR", "lock_active": False},
        "symbols": symbols,
    }


def main() -> int:
    """Exercise record writes, aggregation, setup progression, and formatting."""
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config_dir = root / "config"
        write_config(config_dir)
        collector = LiveDataCollector(config_dir=config_dir, project_root=root)
        timestamp = datetime(2026, 6, 28, 8, 0, tzinfo=ZoneInfo("Africa/Lagos"))

        written = collector.append_scan(scan([scan_record("XAUUSD", "WARM", 63)]), timestamp=timestamp)
        assert written == 1
        assert collector.read_records()[0]["symbol"] == "XAUUSD"

        collector.append_scan(scan([scan_record("XAUUSD", "HOT", 78)]), timestamp=timestamp)
        collector.append_scan(scan([scan_record("XAUUSD", "EXECUTION_READY", 92)]), timestamp=timestamp)
        collector.append_scan(scan([scan_record("US30", "WARM", 55), scan_record("BTCUSD", "HOT", 70)]), timestamp=timestamp)

        records = collector.read_records()
        setup_ids = {record["setup_id"] for record in records if record["symbol"] == "XAUUSD"}
        assert len(records) == 5
        assert len(setup_ids) == 1
        assert json.loads(collector.storage_path.read_text(encoding="utf-8").splitlines()[0])["state"] == "WARM"

        summary = collector.summary()
        assert summary["symbols"]["XAUUSD"]["warm"] == 1
        assert summary["symbols"]["XAUUSD"]["hot"] == 1
        assert summary["symbols"]["XAUUSD"]["execution_ready"] == 1
        assert summary["symbols"]["BTCUSD"]["symbol_mode"] == "demo_sandbox"
        assert summary["killzones"]["london_open"] == 3
        assert summary["narratives"]["distribution"] == 3
        assert summary["rejection_reasons"]["no SMT"] == 4

        stats = LiveDataCollector.format_live_stats(summary)
        assert "Live Data Stats" in stats
        assert "XAUUSD:" in stats
        assert "Exec Ready: 1" in stats
        assert "Mode: DEMO_SANDBOX" in stats

    print("LIVE DATA COLLECTOR TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
