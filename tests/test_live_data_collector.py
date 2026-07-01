from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.live_data.live_data_collector import LiveDataCollector


def write_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "live_data.yaml").write_text(
        """
enabled: true
symbols:
  - XAUUSD
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
  max_records: 100
""",
        encoding="utf-8",
    )


def sample_scan(state: str = "WARM", symbol: str = "XAUUSD", confidence: int = 63) -> dict:
    return {
        "risk_status": "ALLOWED",
        "news_status": "CLEAR",
        "risk": {"permission": {"status": "ALLOWED"}},
        "news": {"status": "CLEAR", "lock_active": False},
        "symbols": [
            {
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
                    "smt": {"smt_detected": True, "direction": "bearish"},
                    "guardrail": {"guardrail_adjusted_confidence": confidence - 5},
                    "rejection_reasons": [] if state == "EXECUTION_READY" else ["No SMT confirmation"],
                },
                "trade_plan": {
                    "execution_allowed": state == "EXECUTION_READY",
                    "rejection_reasons": [],
                },
            }
        ],
    }


def make_collector(tmp_path: Path) -> LiveDataCollector:
    config_dir = tmp_path / "config"
    write_config(config_dir)
    return LiveDataCollector(config_dir=config_dir, project_root=tmp_path)


def test_record_write(tmp_path: Path):
    collector = make_collector(tmp_path)

    written = collector.append_scan(sample_scan(), timestamp=datetime(2026, 6, 28, 8, 0, tzinfo=ZoneInfo("Africa/Lagos")))

    records = collector.read_records()
    assert written == 1
    assert len(records) == 1
    assert records[0]["symbol"] == "XAUUSD"
    assert records[0]["decision"] == "MONITOR"
    assert records[0]["adjusted_confidence"] == 58
    assert records[0]["symbol_mode"] == "production"
    assert records[0]["state_kind"] == "PRODUCTION_CONFIDENCE"
    assert records[0]["rejection_reason_codes"] == ["NO_TRADE_WINDOW"]
    assert "setup_id" in records[0]


def test_aggregation(tmp_path: Path):
    collector = make_collector(tmp_path)
    for state in ("WARM", "HOT", "EXECUTION_READY"):
        collector.append_scan(sample_scan(state=state))
    collector.append_scan(sample_scan(state="WARM", symbol="BTCUSD", confidence=50))

    summary = collector.summary()

    assert summary["total_records"] == 4
    assert summary["symbols"]["XAUUSD"]["warm"] == 1
    assert summary["symbols"]["XAUUSD"]["hot"] == 1
    assert summary["symbols"]["XAUUSD"]["execution_ready"] == 1
    assert summary["symbols"]["BTCUSD"]["symbol_mode"] == "demo_sandbox"
    assert summary["killzones"]["london_open"] == 2
    assert summary["narratives"]["distribution"] == 2
    assert summary["rejection_reasons"]["NO_TRADE_WINDOW"] == 3
    assert summary["exclusive_band_distribution"] == {
        "COLD_ONLY": 0,
        "WARM_ONLY": 1,
        "HOT_ONLY": 1,
        "EXECUTION_READY": 1,
    }
    assert summary["cumulative_funnel"]["HOT_OR_BETTER"] == 2
    assert summary["cumulative_funnel"]["approved_trades"] == 1


def test_setup_id_generation_keeps_progression_together(tmp_path: Path):
    collector = make_collector(tmp_path)
    for state in ("WARM", "HOT", "EXECUTION_READY"):
        collector.append_scan(sample_scan(state=state), timestamp=datetime(2026, 6, 28, 8, 0, tzinfo=ZoneInfo("Africa/Lagos")))

    records = collector.read_records()
    setup_ids = {record["setup_id"] for record in records}

    assert len(setup_ids) == 1


def test_stats_summary_and_telegram_formatting(tmp_path: Path):
    collector = make_collector(tmp_path)
    collector.append_scan(sample_scan(state="WARM"))
    collector.append_scan(sample_scan(state="HOT"))
    collector.append_scan(sample_scan(state="EXECUTION_READY"))
    collector.append_scan(sample_scan(state="WARM", symbol="BTCUSD", confidence=50))

    summary = collector.summary()
    text = LiveDataCollector.format_live_stats(summary)

    assert summary["available"] is True
    assert "Live Data Stats" in text
    assert "XAUUSD:" in text
    assert "Warm: 1" in text
    assert "Hot: 1" in text
    assert "Exec Ready: 1" in text
    assert "BTCUSD:" in text
    assert "Mode: DEMO_SANDBOX" in text


def test_jsonl_is_valid(tmp_path: Path):
    collector = make_collector(tmp_path)
    collector.append_scan(sample_scan())

    line = collector.storage_path.read_text(encoding="utf-8").strip()
    assert json.loads(line)["state"] == "WARM"
