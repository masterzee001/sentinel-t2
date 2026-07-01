"""Alert engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from loguru import logger

from backend.display.confidence_display import action_for_state as display_action_for_state


class AlertEngineError(RuntimeError):
    """Raised when alert engine configuration fails."""


class AlertEngine:
    """Detect meaningful state changes and dispatch configured alerts."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "terminal": True,
        "desktop": False,
        "telegram": False,
        "telegram_settings": {
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        "alert_on": [
            "WARM_TO_HOT",
            "HOT_TO_EXECUTION_READY",
            "EXECUTION_READY_TO_LOWER",
            "RISK_BLOCKED",
            "NEWS_LOCK_ACTIVE",
        ],
        "cooldown_minutes": 5,
        "observer_symbols": ["BTCUSD", "NAS100"],
        "observer_hot_realert_confidence_delta": 5,
    }
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")
    COMMENTARY = {
        "WARM_TO_HOT": "Setup close. Wait for confirmation.",
        "HOT_TO_EXECUTION_READY": "A-grade setup detected. Prepare execution.",
        "EXECUTION_READY_TO_LOWER": "Setup weakened. Stand down.",
        "RISK_BLOCKED": "Risk Governor blocked trading. Stand down.",
        "NEWS_LOCK_ACTIVE": "High impact news lock active. Stand down.",
    }

    TELEGRAM_API_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, config_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        load_dotenv()
        self.config = self._load_config()
        self.last_alert_times: dict[tuple[str, str], datetime] = {}
        self.last_alert_confidence: dict[tuple[str, str], int] = {}
        self.last_telegram_warning: str | None = None
        self.last_telegram_http_status: int | None = None

    def evaluate(
        self,
        *,
        symbol: str,
        previous_state: str | None,
        current_state: str,
        confidence: int,
        risk_status: str = "ALLOWED",
        news_lock_active: bool = False,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Return an alert object for a meaningful transition, or suppressed result."""
        timestamp = self.normalize_timestamp(timestamp)
        normalized_symbol = symbol.upper().strip()
        transition = self.detect_transition(
            previous_state=previous_state,
            current_state=current_state,
            risk_status=risk_status,
            news_lock_active=news_lock_active,
        )

        if not self.enabled or transition is None or transition not in self.alert_on:
            return self.no_alert(normalized_symbol, transition, confidence, timestamp)

        if self.is_in_cooldown(normalized_symbol, transition, timestamp) and not self.observer_realert_allowed(
            normalized_symbol,
            transition,
            confidence,
        ):
            return self.no_alert(normalized_symbol, transition, confidence, timestamp, suppressed_by_cooldown=True)

        self.last_alert_times[(normalized_symbol, transition)] = timestamp
        self.last_alert_confidence[(normalized_symbol, transition)] = int(confidence)
        previous_label = previous_state or "UNKNOWN"
        message = self.build_message(
            symbol=normalized_symbol,
            previous_state=previous_label,
            current_state=current_state,
            transition=transition,
        )
        alert = {
            "alert_triggered": True,
            "symbol": normalized_symbol,
            "previous_state": previous_label,
            "current_state": current_state,
            "transition": transition,
            "message": message,
            "confidence": confidence,
            "action": self.action_for_state(current_state, transition),
            "commentary": self.COMMENTARY[transition],
            "timestamp": timestamp.isoformat(),
            "telegram_sent": False,
            "warnings": [],
        }
        if self.telegram_enabled:
            telegram_sent = self.send_telegram_alert(self.format_telegram_message(alert))
            alert["telegram_sent"] = telegram_sent
            if not telegram_sent:
                if self.last_telegram_warning in {
                    "Telegram enabled but TELEGRAM_BOT_TOKEN missing",
                    "Telegram enabled but TELEGRAM_CHAT_ID missing",
                }:
                    alert["warnings"].append("Telegram credentials missing")
                else:
                    alert["warnings"].append(self.last_telegram_warning or "Telegram alert not sent")
        return alert

    @classmethod
    def detect_transition(
        cls,
        *,
        previous_state: str | None,
        current_state: str,
        risk_status: str = "ALLOWED",
        news_lock_active: bool = False,
    ) -> str | None:
        """Return a configured transition label for meaningful alert states."""
        if risk_status in {"BLOCKED", "HARD_BLOCKED"}:
            return "RISK_BLOCKED"
        if news_lock_active:
            return "NEWS_LOCK_ACTIVE"
        if previous_state is None or previous_state == current_state:
            return None
        if previous_state == "WARM" and current_state == "HOT":
            return "WARM_TO_HOT"
        if previous_state == "HOT" and current_state == "EXECUTION_READY":
            return "HOT_TO_EXECUTION_READY"
        if previous_state == "EXECUTION_READY" and current_state in {"HOT", "WARM", "COLD"}:
            return "EXECUTION_READY_TO_LOWER"
        return None

    def is_in_cooldown(self, symbol: str, transition: str, timestamp: datetime) -> bool:
        """Return whether the symbol/transition is inside alert cooldown."""
        last_alert_time = self.last_alert_times.get((symbol, transition))
        if last_alert_time is None:
            return False
        return timestamp < last_alert_time + timedelta(minutes=self.cooldown_minutes)

    def observer_realert_allowed(self, symbol: str, transition: str, confidence: int) -> bool:
        """Return whether observer WARM->HOT is materially stronger inside cooldown."""
        if symbol not in self.observer_symbols or transition != "WARM_TO_HOT":
            return False
        previous = self.last_alert_confidence.get((symbol, transition))
        if previous is None:
            return False
        return int(confidence) >= previous + self.observer_hot_realert_confidence_delta

    @classmethod
    def build_message(cls, symbol: str, previous_state: str, current_state: str, transition: str) -> str:
        """Return a concise terminal alert message."""
        if transition == "RISK_BLOCKED":
            return f"{symbol} blocked by risk. {cls.COMMENTARY[transition]}"
        if transition == "NEWS_LOCK_ACTIVE":
            return f"{symbol} blocked by news lock. {cls.COMMENTARY[transition]}"
        action = "upgraded" if transition in {"WARM_TO_HOT", "HOT_TO_EXECUTION_READY"} else "downgraded"
        return f"{symbol} {action} {previous_state} -> {current_state}. {cls.COMMENTARY[transition]}"

    @staticmethod
    def format_terminal_alert(alert: dict[str, Any]) -> str:
        """Return clean terminal alert text."""
        if not alert.get("alert_triggered", False):
            return ""
        return "\n".join(
            [
                "ALERT:",
                str(alert.get("message", "")),
                f"Confidence: {alert.get('confidence', 0)}",
                f"Transition: {alert.get('transition', '')}",
                f"Timestamp: {alert.get('timestamp', '')}",
            ]
        )


    @staticmethod
    def format_telegram_message(alert: dict[str, Any]) -> str:
        """Return HTML-formatted Telegram alert text."""
        previous_state = str(alert.get("previous_state", "UNKNOWN"))
        current_state = str(alert.get("current_state", "UNKNOWN"))
        arrow = "\u2192"
        em_dash = "\u2014"
        return "\n".join(
            [
                "<b>PROJECT SENTINEL ALERT</b>",
                "",
                f"Symbol: {alert.get('symbol', '')}",
                f"Transition: {previous_state} {arrow} {current_state}",
                f"Confidence: {alert.get('confidence', 0)}",
                f"Action: {alert.get('action', 'WAIT')}",
                "",
                "Commentary:",
                str(alert.get("commentary", "")),
                "",
                f"Advisor Mode only {em_dash} no trade execution.",
            ]
        )

    @staticmethod
    def format_paper_lifecycle_message(event: dict[str, Any]) -> str:
        """Return HTML-formatted paper-drill lifecycle text."""
        event_type = str(event.get("event", "PAPER_EVENT")).upper()
        symbol = str(event.get("symbol", "UNKNOWN"))
        current_r = event.get("current_r", 0.0)
        realized_rr = event.get("realized_rr")
        outcome = event.get("outcome")
        messages = {
            "SIGNAL_DETECTED": "Signal detected",
            "ORDER_APPROVED": "Order approved",
            "POSITION_1R": "Position reached 1R",
            "POSITION_2R": "Partial close at 2R",
            "TRADE_CLOSED": f"Trade closed {PaperLifecycleFormatter.format_rr(realized_rr)}",
        }
        headline = messages.get(event_type, event_type.replace("_", " ").title())
        lines = [
            "<b>PROJECT SENTINEL PAPER DRILL</b>",
            "",
            f"Symbol: {symbol}",
            f"Event: {headline}",
            f"Current R: {current_r}",
        ]
        if outcome:
            lines.append(f"Outcome: {outcome}")
        lines.extend(["", "Paper rehearsal only. No broker order submitted."])
        return "\n".join(lines)

    def validate_telegram_config(self) -> dict[str, bool]:
        """Return Telegram configuration readiness without exposing credentials."""
        telegram_enabled = self.telegram_enabled
        token_loaded = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
        chat_id_loaded = bool(os.getenv("TELEGRAM_CHAT_ID"))
        return {
            "telegram_enabled": telegram_enabled,
            "token_loaded": token_loaded,
            "chat_id_loaded": chat_id_loaded,
            "valid": telegram_enabled and token_loaded and chat_id_loaded,
        }

    def send_telegram_alert(self, message: str) -> bool:
        """Send a Telegram alert message when Telegram alerts are enabled."""
        self.last_telegram_warning = None
        self.last_telegram_http_status = None
        if not self.enabled or not self.telegram_enabled:
            return False

        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token:
            self.last_telegram_warning = "Telegram enabled but TELEGRAM_BOT_TOKEN missing"
            logger.warning(self.last_telegram_warning)
            return False
        if not chat_id:
            self.last_telegram_warning = "Telegram enabled but TELEGRAM_CHAT_ID missing"
            logger.warning(self.last_telegram_warning)
            return False

        telegram_settings = self.config.get("telegram_settings", {})
        disable_preview = bool(telegram_settings.get("disable_web_page_preview", True))
        payload = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": str(telegram_settings.get("parse_mode", "HTML")),
                "disable_web_page_preview": "true" if disable_preview else "false",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.TELEGRAM_API_TEMPLATE.format(token=token),
            data=payload,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                self.last_telegram_http_status = int(response.status)
                sent = 200 <= int(response.status) < 300
                if not sent:
                    self.last_telegram_warning = f"Telegram API returned status {response.status}"
                return sent
        except Exception as exc:
            self.last_telegram_warning = "Telegram alert send failed"
            logger.warning("{}: {}", self.last_telegram_warning, exc)
            return False

    @staticmethod
    def action_for_state(current_state: str, transition: str) -> str:
        """Return action label for alert state."""
        if transition in {"RISK_BLOCKED", "NEWS_LOCK_ACTIVE"}:
            return "STAND DOWN"
        return display_action_for_state(current_state)

    @staticmethod
    def no_alert(
        symbol: str,
        transition: str | None,
        confidence: int,
        timestamp: datetime,
        suppressed_by_cooldown: bool = False,
    ) -> dict[str, Any]:
        """Return a standard no-alert object."""
        return {
            "alert_triggered": False,
            "symbol": symbol,
            "transition": transition,
            "message": "",
            "confidence": confidence,
            "timestamp": timestamp.isoformat(),
            "suppressed_by_cooldown": suppressed_by_cooldown,
        }

    @classmethod
    def normalize_timestamp(cls, timestamp: datetime | None) -> datetime:
        """Return timestamp normalized to WAT."""
        timestamp = timestamp or datetime.now(cls.WAT_TIMEZONE)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=cls.WAT_TIMEZONE)
        return timestamp.astimezone(cls.WAT_TIMEZONE)

    @property
    def enabled(self) -> bool:
        """Return whether alerts are enabled."""
        return bool(self.config.get("enabled", True))

    @property
    def terminal_enabled(self) -> bool:
        """Return whether terminal alerts are enabled."""
        return bool(self.config.get("terminal", True))

    @property
    def telegram_enabled(self) -> bool:
        """Return whether Telegram alerts are enabled."""
        return bool(self.config.get("telegram", False))

    @property
    def alert_on(self) -> set[str]:
        """Return enabled transition labels."""
        return {str(item).upper().strip() for item in self.config.get("alert_on", [])}

    @property
    def cooldown_minutes(self) -> int:
        """Return alert cooldown in minutes."""
        return int(self.config.get("cooldown_minutes", 5))

    @property
    def observer_symbols(self) -> set[str]:
        """Return observer symbols that need stronger WARM->HOT hysteresis."""
        return {str(symbol).upper().strip() for symbol in self.config.get("observer_symbols", self.DEFAULT_CONFIG["observer_symbols"])}

    @property
    def observer_hot_realert_confidence_delta(self) -> int:
        """Return confidence delta needed for observer WARM->HOT re-alerts inside cooldown."""
        return int(self.config.get("observer_hot_realert_confidence_delta", self.DEFAULT_CONFIG["observer_hot_realert_confidence_delta"]))

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "alerts.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using alert defaults.", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise AlertEngineError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged


class PaperLifecycleFormatter:
    """Small formatting helper for paper lifecycle messages."""

    @staticmethod
    def format_rr(value: Any) -> str:
        if value is None:
            return ""
        numeric = float(value)
        if numeric > 0:
            return f"+{numeric:g}R"
        return f"{numeric:g}R"
