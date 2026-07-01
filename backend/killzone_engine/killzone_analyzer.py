"""ICT killzone analysis engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from loguru import logger


class KillzoneAnalyzerError(RuntimeError):
    """Raised when killzone analysis cannot be completed."""


class KillzoneAnalyzer:
    """Determine active ICT killzones for supported Sentinel symbols."""

    DEFAULT_ALLOWED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD", "BTCUSD", "NAS100"})
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")
    DEFAULT_CONFIG = {
        "timezone": "WAT",
        "killzones": {},
    }

    def __init__(self, config_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.config = self._load_config()
        self.killzones = self.config.get("killzones", {})

    def analyze(self, symbol: str, current_time: datetime | str | None = None) -> dict[str, Any]:
        """Return active killzone status for a symbol at the supplied or current WAT time."""
        normalized_symbol = self._validate_symbol(symbol)
        wat_time = self.normalize_time(current_time)
        current_minute = wat_time.hour * 60 + wat_time.minute
        active_name, active_config = self.find_active_killzone(normalized_symbol, current_minute)
        quality_score = int(active_config.get("quality_score", 0)) if active_config else 0
        commentary = str(active_config.get("commentary", "No active killzone.")) if active_config else "No active killzone."

        return {
            "symbol": normalized_symbol,
            "current_time_wat": wat_time.strftime("%H:%M"),
            "active_killzone": active_name or "none",
            "is_valid": quality_score > 0,
            "quality_score": quality_score,
            "commentary": commentary,
            "minutes_to_next_killzone": self.minutes_to_next_killzone(normalized_symbol, current_minute, active_name),
        }

    def find_active_killzone(self, symbol: str, current_minute: int) -> tuple[str | None, dict[str, Any] | None]:
        """Return the active symbol-eligible killzone, if any."""
        for name, killzone in self.killzones.items():
            if symbol not in self.normalize_symbols(killzone.get("symbols", [])):
                continue
            start = self.parse_hhmm(str(killzone.get("start", "00:00")))
            end = self.parse_hhmm(str(killzone.get("end", "00:00")))
            if self.is_minute_in_window(current_minute, start, end):
                return str(name), killzone
        return None, None

    def minutes_to_next_killzone(
        self,
        symbol: str,
        current_minute: int,
        active_killzone: str | None = None,
    ) -> int:
        """Return minutes until the next configured symbol-eligible killzone start."""
        starts = []
        for name, killzone in self.killzones.items():
            if name == active_killzone:
                continue
            if symbol not in self.normalize_symbols(killzone.get("symbols", [])):
                continue
            starts.append(self.parse_hhmm(str(killzone.get("start", "00:00"))))

        if not starts:
            return 0

        future_starts = [start for start in starts if start > current_minute]
        if future_starts:
            return min(future_starts) - current_minute
        return (24 * 60 - current_minute) + min(starts)

    @classmethod
    def normalize_time(cls, current_time: datetime | str | None) -> datetime:
        """Return supplied or current time normalized to WAT."""
        if current_time is None:
            return datetime.now(cls.WAT_TIMEZONE)
        if isinstance(current_time, str):
            today = datetime.now(cls.WAT_TIMEZONE)
            hour, minute = current_time.split(":", maxsplit=1)
            return today.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=cls.WAT_TIMEZONE)
        return current_time.astimezone(cls.WAT_TIMEZONE)

    @staticmethod
    def is_minute_in_window(current_minute: int, start: int, end: int) -> bool:
        """Return whether current minute is inside a same-day or overnight window."""
        if start <= end:
            return start <= current_minute < end
        return current_minute >= start or current_minute < end

    @staticmethod
    def parse_hhmm(value: str) -> int:
        """Parse HH:MM into minutes from midnight."""
        hour, minute = value.split(":", maxsplit=1)
        return int(hour) * 60 + int(minute)

    @staticmethod
    def normalize_symbols(symbols: list[Any]) -> set[str]:
        """Return uppercase symbol set."""
        return {str(symbol).upper().strip() for symbol in symbols}

    @staticmethod
    def display_name(killzone_name: str) -> str:
        """Return terminal-friendly killzone name."""
        if not killzone_name or killzone_name == "none":
            return "None"
        return killzone_name.replace("_", " ").title()

    def _validate_symbol(self, symbol: str) -> str:
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol not in self.DEFAULT_ALLOWED_SYMBOLS:
            supported = ", ".join(sorted(self.DEFAULT_ALLOWED_SYMBOLS))
            raise ValueError(f"Unsupported symbol '{symbol}'. Supported symbols: {supported}.")
        return normalized_symbol

    def _load_config(self) -> dict[str, Any]:
        config_path = self.config_dir / "killzones.yaml"
        if not config_path.exists():
            logger.warning("Config file {} does not exist; using killzone defaults.", config_path)
            return dict(self.DEFAULT_CONFIG)

        try:
            with config_path.open("r", encoding="utf-8") as file:
                config = yaml.safe_load(file) or {}
        except Exception as exc:
            raise KillzoneAnalyzerError(f"Failed to load config {config_path}: {exc}") from exc

        return {**self.DEFAULT_CONFIG, **config}
