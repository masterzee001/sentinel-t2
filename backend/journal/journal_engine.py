"""Decision journal engine for Project Sentinel Advisor Mode."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from loguru import logger

from backend.news_filter.news_filter import NewsFilter


class JournalEngineError(RuntimeError):
    """Raised when journaling cannot be completed."""


class JournalEngine:
    """Append Sentinel scan decisions to local JSONL storage."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "storage_type": "jsonl",
        "path": "data/journal/sentinel_decisions.jsonl",
        "record_diagnostic_plans": True,
        "record_rejected_setups": True,
    }
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")
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
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else self.project_root / "config"
        self.config = self._load_config()

    def append_record(self, record: dict[str, Any]) -> bool:
        """Append one sanitized record to the JSONL journal."""
        if not self.enabled:
            return False
        if self.storage_type != "jsonl":
            raise JournalEngineError(f"Unsupported journal storage_type '{self.storage_type}'.")

        sanitized = self.sanitize_record(record)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(sanitized, sort_keys=True, default=str) + "\n")
        return True

    def append_scan_records(
        self,
        *,
        environment: str,
        risk: dict[str, Any],
        news: dict[str, Any],
        symbol_payloads: list[dict[str, Any]],
        timestamp: datetime | None = None,
    ) -> int:
        """Append one journal record per symbol in a scan."""
        if not self.enabled:
            return 0

        records_written = 0
        for payload in symbol_payloads:
            record = self.build_record(
                environment=environment,
                risk=risk,
                news=news,
                symbol=str(payload.get("symbol", "")),
                trend=payload.get("trend", {}),
                ict=payload.get("ict", {}),
                killzone=payload.get("killzone", {}),
                confidence=payload.get("confidence", {}),
                trade_plan=payload.get("trade_plan", {}),
                commentary=str(payload.get("commentary", "")),
                timestamp=timestamp,
            )
            if not self.should_record(record):
                continue
            if self.append_record(record):
                records_written += 1
        return records_written

    def build_record(
        self,
        *,
        environment: str,
        risk: dict[str, Any],
        news: dict[str, Any],
        symbol: str,
        trend: dict[str, Any] | None = None,
        ict: dict[str, Any] | None = None,
        killzone: dict[str, Any] | None = None,
        confidence: dict[str, Any] | None = None,
        trade_plan: dict[str, Any] | None = None,
        commentary: str = "",
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Build a normalized Sentinel decision journal record."""
        trend = trend or {}
        ict = ict or {}
        confidence = confidence or {}
        killzone = killzone or confidence.get("killzone", {})
        trade_plan = trade_plan or {}
        risk_data = risk.get("risk", {})
        permission = risk.get("permission", {})
        account = risk.get("account", {})
        fvg = ict.get("fvg", {})
        mss = ict.get("mss", {})
        order_block = ict.get("order_block", {})
        entry = trade_plan.get("entry", {})
        stop_loss = trade_plan.get("stop_loss", {})
        take_profit = trade_plan.get("take_profit", {})
        trade_risk = trade_plan.get("risk", {})
        timestamp = timestamp or datetime.now(self.WAT_TIMEZONE)

        return {
            "timestamp": self.format_timestamp(timestamp),
            "environment": environment,
            "account": {
                "login": account.get("login", 0),
                "server": account.get("server", ""),
                "account_mode": account.get("account_mode", risk.get("account_mode", "")),
                "balance": account.get("balance", 0.0),
                "equity": account.get("equity", 0.0),
                "currency": account.get("currency", "USD"),
            },
            "risk": {
                "status": permission.get("status", "UNKNOWN"),
                "risk_amount": risk_data.get("risk_amount", 0.0),
                "warnings": permission.get("warnings", []),
                "block_reasons": permission.get("block_reasons", []),
            },
            "news": {
                "status": NewsFilter.format_status(news),
                "lock_active": bool(news.get("lock_active", False)),
                "event_name": news.get("event_name"),
                "reason": news.get("reason", ""),
            },
            "symbol": symbol.upper().strip(),
            "state": confidence.get("confidence_band", "UNAVAILABLE"),
            "confidence": confidence.get("total_confidence", 0),
            "decision": confidence.get("decision", "UNAVAILABLE"),
            "recommended_action": confidence.get("recommended_action", "UNAVAILABLE"),
            "rejection_reasons": confidence.get("rejection_reasons", []),
            "trend": {
                "daily_bias": trend.get("daily_bias", "unknown"),
                "h4_bias": trend.get("h4_bias", "unknown"),
                "h1_context": trend.get("h1_context", "unknown"),
            },
            "ict": {
                "mss_detected": bool(mss.get("detected", False)),
                "mss_direction": mss.get("direction"),
                "fvg_detected": bool(fvg.get("detected", False)),
                "fvg_direction": fvg.get("direction"),
                "fvg_grade": fvg.get("grade"),
                "order_block_detected": bool(order_block.get("detected", False)),
            },
            "killzone": {
                "active_killzone": killzone.get("active_killzone", "none"),
                "killzone_quality": killzone.get("quality_score", 0),
                "killzone_commentary": killzone.get("commentary", ""),
            },
            "smt": {
                "smt_detected": bool(confidence.get("smt", {}).get("smt_detected", False)),
                "smt_pair": confidence.get("smt", {}).get("pair_name", "none"),
                "smt_direction": confidence.get("smt", {}).get("direction"),
                "smt_confidence": confidence.get("smt", {}).get("confidence", 0),
            },
            "guardrail": {
                "status": confidence.get("guardrail_status", confidence.get("guardrail", {}).get("status", "PASS")),
                "reasons": confidence.get("guardrail_reasons", confidence.get("guardrail", {}).get("reasons", [])),
                "warnings": confidence.get("guardrail", {}).get("warnings", []),
            },
            "trade_plan": {
                "plan_quality": trade_plan.get("plan_quality", "unavailable"),
                "execution_allowed": bool(trade_plan.get("execution_allowed", False)),
                "direction": trade_plan.get("direction"),
                "entry": entry.get("price", 0.0),
                "stop_loss": stop_loss.get("price", 0.0),
                "tp1": take_profit.get("tp1", 0.0),
                "tp2": take_profit.get("tp2", 0.0),
                "tp3": take_profit.get("tp3", 0.0),
                "lot_size": trade_risk.get("lot_size", 0.0),
                "rr_to_tp1": trade_risk.get("rr_to_tp1", 0.0),
                "rr_to_tp2": trade_risk.get("rr_to_tp2", 0.0),
                "rr_to_tp3": trade_risk.get("rr_to_tp3", 0.0),
            },
            "commentary": commentary,
        }

    def should_record(self, record: dict[str, Any]) -> bool:
        """Return whether this record should be persisted by config rules."""
        trade_plan = record.get("trade_plan", {})
        if record.get("decision") == "REJECTED" and not bool(self.config.get("record_rejected_setups", True)):
            return False
        if trade_plan.get("plan_quality") == "diagnostic_only" and not bool(
            self.config.get("record_diagnostic_plans", True)
        ):
            return False
        return True

    def read_last_records(self, count: int = 1) -> list[dict[str, Any]]:
        """Read the last N journal records."""
        if count <= 0:
            return []
        if not self.journal_path.exists():
            return []

        with self.journal_path.open("r", encoding="utf-8") as file:
            lines = [line.strip() for line in file if line.strip()]
        return [json.loads(line) for line in lines[-count:]]

    def count_records(self) -> int:
        """Return number of JSONL records in the journal."""
        if not self.journal_path.exists():
            return 0
        with self.journal_path.open("r", encoding="utf-8") as file:
            return sum(1 for line in file if line.strip())

    @classmethod
    def sanitize_record(cls, value: Any) -> Any:
        """Remove credential-like fields from a record recursively."""
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                if str(key).lower() in cls.SENSITIVE_KEYS:
                    continue
                sanitized[key] = cls.sanitize_record(item)
            return sanitized
        if isinstance(value, list):
            return [cls.sanitize_record(item) for item in value]
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
            timestamp = timestamp.replace(tzinfo=JournalEngine.WAT_TIMEZONE)
        return timestamp.astimezone(JournalEngine.WAT_TIMEZONE).isoformat()

    @property
    def enabled(self) -> bool:
        """Return whether journaling is enabled."""
        return bool(self.config.get("enabled", True))

    @property
    def storage_type(self) -> str:
        """Return configured storage type."""
        return str(self.config.get("storage_type", "jsonl")).lower().strip()

    @property
    def journal_path(self) -> Path:
        """Return resolved journal path."""
        configured_path = Path(str(self.config.get("path", self.DEFAULT_CONFIG["path"])))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "journal.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using journal defaults.", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise JournalEngineError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
