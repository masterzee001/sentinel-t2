"""Live monitoring loop for Project Sentinel Advisor Mode."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from loguru import logger

from backend.alerts.alert_engine import AlertEngine
from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer, ConfidenceAnalyzerError
from backend.display.confidence_display import DEMO_SANDBOX_LABEL, action_for_state, confidence_display_fields, observer_display_fields
from backend.journal.journal_engine import JournalEngine
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.live_data.live_data_collector import LiveDataCollector
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.news_filter.news_filter import NewsFilter
from backend.observer.btc_observer import BTCObserver
from backend.observer.nas100_observer import NAS100Observer
from backend.risk_manager.risk_governor import RiskGovernor, RiskGovernorError
from backend.risk_manager.risk_state_store import RiskStateStore
from backend.shared.shared_candidate_scanner import SharedCandidateScanner
from backend.shared.shared_decision_adapter import DecisionRequest, SharedDecisionAdapter
from backend.trade_planner.trade_planner import TradePlanner, TradePlannerError


class LiveMonitorError(RuntimeError):
    """Raised when the live monitor cannot be configured or run."""


class LiveMonitor:
    """Continuously scan Sentinel symbols and alert on confidence state transitions."""

    DEFAULT_CONFIG = {
        "environment": "development",
        "scan_interval_seconds": {
            "development": 180,
            "production": 60,
        },
        "symbols": ["XAUUSD", "US30", "EURUSD", "GBPUSD", "BTCUSD", "NAS100"],
        "alerts": {
            "terminal": True,
            "telegram": False,
            "desktop": False,
        },
        "heartbeat": {
            "enabled": True,
            "every_n_scans": 1,
        },
    }
    STATE_ORDER = {
        "COLD": 0,
        "WARM": 1,
        "HOT": 2,
        "EXECUTION_READY": 3,
    }
    COMMENTARY = {
        "COLD": "No narrative yet. Ignore noise.",
        "WARM": "Narrative forming. Watch liquidity.",
        "HOT": "Setup close. Wait for confirmation.",
        "EXECUTION_READY": "A-grade setup detected. Prepare execution.",
    }

    def __init__(
        self,
        connector: MT5Connector,
        confidence_analyzer: ConfidenceAnalyzer | None = None,
        risk_governor: RiskGovernor | None = None,
        trade_planner: TradePlanner | None = None,
        news_filter: NewsFilter | None = None,
        journal_engine: JournalEngine | None = None,
        alert_engine: AlertEngine | None = None,
        killzone_analyzer: KillzoneAnalyzer | None = None,
        live_data_collector: LiveDataCollector | None = None,
        shared_decision_adapter: SharedDecisionAdapter | None = None,
        candidate_scanner: SharedCandidateScanner | None = None,
        config_dir: str | Path | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        output_fn: Callable[[str], None] | None = None,
        risk_state_store: RiskStateStore | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.project_root = project_root
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.config = self._load_config()
        self.connector = connector
        self.risk_state_store = risk_state_store or RiskStateStore(
            project_root / ".sentinel_runtime" / "risk_state.json"
        )
        self.risk_governor = risk_governor or RiskGovernor(
            connector=self.connector,
            config_dir=self.config_dir,
            state_store=self.risk_state_store,
        )
        self.news_filter = news_filter or NewsFilter(config_dir=self.config_dir)
        self.journal_engine = journal_engine or JournalEngine(config_dir=self.config_dir)
        self.alert_engine = alert_engine or AlertEngine(config_dir=self.config_dir)
        self.killzone_analyzer = killzone_analyzer or KillzoneAnalyzer(config_dir=self.config_dir)
        self.btc_observer = BTCObserver(
            connector=self.connector,
            config_dir=self.config_dir,
            killzone_analyzer=self.killzone_analyzer,
        )
        self.nas100_observer = NAS100Observer(
            connector=self.connector,
            config_dir=self.config_dir,
            killzone_analyzer=self.killzone_analyzer,
        )
        self.confidence_analyzer = confidence_analyzer or ConfidenceAnalyzer(
            connector=self.connector,
            news_filter=self.news_filter,
            killzone_analyzer=self.killzone_analyzer,
            config_dir=self.config_dir,
        )
        self.candidate_scanner = candidate_scanner or SharedCandidateScanner()
        self.shared_decision_adapter = shared_decision_adapter or SharedDecisionAdapter(self.confidence_analyzer)
        self.trade_planner = trade_planner or TradePlanner(
            connector=self.connector,
            confidence_analyzer=self.confidence_analyzer,
            risk_governor=self.risk_governor,
            config_dir=self.config_dir,
        )
        self.live_data_collector = live_data_collector
        if self.live_data_collector is None and (self.config_dir / "live_data.yaml").exists():
            self.live_data_collector = LiveDataCollector(config_dir=self.config_dir, project_root=project_root)
        self.sleep_fn = sleep_fn or time.sleep
        self.output_fn = output_fn or print
        self.previous_states: dict[str, str] = {}
        self.scan_count = 0
        self.running = False

    def start(self, max_scans: int | None = None) -> None:
        """Start the monitor loop until stopped, interrupted, or max_scans is reached."""
        self.running = True
        self.print_startup()

        while self.running:
            self.scan_count += 1
            scan = self.scan_once(self.scan_count)
            self.print_scan(scan)
            self.print_alerts(scan["alerts"])

            if max_scans is not None and self.scan_count >= max_scans:
                self.stop()
                break

            if self.running:
                self.sleep_fn(self.scan_interval_seconds)

    def stop(self) -> None:
        """Request graceful monitor shutdown."""
        self.running = False

    def scan_once(self, scan_number: int | None = None) -> dict[str, Any]:
        """Run one full Sentinel scan across configured symbols."""
        scan_number = scan_number or (self.scan_count + 1)
        logger.info("Starting Sentinel live monitor scan {}.", scan_number)

        try:
            risk = self.risk_governor.evaluate()
        except RiskGovernorError as exc:
            risk = self.blocked_risk_result(str(exc))

        news_status = self.news_filter.check()
        results = [self.analyze_symbol(symbol, risk=risk) for symbol in self.symbols]
        alerts = self.detect_alerts(
            results,
            risk_status=risk.get("permission", {}).get("status", "UNKNOWN"),
            news_lock_active=bool(news_status.get("lock_active", False)),
        )
        scan = {
            "scan": scan_number,
            "risk": risk,
            "risk_status": risk.get("permission", {}).get("status", "UNKNOWN"),
            "news": news_status,
            "news_status": NewsFilter.format_status(news_status),
            "symbols": results,
            "alerts": alerts,
            "heartbeat": self.should_print_heartbeat(scan_number),
        }
        self.write_journal_records(scan)
        scan["live_data_records_written"] = self.write_live_data_records(scan)
        logger.info("Completed Sentinel live monitor scan {}.", scan_number)
        return scan

    def analyze_symbol(self, symbol: str, risk: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return one symbol's confidence state and trade-plan snapshot."""
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol == BTCObserver.SYMBOL:
            return self.btc_observer.observe()
        if normalized_symbol == NAS100Observer.SYMBOL:
            return self.nas100_observer.observe()
        risk_flags = RiskGovernor.decision_context_from_result(risk) if risk else {}
        try:
            killzone = self.killzone_analyzer.analyze(normalized_symbol)
            candidate_scan = self.candidate_scanner.scan_live_candidate(
                normalized_symbol,
                context={"risk_reward": 3.0, "killzone": killzone},
            )
            shared_decision = self.shared_decision_adapter.evaluate(
                DecisionRequest(
                    mode="LIVE",
                    symbol=normalized_symbol,
                    context={
                        "risk_reward": 3.0,
                        "killzone": killzone,
                        "candidate_scan": candidate_scan,
                        "plan": candidate_scan.get("plan", {}),
                        **risk_flags,
                    },
                )
            )
            confidence = shared_decision.get("decision", {})
            trade_plan = self.trade_planner.analyze(
                normalized_symbol,
                confidence_context=risk_flags or None,
            )
        except (ConfidenceAnalyzerError, TradePlannerError, MT5ConnectorError, ValueError) as exc:
            return self.unavailable_symbol_result(normalized_symbol, str(exc))

        state = str(confidence.get("confidence_band", "COLD"))
        score = int(confidence.get("total_confidence", 0))
        display = confidence_display_fields(confidence, fallback_state=state)
        return {
            "symbol": normalized_symbol,
            "available": True,
            "state": state,
            "score": score,
            "action": self.action_for_state(state),
            "raw_confidence": display["raw_confidence"],
            "adjusted_confidence": display["adjusted_confidence"],
            "raw_band": display["raw_band"],
            "guardrail_penalty": display["guardrail_penalty"],
            "band_differs": display["band_differs"],
            "trend": self.get_latest_trend(normalized_symbol),
            "ict": self.get_latest_ict(normalized_symbol),
            "killzone": killzone,
            "guardrail_status": confidence.get("guardrail_status", "PASS"),
            "guardrail_reasons": confidence.get("guardrail_reasons", []),
            "confidence": confidence,
            "candidate_scan": candidate_scan,
            "shared_decision": shared_decision,
            "decision_source": shared_decision.get("decision_source"),
            "sda_compliant": bool(shared_decision.get("parity_compliant", False)),
            "trade_plan": trade_plan,
        }

    def write_journal_records(self, scan: dict[str, Any]) -> int:
        """Append one journal record per available symbol in a scan."""
        payloads = []
        for result in scan.get("symbols", []):
            if not result.get("available", False):
                continue
            payloads.append(
                {
                    "symbol": result.get("symbol"),
                    "trend": result.get("trend", {}),
                    "ict": result.get("ict", {}),
                    "killzone": result.get("killzone", {}),
                    "confidence": result.get("confidence", {}),
                    "trade_plan": result.get("trade_plan", {}),
                    "commentary": self.commentary_for_state(str(result.get("state", "COLD"))),
                }
            )
        return self.journal_engine.append_scan_records(
            environment=str(scan.get("risk", {}).get("environment", self.environment)),
            risk=scan.get("risk", {}),
            news=scan.get("news", {}),
            symbol_payloads=payloads,
        )

    def write_live_data_records(self, scan: dict[str, Any]) -> int:
        """Append compact live-data records without affecting scan flow."""
        if self.live_data_collector is None:
            return 0
        try:
            return self.live_data_collector.append_scan(scan)
        except Exception as exc:  # pragma: no cover - collection must not stop monitoring.
            logger.warning("Live data collection skipped: {}", exc)
            return 0

    def get_latest_trend(self, symbol: str) -> dict[str, Any]:
        """Best-effort trend snapshot for journaling."""
        trend_analyzer = getattr(self.confidence_analyzer, "trend_analyzer", None)
        if not trend_analyzer or not hasattr(trend_analyzer, "get_overall_bias"):
            return {}
        try:
            return trend_analyzer.get_overall_bias(symbol)
        except Exception as exc:  # pragma: no cover - defensive around live data.
            logger.warning("Could not journal latest trend for {}: {}", symbol, exc)
            return {}

    def get_latest_ict(self, symbol: str) -> dict[str, Any]:
        """Best-effort ICT snapshot for journaling."""
        ict_analyzer = getattr(self.confidence_analyzer, "ict_analyzer", None)
        if not ict_analyzer or not hasattr(ict_analyzer, "analyze"):
            return {}
        try:
            return ict_analyzer.analyze(symbol)
        except Exception as exc:  # pragma: no cover - defensive around live data.
            logger.warning("Could not journal latest ICT for {}: {}", symbol, exc)
            return {}

    def detect_alerts(
        self,
        symbol_results: list[dict[str, Any]],
        risk_status: str = "ALLOWED",
        news_lock_active: bool = False,
    ) -> list[dict[str, Any]]:
        """Return alerts for meaningful per-symbol state changes."""
        alerts: list[dict[str, Any]] = []
        for result in symbol_results:
            symbol = str(result["symbol"])
            current_state = str(result["state"])
            previous_state = self.previous_states.get(symbol)
            self.previous_states[symbol] = current_state

            alert = self.alert_engine.evaluate(
                symbol=symbol,
                previous_state=previous_state,
                current_state=current_state,
                confidence=int(result.get("score", 0)),
                risk_status=risk_status,
                news_lock_active=news_lock_active,
            )
            if alert.get("alert_triggered", False):
                alerts.append(alert)

        return alerts

    def print_startup(self) -> None:
        """Print live monitor startup banner."""
        self.output_fn("PROJECT SENTINEL LIVE MONITOR")
        self.output_fn("Advisor Mode: no execution")
        self.output_fn(f"Symbols: {', '.join(self.symbols)}")
        self.output_fn(f"Scan interval: {self.scan_interval_seconds} seconds")

    def print_scan(self, scan: dict[str, Any]) -> None:
        """Print heartbeat scan summary when configured."""
        if not scan.get("heartbeat", False):
            return
        self.output_fn("")
        self.output_fn(self.format_heartbeat(scan))

    def print_alerts(self, alerts: list[dict[str, Any]]) -> None:
        """Print terminal alerts when enabled."""
        if not self.terminal_alerts_enabled or not self.alert_engine.terminal_enabled:
            return
        for alert in alerts:
            self.output_fn("")
            self.output_fn(self.alert_engine.format_terminal_alert(alert))

    def should_print_heartbeat(self, scan_number: int) -> bool:
        """Return whether this scan should print a heartbeat summary."""
        heartbeat = self.config.get("heartbeat", {})
        if not bool(heartbeat.get("enabled", True)):
            return False
        every_n_scans = max(int(heartbeat.get("every_n_scans", 1)), 1)
        return scan_number % every_n_scans == 0

    @classmethod
    def format_heartbeat(cls, scan: dict[str, Any]) -> str:
        """Return the clean terminal heartbeat block."""
        lines = [
            f"SCAN {scan.get('scan', 0)}",
            f"Risk Status: {scan.get('risk_status', 'UNKNOWN')}",
            f"News Status: {scan.get('news_status', 'CLEAR')}",
        ]
        for result in scan.get("symbols", []):
            if not result.get("available", False):
                lines.append(f"{result.get('symbol')}: UNAVAILABLE - {result.get('error', 'symbol unavailable')}")
                continue
            lines.append(cls.format_symbol_heartbeat(result))
        return "\n".join(lines)

    @classmethod
    def format_symbol_heartbeat(cls, result: dict[str, Any]) -> str:
        """Return one consistent live heartbeat row."""
        if cls.is_demo_sandbox_result(result):
            observer = observer_display_fields(
                str(result.get("symbol", "UNKNOWN")),
                result.get("state", "UNAVAILABLE"),
                result.get("score", 0),
            )
            return (
                f"{observer['symbol']}: Sandbox {observer['display_state']} | State {observer['observer_state']} | "
                f"Score {observer['score']} | {DEMO_SANDBOX_LABEL} | "
                "SANDBOX DEMO ONLY | NOT PRODUCTION | NOT FUNDED | NOT CHALLENGE"
            )
        if cls.is_observer_result(result):
            observer = observer_display_fields(
                str(result.get("symbol", "UNKNOWN")),
                result.get("state", "UNAVAILABLE"),
                result.get("score", 0),
            )
            return (
                f"{observer['symbol']}: Observer {observer['display_state']} | State {observer['observer_state']} | Score {observer['score']} | "
                f"{observer['mode']} | {observer['execution_note']}"
            )
        confidence = result.get("confidence", {}) if isinstance(result.get("confidence", {}), dict) else {}
        if not confidence:
            confidence = {
                "total_confidence": result.get("score", 0),
                "confidence_band": result.get("state", "COLD"),
            }
        display = confidence_display_fields(confidence, fallback_state=str(result.get("state", "COLD")))
        guardrail = result.get("guardrail_status", "PASS")
        line = (
            f"{result.get('symbol')}: Raw {display['raw_confidence']} | Adjusted {display['adjusted_confidence']} | "
            f"{result.get('state')} {result.get('action')} | Killzone: {cls.format_killzone_status(result.get('killzone', {}))} "
            f"| Guardrail: {guardrail}"
        )
        if display["band_differs"]:
            line += f" | Penalty: {display['guardrail_penalty']}"
        return line

    @staticmethod
    def is_demo_sandbox_result(result: dict[str, Any]) -> bool:
        """Return whether a scan result is demo-sandbox diagnostic data."""
        return bool(result.get("sandbox_mode") or result.get("mode") == DEMO_SANDBOX_LABEL)

    @staticmethod
    def is_observer_result(result: dict[str, Any]) -> bool:
        """Return whether a scan result is diagnostic-only observer data."""
        plan = result.get("trade_plan", {}) if isinstance(result.get("trade_plan", {}), dict) else {}
        confidence = result.get("confidence", {}) if isinstance(result.get("confidence", {}), dict) else {}
        return bool(
            result.get("observer_mode")
            or result.get("experimental")
            or plan.get("plan_quality") == "observer_only"
            or any("observer mode" in str(reason).lower() for reason in confidence.get("rejection_reasons", []) or [])
        )

    @staticmethod
    def format_killzone_status(killzone: dict[str, Any]) -> str:
        """Return compact killzone heartbeat text."""
        if not killzone:
            return "None 0"
        name = KillzoneAnalyzer.display_name(str(killzone.get("active_killzone", "none")))
        return f"{name} {killzone.get('quality_score', 0)}"

    @staticmethod
    def format_alert(alert: dict[str, Any]) -> str:
        """Return the clean terminal alert block."""
        direction = alert.get("direction", "changed")
        lines = [
            "ALERT:",
            (
                f"{alert.get('symbol')} {direction} "
                f"{alert.get('previous_state')} -> {alert.get('current_state')}"
            ),
            f"Confidence: {alert.get('confidence', 0)}",
            f"Action: {alert.get('action', 'WAIT')}",
            f"Commentary: {alert.get('commentary', '')}",
        ]
        return "\n".join(lines)

    @classmethod
    def transition_direction(cls, previous_state: str, current_state: str) -> str:
        """Return upgraded, downgraded, or changed for a state transition."""
        previous_rank = cls.STATE_ORDER.get(previous_state, -1)
        current_rank = cls.STATE_ORDER.get(current_state, -1)
        if current_rank > previous_rank:
            return "upgraded"
        if current_rank < previous_rank:
            return "downgraded"
        return "changed"

    @classmethod
    def transition_commentary(cls, transition_direction: str, current_state: str) -> str:
        """Return commentary for a transition alert."""
        if transition_direction == "downgraded":
            return "Setup weakened. Stand down."
        return cls.commentary_for_state(current_state)

    @classmethod
    def commentary_for_state(cls, state: str) -> str:
        """Return analyst commentary for a confidence state."""
        return cls.COMMENTARY.get(state, cls.COMMENTARY["COLD"])

    @classmethod
    def action_for_state(cls, state: str) -> str:
        """Return terminal action label for a confidence state."""
        return action_for_state(state)

    @staticmethod
    def unavailable_symbol_result(symbol: str, error: str) -> dict[str, Any]:
        """Return a non-fatal unavailable symbol result."""
        return {
            "symbol": symbol,
            "available": False,
            "state": "UNAVAILABLE",
            "score": 0,
            "action": "UNAVAILABLE",
            "error": error,
            "confidence": None,
            "trade_plan": None,
        }

    @staticmethod
    def blocked_risk_result(reason: str) -> dict[str, Any]:
        """Return a safe blocked risk result when account risk cannot be read."""
        return {
            "permission": {
                "trade_allowed": False,
                "status": "BLOCKED",
                "block_reasons": [reason],
                "warnings": [],
            }
        }

    @property
    def symbols(self) -> list[str]:
        """Return configured monitor symbols."""
        symbols = self.config.get("symbols", self.DEFAULT_CONFIG["symbols"])
        return [str(symbol).upper().strip() for symbol in symbols]

    @property
    def environment(self) -> str:
        """Return configured monitor environment."""
        return (
            os.getenv("SENTINEL_ENV")
            or os.getenv("SENTINEL_ENVIRONMENT_MODE")
            or str(self.config.get("environment", "development"))
        ).lower().strip()

    @property
    def scan_interval_seconds(self) -> int:
        """Return environment-specific scan interval."""
        intervals = self.config.get("scan_interval_seconds", {})
        default_interval = self.DEFAULT_CONFIG["scan_interval_seconds"]["development"]
        return int(intervals.get(self.environment, default_interval))

    @property
    def terminal_alerts_enabled(self) -> bool:
        """Return whether terminal alerts are enabled."""
        return bool(self.config.get("alerts", {}).get("terminal", True))

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "monitoring.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using live monitor defaults.", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise LiveMonitorError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
