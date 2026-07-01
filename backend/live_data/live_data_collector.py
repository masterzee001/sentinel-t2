"""Live production data collector for Project Sentinel Advisor Mode."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from loguru import logger

from backend.display.confidence_display import OBSERVER_ONLY_LABEL
from backend.shared.confidence_band_registry import (
    cumulative_funnel,
    exclusive_band_distribution,
    observer_state,
    rejection_reason_code,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIVE_DATA_PATH = PROJECT_ROOT / "data" / "live_data" / "live_signals.jsonl"


class LiveDataCollectorError(RuntimeError):
    """Raised when live data collection cannot continue safely."""


class LiveDataCollector:
    """Append compact live scan records and summarize collection analytics."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "symbols": ["XAUUSD", "US30", "EURUSD", "GBPUSD", "BTCUSD", "NAS100"],
        "capture_interval_seconds": 180,
        "record_states": ["COLD", "WARM", "HOT", "EXECUTION_READY"],
        "storage": {
            "format": "jsonl",
            "path": "data/live_data/live_signals.jsonl",
        },
        "retention": {
            "max_records": 100000,
        },
    }
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")
    SETUP_STATES = {"WARM", "HOT", "EXECUTION_READY"}
    SENSITIVE_KEYS = {
        "password",
        "pass",
        "api_key",
        "apikey",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "mt5_password",
        "mt5_login",
        "mt5_server",
    }

    def __init__(self, config_dir: str | Path | None = None, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root else PROJECT_ROOT
        self.config_dir = Path(config_dir) if config_dir else self.project_root / "config"
        self.config = self._load_config()
        self.active_setups: dict[str, dict[str, str]] = {}
        self._records_since_retention = 0

    def append_scan(self, scan: dict[str, Any], timestamp: datetime | None = None) -> int:
        """Append one live-data record per eligible symbol result in a scan."""
        if not self.enabled:
            return 0
        if self.storage_format != "jsonl":
            raise LiveDataCollectorError(f"Unsupported live data format '{self.storage_format}'.")

        records = []
        for result in scan.get("symbols", []):
            if not result.get("available", False):
                continue
            record = self.build_record(scan=scan, result=result, timestamp=timestamp)
            if record["state"] not in self.record_states:
                continue
            records.append(record)

        return self.append_records(records)

    def append_records(self, records: list[dict[str, Any]]) -> int:
        """Append already-normalized records to JSONL storage."""
        if not self.enabled or not records:
            return 0
        if self.storage_format != "jsonl":
            raise LiveDataCollectorError(f"Unsupported live data format '{self.storage_format}'.")

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self.storage_path.open("a", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(self.sanitize_record(record), sort_keys=True, default=str) + "\n")
        self._records_since_retention += len(records)
        self.enforce_retention_if_needed()
        return len(records)

    def build_record(
        self,
        *,
        scan: dict[str, Any],
        result: dict[str, Any],
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Return the stable live_signals.jsonl record shape."""
        timestamp = timestamp or datetime.now(self.WAT_TIMEZONE)
        confidence = result.get("confidence") or {}
        trade_plan = result.get("trade_plan") or {}
        killzone = result.get("killzone") or confidence.get("killzone", {}) or {}
        narrative = result.get("narrative") or confidence.get("narrative", {}) or {}
        smt = result.get("smt") or confidence.get("smt", {}) or {}
        symbol = str(result.get("symbol", "")).upper().strip()
        state = str(result.get("state") or confidence.get("confidence_band") or "UNAVAILABLE")
        symbol_mode = self.symbol_mode(symbol)
        raw_confidence = self.to_int(result.get("score", confidence.get("total_confidence", 0)))
        adjusted_confidence = self.adjusted_confidence(confidence, raw_confidence)
        rejection_reasons = self.rejection_reasons(confidence, trade_plan)

        record = {
            "timestamp": self.format_timestamp(timestamp),
            "symbol": symbol,
            "state": state,
            "confidence": raw_confidence,
            "adjusted_confidence": adjusted_confidence,
            "decision": self.extract_decision(result, confidence),
            "bias": self.extract_bias(result, narrative),
            "narrative_phase": narrative.get("phase", narrative.get("market_phase", "unknown")),
            "killzone": killzone.get("active_killzone", "none"),
            "killzone_quality": self.to_int(killzone.get("quality_score", killzone.get("killzone_quality", 0))),
            "smt_detected": bool(smt.get("smt_detected", False)),
            "smt_direction": smt.get("direction"),
            "risk_status": scan.get("risk_status") or scan.get("risk", {}).get("permission", {}).get("status", "UNKNOWN"),
            "news_status": scan.get("news_status") or scan.get("news", {}).get("status", "UNKNOWN"),
            "execution_allowed": bool(result.get("execution_allowed", trade_plan.get("execution_allowed", False))),
            "rejection_reasons": rejection_reasons,
            "rejection_reason_codes": [rejection_reason_code(reason) for reason in rejection_reasons],
            "observer_state": observer_state(state) if symbol_mode != "production" else None,
            "state_kind": "OBSERVER_MOVEMENT" if symbol_mode != "production" else "PRODUCTION_CONFIDENCE",
            "symbol_mode": symbol_mode,
        }
        record["setup_id"] = self.setup_id_for(record)
        return record

    def setup_id_for(self, record: dict[str, Any]) -> str:
        """Return a correlation key for the current setup progression."""
        symbol = str(record.get("symbol", "")).upper().strip()
        state = str(record.get("state", "COLD"))
        setup_key = self.setup_key(record)
        active = self.active_setups.get(symbol)
        if active and active.get("key") == setup_key:
            setup_id = active["setup_id"]
        else:
            setup_id = self.generate_setup_id(record)
            self.active_setups[symbol] = {"key": setup_key, "setup_id": setup_id}

        if state == "COLD":
            self.active_setups.pop(symbol, None)
        return setup_id

    @classmethod
    def generate_setup_id(cls, record: dict[str, Any]) -> str:
        """Generate a stable setup ID from symbol, date, and setup context."""
        timestamp = str(record.get("timestamp", ""))
        date_part = timestamp[:10].replace("-", "") if len(timestamp) >= 10 else "unknown"
        symbol = str(record.get("symbol", "UNKNOWN")).upper().strip() or "UNKNOWN"
        digest = hashlib.sha1(cls.setup_key(record).encode("utf-8")).hexdigest()[:10]
        return f"{symbol}-{date_part}-{digest}"

    @staticmethod
    def setup_key(record: dict[str, Any]) -> str:
        """Return the setup identity dimensions used for progression analysis."""
        parts = [
            record.get("symbol", ""),
            str(record.get("timestamp", ""))[:10],
            record.get("bias", "unknown"),
            record.get("narrative_phase", "unknown"),
            record.get("killzone", "none"),
            record.get("smt_direction") or "none",
        ]
        return "|".join(str(part).lower().strip() for part in parts)

    def summary(self, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Return collection analytics grouped by symbol, killzone, narrative, and rejections."""
        records = records if records is not None else self.read_records()
        symbol_stats: dict[str, dict[str, Any]] = {
            symbol: {
                "total_scans": 0,
                "warm": 0,
                "hot": 0,
                "execution_ready": 0,
                "symbol_mode": self.symbol_mode(symbol),
            }
            for symbol in self.symbols
        }
        killzone_setups: dict[str, set[str]] = {}
        narrative_setups: dict[str, set[str]] = {}
        rejection_reasons: Counter[str] = Counter()
        setup_ids: set[str] = set()
        production_bands: list[str] = []
        approved_trades = 0

        for record in records:
            symbol = str(record.get("symbol", "")).upper().strip()
            state = str(record.get("state", "")).upper().strip()
            symbol_mode = str(record.get("symbol_mode", "production")).lower().strip()
            stats = symbol_stats.setdefault(
                symbol,
                {
                    "total_scans": 0,
                    "warm": 0,
                    "hot": 0,
                    "execution_ready": 0,
                    "symbol_mode": record.get("symbol_mode", "production"),
                },
            )
            stats["total_scans"] += 1
            stats["symbol_mode"] = symbol_mode or stats.get("symbol_mode", "production")
            if state == "WARM":
                stats["warm"] += 1
            elif state == "HOT":
                stats["hot"] += 1
            elif state == "EXECUTION_READY":
                stats["execution_ready"] += 1

            if symbol_mode == "production":
                production_bands.append(state)
                if bool(record.get("execution_allowed", False)):
                    approved_trades += 1

            setup_id = str(record.get("setup_id", "")).strip()
            if setup_id and state in self.SETUP_STATES:
                setup_ids.add(setup_id)
                killzone_setups.setdefault(str(record.get("killzone", "none")), set()).add(setup_id)
                narrative_setups.setdefault(str(record.get("narrative_phase", "unknown")), set()).add(setup_id)

            reason_codes = record.get("rejection_reason_codes") or []
            if reason_codes:
                for reason in reason_codes:
                    rejection_reasons[self.rejection_bucket(reason)] += 1
            else:
                for reason in record.get("rejection_reasons", []) or []:
                    rejection_reasons[self.rejection_bucket(reason)] += 1

        exclusive = exclusive_band_distribution(production_bands)

        return {
            "available": bool(records),
            "total_records": len(records),
            "setup_count": len(setup_ids),
            "symbols": symbol_stats,
            "killzones": {name: len(ids) for name, ids in killzone_setups.items()},
            "narratives": {name: len(ids) for name, ids in narrative_setups.items()},
            "rejection_reasons": dict(rejection_reasons),
            "exclusive_band_distribution": exclusive,
            "cumulative_funnel": cumulative_funnel(
                qualifying_setups=len(setup_ids),
                exclusive_distribution=exclusive,
                approved_trades=approved_trades,
                wins=0,
                losses=0,
                total_scans=len(production_bands),
            ),
        }

    def read_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Read live-data JSONL records, ignoring malformed lines."""
        if not self.storage_path.exists():
            return []
        records = []
        with self.storage_path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    records.append(json.loads(text))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed live data line {} in {}", line_number, self.storage_path)
        return records[-limit:] if limit else records

    def enforce_retention_if_needed(self) -> None:
        """Trim JSONL storage to max_records without doing heavy work every scan."""
        max_records = self.max_records
        if max_records <= 0:
            return
        if max_records > 5000 and self._records_since_retention < 500:
            return
        self._records_since_retention = 0
        if not self.storage_path.exists():
            return
        with self.storage_path.open("r", encoding="utf-8") as file:
            lines = [line for line in file if line.strip()]
        if len(lines) <= max_records:
            return
        temp_path = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        temp_path.write_text("".join(lines[-max_records:]), encoding="utf-8")
        temp_path.replace(self.storage_path)

    @classmethod
    def format_live_stats(cls, summary: dict[str, Any]) -> str:
        """Return Telegram-friendly live data statistics."""
        if not summary.get("available"):
            return "No live data stats available yet."
        lines = ["<b>Live Data Stats</b>"]
        for symbol, stats in summary.get("symbols", {}).items():
            if int(stats.get("total_scans", 0) or 0) == 0:
                continue
            lines.extend(
                [
                    "",
                    f"{symbol}:",
                    f"Warm: {int(stats.get('warm', 0) or 0)}",
                    f"Hot: {int(stats.get('hot', 0) or 0)}",
                    f"Exec Ready: {int(stats.get('execution_ready', 0) or 0)}",
                ]
            )
            if stats.get("symbol_mode") == "demo_sandbox":
                lines.append("Mode: DEMO_SANDBOX")
            elif stats.get("symbol_mode") in {"experimental", "observer", "observer_only", OBSERVER_ONLY_LABEL}:
                lines.append(f"Mode: {OBSERVER_ONLY_LABEL}")
        return "\n".join(lines)

    @classmethod
    def adjusted_confidence(cls, confidence: dict[str, Any], fallback: int) -> int:
        """Return guardrail-adjusted confidence when available."""
        guardrail = confidence.get("guardrail", {}) if isinstance(confidence, dict) else {}
        candidates = [
            guardrail.get("guardrail_adjusted_confidence") if isinstance(guardrail, dict) else None,
            guardrail.get("adjusted_confidence") if isinstance(guardrail, dict) else None,
            confidence.get("adjusted_confidence") if isinstance(confidence, dict) else None,
        ]
        for candidate in candidates:
            if candidate is not None:
                return cls.to_int(candidate)
        return fallback

    @staticmethod
    def rejection_reasons(confidence: dict[str, Any], trade_plan: dict[str, Any]) -> list[str]:
        """Return deduplicated confidence and planner rejection reasons."""
        reasons: list[str] = []
        for source in (confidence.get("rejection_reasons", []), trade_plan.get("rejection_reasons", [])):
            for reason in source or []:
                text = str(reason).strip()
                if text and text not in reasons:
                    reasons.append(text)
        return reasons

    @staticmethod
    def rejection_bucket(reason: Any) -> str:
        """Normalize rejection reasons to the shared allowed truth-layer codes."""
        return rejection_reason_code(reason)

    @staticmethod
    def extract_bias(result: dict[str, Any], narrative: dict[str, Any]) -> str:
        """Extract directional bias from narrative or trend context."""
        trend = result.get("trend", {}) or {}
        return str(
            narrative.get("bias")
            or trend.get("daily_bias")
            or trend.get("overall_bias")
            or trend.get("h4_bias")
            or "unknown"
        )

    @staticmethod
    def extract_decision(result: dict[str, Any], confidence: dict[str, Any]) -> str:
        """Return the operator-facing scan decision for live-data records."""
        value = (
            result.get("action")
            or result.get("decision")
            or confidence.get("recommended_action")
            or confidence.get("decision")
            or "UNAVAILABLE"
        )
        return str(value).upper().strip()

    @staticmethod
    def sanitize_record(value: Any) -> Any:
        """Remove credential-like fields recursively."""
        if isinstance(value, dict):
            return {
                key: LiveDataCollector.sanitize_record(item)
                for key, item in value.items()
                if str(key).lower() not in LiveDataCollector.SENSITIVE_KEYS
            }
        if isinstance(value, list):
            return [LiveDataCollector.sanitize_record(item) for item in value]
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return str(value)
        return value

    @staticmethod
    def format_timestamp(timestamp: datetime) -> str:
        """Return ISO timestamp in WAT."""
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=LiveDataCollector.WAT_TIMEZONE)
        return timestamp.astimezone(LiveDataCollector.WAT_TIMEZONE).isoformat()

    @staticmethod
    def to_int(value: Any) -> int:
        """Return an integer from numeric-looking values."""
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def symbol_mode(symbol: str) -> str:
        """Return live-data symbol mode."""
        normalized = str(symbol).upper().strip()
        if normalized in {"BTCUSD", "NAS100"}:
            return "demo_sandbox"
        if normalized in {"EURUSD", "GBPUSD"}:
            return "observer_only"
        return "production"

    @property
    def enabled(self) -> bool:
        """Return whether live data collection is enabled."""
        return bool(self.config.get("enabled", True))

    @property
    def symbols(self) -> list[str]:
        """Return configured live-data symbols."""
        return [str(symbol).upper().strip() for symbol in self.config.get("symbols", self.DEFAULT_CONFIG["symbols"])]

    @property
    def record_states(self) -> set[str]:
        """Return states eligible for collection."""
        return {str(state).upper().strip() for state in self.config.get("record_states", self.DEFAULT_CONFIG["record_states"])}

    @property
    def storage_format(self) -> str:
        """Return configured storage format."""
        return str(self.config.get("storage", {}).get("format", "jsonl")).lower().strip()

    @property
    def storage_path(self) -> Path:
        """Return resolved live-data JSONL path."""
        configured_path = Path(str(self.config.get("storage", {}).get("path", self.DEFAULT_CONFIG["storage"]["path"])))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    @property
    def max_records(self) -> int:
        """Return retention limit."""
        return int(self.config.get("retention", {}).get("max_records", 100000) or 0)

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "live_data.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using live data defaults.", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise LiveDataCollectorError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
