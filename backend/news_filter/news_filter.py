"""Manual high-impact news protection filter for Project Sentinel."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from loguru import logger


class NewsFilterError(RuntimeError):
    """Raised when the news filter cannot be configured."""


class NewsFilter:
    """Block new trade approvals around manually configured high-impact news."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "timezone": "WAT",
        "pre_event_block_minutes": 30,
        "post_event_block_minutes": 30,
        "events": [],
    }
    DEFAULT_AFFECTED_SYMBOLS = ("XAUUSD", "US30", "EURUSD", "GBPUSD")
    HIGH_IMPACT_KEYWORDS = (
        "CPI",
        "NFP",
        "FOMC",
        "FED RATE DECISION",
        "POWELL SPEECH",
        "PPI",
        "GDP",
        "UNEMPLOYMENT CLAIMS",
    )
    TIMEZONE_ALIASES = {
        "WAT": "Africa/Lagos",
        "AFRICA/LAGOS": "Africa/Lagos",
    }

    def __init__(self, config_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.config = self._load_config()
        self.timezone = ZoneInfo(self.resolve_timezone(str(self.config.get("timezone", "WAT"))))

    def check(self, symbol: str | None = None, current_time: datetime | str | None = None) -> dict[str, Any]:
        """Return whether a high-impact news lock is active."""
        enabled = bool(self.config.get("enabled", True))
        if not enabled:
            return self.empty_result(enabled=False)

        now = self.normalize_time(current_time)
        normalized_symbol = symbol.upper().strip() if symbol else None

        active_locks = []
        for event in self.config.get("events", []):
            if not self.is_blocking_event(event):
                continue
            affected_symbols = self.affected_symbols_for_event(event)
            if normalized_symbol and normalized_symbol not in affected_symbols:
                continue
            event_time = self.parse_event_time(str(event.get("datetime", "")))
            if self.is_inside_lock_window(now, event_time):
                active_locks.append((event, event_time, affected_symbols))

        if not active_locks:
            return self.empty_result(enabled=True)

        event, event_time, affected_symbols = min(active_locks, key=lambda item: abs(item[1] - now))
        minutes_to_event = self.minutes_to_event(now, event_time)
        event_name = str(event.get("name", "High impact news"))
        return {
            "enabled": True,
            "lock_active": True,
            "event_name": event_name,
            "minutes_to_event": minutes_to_event,
            "affected_symbols": affected_symbols,
            "reason": self.build_reason(event_name, minutes_to_event),
        }

    def is_inside_lock_window(self, now: datetime, event_time: datetime) -> bool:
        """Return whether now is inside the configured event lock window."""
        pre_minutes = int(self.config.get("pre_event_block_minutes", 30))
        post_minutes = int(self.config.get("post_event_block_minutes", 30))
        return event_time - timedelta(minutes=pre_minutes) <= now <= event_time + timedelta(minutes=post_minutes)

    def is_blocking_event(self, event: dict[str, Any]) -> bool:
        """Return whether an event is a configured high-impact USD event."""
        currency = str(event.get("currency", "")).upper().strip()
        impact = str(event.get("impact", "")).lower().strip()
        name = str(event.get("name", "")).upper()
        return (
            currency == "USD"
            and impact == "high"
            and any(keyword in name for keyword in self.HIGH_IMPACT_KEYWORDS)
            and bool(event.get("datetime"))
        )

    def affected_symbols_for_event(self, event: dict[str, Any]) -> list[str]:
        """Return symbols affected by this event."""
        configured = event.get("affected_symbols") or event.get("symbols")
        if configured:
            return [str(symbol).upper().strip() for symbol in configured]
        return list(self.DEFAULT_AFFECTED_SYMBOLS)

    def normalize_time(self, current_time: datetime | str | None) -> datetime:
        """Return current time normalized to the configured timezone."""
        if current_time is None:
            return datetime.now(self.timezone)
        if isinstance(current_time, str):
            current_time = datetime.fromisoformat(current_time)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=self.timezone)
        return current_time.astimezone(self.timezone)

    def parse_event_time(self, value: str) -> datetime:
        """Parse a config event datetime as WAT/local configured time."""
        try:
            event_time = datetime.fromisoformat(value)
        except ValueError as exc:
            raise NewsFilterError(f"Invalid news event datetime '{value}'.") from exc
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=self.timezone)
        return event_time.astimezone(self.timezone)

    @staticmethod
    def minutes_to_event(now: datetime, event_time: datetime) -> int:
        """Return whole minutes until event; negative means event already passed."""
        return round((event_time - now).total_seconds() / 60)

    @staticmethod
    def build_reason(event_name: str, minutes_to_event: int) -> str:
        """Return human-readable lock reason."""
        if minutes_to_event > 0:
            return f"High impact news lock active: {event_name} in {minutes_to_event} minutes."
        if minutes_to_event < 0:
            return f"High impact news lock active: {event_name} {abs(minutes_to_event)} minutes ago."
        return f"High impact news lock active: {event_name} now."

    @staticmethod
    def empty_result(enabled: bool) -> dict[str, Any]:
        """Return a clear/no-lock result."""
        return {
            "enabled": enabled,
            "lock_active": False,
            "event_name": None,
            "minutes_to_event": None,
            "affected_symbols": [],
            "reason": "",
        }

    @staticmethod
    def format_status(news_status: dict[str, Any]) -> str:
        """Return terminal-friendly news status."""
        if not news_status.get("enabled", True):
            return "DISABLED"
        if not news_status.get("lock_active", False):
            return "CLEAR"

        event_name = news_status.get("event_name", "High impact news")
        minutes_to_event = news_status.get("minutes_to_event")
        if minutes_to_event is None:
            return f"LOCKED - {event_name}"
        if minutes_to_event > 0:
            return f"LOCKED - {event_name} in {minutes_to_event} minutes"
        if minutes_to_event < 0:
            return f"LOCKED - {event_name} {abs(minutes_to_event)} minutes ago"
        return f"LOCKED - {event_name} now"

    @classmethod
    def resolve_timezone(cls, value: str) -> str:
        """Resolve config timezone labels to IANA timezone names."""
        normalized = value.upper().strip()
        return cls.TIMEZONE_ALIASES.get(normalized, value)

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "news_filter.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using news filter defaults.", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise NewsFilterError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
