from __future__ import annotations

from pathlib import Path

from backend.monitor.live_monitor import LiveMonitor


class FakeRiskGovernor:
    def evaluate(self):
        return {
            "permission": {
                "trade_allowed": True,
                "status": "ALLOWED",
                "block_reasons": [],
                "warnings": [],
            }
        }


class FakeConfidenceAnalyzer:
    def __init__(self, states: dict[str, tuple[str, int]] | None = None, unavailable: set[str] | None = None):
        self.states = states or {
            "XAUUSD": ("WARM", 51),
            "US30": ("WARM", 40),
            "EURUSD": ("COLD", 28),
            "GBPUSD": ("HOT", 74),
        }
        self.unavailable = unavailable or set()

    def analyze(self, symbol: str, context=None):
        if symbol in self.unavailable:
            raise ValueError(f"{symbol} is not available from broker")
        state, score = self.states[symbol]
        return {
            "symbol": symbol,
            "confidence_band": state,
            "total_confidence": score,
            "decision": "REJECTED",
            "guardrail_status": "PASS",
            "guardrail_reasons": [],
        }


class FakeTradePlanner:
    def analyze(self, symbol: str):
        return {
            "symbol": symbol,
            "execution_allowed": False,
            "plan_quality": "diagnostic_only",
        }


class FakeNewsFilter:
    def check(self, symbol=None, current_time=None):
        return {
            "enabled": True,
            "lock_active": False,
            "event_name": None,
            "minutes_to_event": None,
            "affected_symbols": [],
            "reason": "",
        }


class FakeJournalEngine:
    def __init__(self):
        self.records_written = 0

    def append_scan_records(self, **kwargs):
        self.records_written += len(kwargs.get("symbol_payloads", []))
        return self.records_written


class FakeBTCObserver:
    def observe(self):
        return {
            "symbol": "BTCUSD",
            "display_symbol": "BTCUSD (EXPERIMENTAL)",
            "available": True,
            "state": "WARM",
            "score": 50,
            "action": "OBSERVE",
            "killzone": {"active_killzone": "new_york_open", "quality_score": 10},
            "confidence": {
                "symbol": "BTCUSD",
                "confidence_band": "WARM",
                "total_confidence": 50,
                "decision": "REJECTED",
                "rejection_reasons": ["BTCUSD demo sandbox: production execution disabled"],
                "guardrail_status": "BLOCKED",
                "guardrail_reasons": ["BTCUSD demo sandbox: production execution disabled"],
            },
            "trade_plan": {
                "symbol": "BTCUSD",
                "plan_quality": "observer_only",
                "execution_allowed": False,
            },
            "execution_allowed": False,
        }


class FakeLiveDataCollector:
    def __init__(self):
        self.scans: list[dict] = []

    def append_scan(self, scan: dict) -> int:
        self.scans.append(scan)
        return len(scan.get("symbols", []))


def write_monitoring_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "monitoring.yaml").write_text(
        """
environment: development
scan_interval_seconds:
  development: 180
  production: 60
symbols:
  - XAUUSD
  - US30
  - EURUSD
  - GBPUSD
alerts:
  terminal: true
  telegram: false
  desktop: false
heartbeat:
  enabled: true
  every_n_scans: 1
""",
        encoding="utf-8",
    )


def make_monitor(tmp_path: Path, confidence_analyzer=None, output=None, live_data_collector=None) -> LiveMonitor:
    config_dir = tmp_path / "config"
    write_monitoring_config(config_dir)
    return LiveMonitor(
        connector=object(),
        confidence_analyzer=confidence_analyzer or FakeConfidenceAnalyzer(),
        risk_governor=FakeRiskGovernor(),
        trade_planner=FakeTradePlanner(),
        news_filter=FakeNewsFilter(),
        journal_engine=FakeJournalEngine(),
        live_data_collector=live_data_collector,
        config_dir=config_dir,
        sleep_fn=lambda seconds: None,
        output_fn=output.append if output is not None else lambda line: None,
    )


def test_state_transition_detection(tmp_path: Path):
    monitor = make_monitor(tmp_path)
    monitor.previous_states["GBPUSD"] = "WARM"

    alerts = monitor.detect_alerts(
        [
            {
                "symbol": "GBPUSD",
                "available": True,
                "state": "HOT",
                "score": 74,
                "action": "PREPARE",
            }
        ]
    )

    assert len(alerts) == 1
    assert alerts[0]["alert_triggered"] is True
    assert alerts[0]["symbol"] == "GBPUSD"
    assert alerts[0]["transition"] == "WARM_TO_HOT"
    assert alerts[0]["message"] == "GBPUSD upgraded WARM -> HOT. Setup close. Wait for confirmation."
    assert alerts[0]["confidence"] == 74
    assert alerts[0]["telegram_sent"] is False
    assert alerts[0]["warnings"] == []


def test_execution_ready_downgrade_transition_alerts(tmp_path: Path):
    monitor = make_monitor(tmp_path)
    monitor.previous_states["EURUSD"] = "EXECUTION_READY"

    alerts = monitor.detect_alerts(
        [
            {
                "symbol": "EURUSD",
                "available": True,
                "state": "WARM",
                "score": 52,
                "action": "MONITOR",
            }
        ]
    )

    assert alerts[0]["transition"] == "EXECUTION_READY_TO_LOWER"
    assert alerts[0]["message"] == "EURUSD downgraded EXECUTION_READY -> WARM. Setup weakened. Stand down."


def test_unavailable_symbol_is_non_fatal(tmp_path: Path):
    analyzer = FakeConfidenceAnalyzer(unavailable={"EURUSD"})
    monitor = make_monitor(tmp_path, confidence_analyzer=analyzer)

    result = monitor.analyze_symbol("EURUSD")

    assert result["available"] is False
    assert result["state"] == "UNAVAILABLE"
    assert result["action"] == "UNAVAILABLE"
    assert "not available from broker" in result["error"]


def test_heartbeat_formatting():
    heartbeat = LiveMonitor.format_heartbeat(
        {
            "scan": 1,
            "risk_status": "ALLOWED",
            "news_status": "CLEAR",
            "symbols": [
                {"symbol": "XAUUSD", "available": True, "state": "WARM", "score": 51, "action": "MONITOR"},
                {"symbol": "EURUSD", "available": False, "error": "symbol unavailable"},
            ],
        }
    )

    assert "SCAN 1" in heartbeat
    assert "Risk Status: ALLOWED" in heartbeat
    assert "News Status: CLEAR" in heartbeat
    assert "XAUUSD: Raw 51 | Adjusted 51 | WARM MONITOR | Killzone: None 0 | Guardrail: PASS" in heartbeat
    assert "EURUSD: UNAVAILABLE - symbol unavailable" in heartbeat


def test_heartbeat_shows_raw_adjusted_confidence_and_penalty():
    heartbeat = LiveMonitor.format_heartbeat(
        {
            "scan": 1,
            "risk_status": "ALLOWED",
            "news_status": "CLEAR",
            "symbols": [
                {
                    "symbol": "XAUUSD",
                    "available": True,
                    "state": "COLD",
                    "score": 62,
                    "action": "WAIT",
                    "guardrail_status": "BLOCKED",
                    "confidence": {
                        "total_confidence": 62,
                        "confidence_band": "COLD",
                        "guardrail": {
                            "guardrail_adjusted_confidence": 34,
                            "adjusted_confidence_band": "COLD",
                            "guardrail_penalty_total": 28,
                            "penalties": [{"reason": "London continuation penalty", "value": 28}],
                        },
                    },
                }
            ],
        }
    )

    assert "XAUUSD: Raw 62 | Adjusted 34 | COLD WAIT" in heartbeat
    assert "Guardrail: BLOCKED | Penalty: london_continuation" in heartbeat


def test_heartbeat_labels_sandbox_hot_as_demo_sandbox():
    heartbeat = LiveMonitor.format_heartbeat(
        {
            "scan": 1,
            "risk_status": "ALLOWED",
            "news_status": "CLEAR",
            "symbols": [
                {
                    "symbol": "NAS100",
                    "available": True,
                    "state": "HOT",
                    "score": 53,
                    "action": "OBSERVE",
                    "mode": "DEMO_SANDBOX",
                    "sandbox_mode": True,
                    "observer_mode": True,
                    "trade_plan": {"plan_quality": "observer_only"},
                }
            ],
        }
    )

    assert "NAS100: Sandbox HOT | State OBSERVER_HOT | Score 53 | DEMO_SANDBOX" in heartbeat
    assert "SANDBOX DEMO ONLY | NOT PRODUCTION | NOT FUNDED | NOT CHALLENGE" in heartbeat


def test_start_stops_cleanly_with_max_scans(tmp_path: Path):
    output: list[str] = []
    monitor = make_monitor(tmp_path, output=output)

    monitor.start(max_scans=1)

    assert monitor.running is False
    assert any("PROJECT SENTINEL LIVE MONITOR" in line for line in output)
    assert any("SCAN 1" in line for line in output)


def test_stop_sets_running_false(tmp_path: Path):
    monitor = make_monitor(tmp_path)
    monitor.running = True

    monitor.stop()

    assert monitor.running is False


def test_btc_observer_symbol_loads_and_blocks_execution(tmp_path: Path):
    monitor = make_monitor(tmp_path)
    monitor.btc_observer = FakeBTCObserver()

    result = monitor.analyze_symbol("BTCUSD")

    assert result["symbol"] == "BTCUSD"
    assert result["available"] is True
    assert result["trade_plan"]["plan_quality"] == "observer_only"
    assert result["trade_plan"]["execution_allowed"] is False
    assert result["confidence"]["guardrail_status"] == "BLOCKED"


def test_scan_writes_live_data_records(tmp_path: Path):
    collector = FakeLiveDataCollector()
    monitor = make_monitor(tmp_path, live_data_collector=collector)

    scan = monitor.scan_once(1)

    assert scan["live_data_records_written"] == 4
    assert len(collector.scans) == 1
    assert collector.scans[0]["risk_status"] == "ALLOWED"
