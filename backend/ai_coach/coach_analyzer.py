"""Rule-based AI Coach analytics for Project Sentinel Advisor Mode."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from backend.journal.journal_engine import JournalEngine
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer


class AICoachError(RuntimeError):
    """Raised when AI Coach analytics cannot be completed."""


class AICoachAnalyzer:
    """Analyze journal and backtest diagnostics into coaching recommendations."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "mode": "advisor",
        "journal_path": "data/journal/sentinel_decisions.jsonl",
        "fallback_to_synthetic": True,
        "minimum_bucket_trades": 1,
        "low_win_rate_warning": 45.0,
        "critical_drawdown_percent": 6.0,
        "news_lock_warning_rate": 0.25,
        "risk_block_warning_rate": 0.15,
        "execution_ready_target_rate": 0.10,
        "safe_summary_max_recommendations": 2,
    }
    SEVERITIES = {"INFO", "WARNING", "CRITICAL"}
    CATEGORIES = {
        "symbol",
        "session",
        "confidence",
        "risk",
        "execution",
        "guardrail",
        "psychology",
    }
    SENSITIVE_KEYS = JournalEngine.SENSITIVE_KEYS | {
        "credential",
        "credentials",
        "authorization",
        "auth",
        "bot_token",
        "chat_id",
        "webhook",
    }

    def __init__(
        self,
        config_dir: str | Path | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else self.project_root / "config"
        self.config = self._load_config()

    def analyze(
        self,
        *,
        journal_records: list[dict[str, Any]] | None = None,
        backtest_summary: dict[str, Any] | None = None,
        use_synthetic_if_empty: bool | None = None,
    ) -> dict[str, Any]:
        """Return a coaching report with no execution side effects."""
        if not self.enabled:
            return self.empty_report("AI Coach disabled in config.", severity="WARNING")
        if self.mode != "advisor":
            raise AICoachError("AI Coach v0.1 supports Advisor Mode only.")

        records = journal_records
        if records is None:
            records = self.read_journal_records()
        fallback_enabled = self.fallback_to_synthetic if use_synthetic_if_empty is None else use_synthetic_if_empty
        if not records and fallback_enabled:
            records = self.synthetic_journal_records()

        records = [self.sanitize_record(record) for record in records]
        backtest_summary = self.sanitize_record(backtest_summary or {})
        journal_analysis = self.analyze_journal(records)
        backtest_analysis = self.analyze_backtest(backtest_summary)

        strengths = self.build_strengths(journal_analysis, backtest_analysis)
        weaknesses = self.build_weaknesses(journal_analysis, backtest_analysis)
        recommendations = self.build_recommendations(journal_analysis, backtest_analysis)
        risk_notes = self.build_risk_notes(journal_analysis, backtest_analysis)
        next_actions = self.build_next_actions(journal_analysis, backtest_analysis)

        report = {
            "coach_status": "READY",
            "summary": self.build_summary(journal_analysis, backtest_analysis, recommendations),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendations": recommendations,
            "risk_notes": risk_notes,
            "next_actions": next_actions,
        }
        return self.sanitize_record(report)

    def read_journal_records(self) -> list[dict[str, Any]]:
        """Read local JSONL journal records."""
        path = self.journal_path
        if not path.exists():
            return []

        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    records.append(json.loads(text))
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed AI Coach journal line {}: {}", line_number, exc)
        return records

    def analyze_journal(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Return frequency analytics from journal decision records."""
        total = len(records)
        symbols = Counter()
        states = Counter()
        killzones = Counter()
        narrative_phases = Counter()
        confidence_bands = Counter()
        rejection_reasons = Counter()
        guardrail_reasons = Counter()
        guardrail_statuses = Counter()
        risk_warnings = Counter()
        news_locks = 0
        execution_ready = 0
        execution_allowed = 0
        risk_blocked = 0

        for record in records:
            symbol = self.normalized_text(record.get("symbol"), default="UNKNOWN").upper()
            state = self.normalized_text(record.get("state", record.get("confidence_band")), default="UNAVAILABLE").upper()
            symbols[symbol] += 1
            states[state] += 1
            confidence_bands[state] += 1
            if state == "EXECUTION_READY":
                execution_ready += 1

            killzone = self.extract_killzone(record)
            killzones[killzone] += 1
            phase = self.extract_narrative_phase(record)
            if phase != "none":
                narrative_phases[phase] += 1

            if bool(record.get("trade_plan", {}).get("execution_allowed", False)):
                execution_allowed += 1

            news = record.get("news", {})
            if bool(news.get("lock_active", False)) or str(news.get("status", "")).upper() == "LOCKED":
                news_locks += 1

            risk = record.get("risk", {})
            for warning in risk.get("warnings", []) or []:
                risk_warnings[str(warning)] += 1
            block_reasons = risk.get("block_reasons", []) or []
            if block_reasons or str(risk.get("status", "")).upper() == "BLOCKED":
                risk_blocked += 1
            for reason in block_reasons:
                risk_warnings[str(reason)] += 1

            for reason in record.get("rejection_reasons", []) or []:
                rejection_reasons[str(reason)] += 1

            guardrail = record.get("guardrail", {})
            guardrail_statuses[self.normalized_text(guardrail.get("status"), default="UNKNOWN").upper()] += 1
            for reason in guardrail.get("reasons", []) or []:
                guardrail_reasons[str(reason)] += 1
            for warning in guardrail.get("warnings", []) or []:
                guardrail_reasons[str(warning)] += 1

        return {
            "records": total,
            "symbol_frequency": dict(symbols),
            "state_frequency": dict(states),
            "confidence_band_frequency": dict(confidence_bands),
            "killzone_frequency": dict(killzones),
            "narrative_phase_frequency": dict(narrative_phases),
            "repeated_rejection_reasons": self.common_values(rejection_reasons),
            "guardrail_reasons": self.common_values(guardrail_reasons),
            "guardrail_statuses": dict(guardrail_statuses),
            "risk_warnings": self.common_values(risk_warnings),
            "news_lock_count": news_locks,
            "news_lock_rate": self.rate(news_locks, total),
            "execution_ready_count": execution_ready,
            "execution_ready_rate": self.rate(execution_ready, total),
            "execution_allowed_count": execution_allowed,
            "execution_allowed_rate": self.rate(execution_allowed, total),
            "risk_block_count": risk_blocked,
            "risk_block_rate": self.rate(risk_blocked, total),
        }

    def analyze_backtest(self, summary: dict[str, Any]) -> dict[str, Any]:
        """Return performance analytics from a backtest diagnostics summary."""
        overall = summary.get("after_guardrails") or summary.get("overall") or {}
        by_symbol = summary.get("by_symbol", {})
        by_killzone = summary.get("by_killzone", {})
        by_confidence = summary.get("by_confidence_band", {})
        by_narrative = summary.get("by_narrative_phase", summary.get("by_narrative", {}))
        diagnostics = summary.get("diagnostics", {})

        best_symbol = self.bucket_from_diagnostics(diagnostics, "best_symbol") or self.best_bucket(by_symbol)
        worst_symbol = self.bucket_from_diagnostics(diagnostics, "worst_symbol") or self.worst_bucket(by_symbol)
        best_killzone = self.bucket_from_diagnostics(diagnostics, "best_killzone") or self.best_bucket(by_killzone)
        worst_killzone = self.bucket_from_diagnostics(diagnostics, "worst_killzone") or self.worst_bucket(by_killzone)
        best_confidence = self.best_bucket(by_confidence)
        worst_confidence = self.worst_bucket(by_confidence)
        best_narrative = self.best_bucket(by_narrative)
        worst_narrative = (
            self.bucket_from_diagnostics(diagnostics, "worst_narrative_phase")
            or self.worst_bucket(by_narrative)
        )
        guardrail_impact = summary.get("guardrail_impact", {})

        return {
            "available": bool(summary),
            "overall": overall,
            "best_symbol": best_symbol,
            "worst_symbol": worst_symbol,
            "best_killzone": best_killzone,
            "worst_killzone": worst_killzone,
            "best_confidence_band": best_confidence,
            "worst_confidence_band": worst_confidence,
            "best_narrative_phase": best_narrative,
            "worst_narrative_phase": worst_narrative,
            "guardrail_impact": guardrail_impact,
            "repeated_rejection_reasons": self.extract_loss_cluster(diagnostics),
        }

    def build_strengths(self, journal: dict[str, Any], backtest: dict[str, Any]) -> list[dict[str, str]]:
        """Build positive coach observations."""
        strengths: list[dict[str, str]] = []
        best_symbol = backtest.get("best_symbol", {})
        best_killzone = backtest.get("best_killzone", {})
        overall = backtest.get("overall", {})

        if best_symbol.get("name") not in (None, "", "none"):
            strengths.append(
                self.item(
                    f"{best_symbol['name']} is the strongest symbol bucket ({self.metric_summary(best_symbol.get('metrics', {}))}).",
                    category="symbol",
                )
            )
        if best_killzone.get("name") not in (None, "", "none"):
            strengths.append(
                self.item(
                    f"{self.display_killzone(best_killzone['name'])} is the strongest session bucket ({self.metric_summary(best_killzone.get('metrics', {}))}).",
                    category="session",
                )
            )
        if float(overall.get("profit_factor", 0.0) or 0.0) >= 1.5:
            strengths.append(self.item("Backtest profit factor remains above the Phase 3 research threshold.", category="guardrail"))
        if journal.get("execution_ready_count", 0) > 0:
            strengths.append(
                self.item(
                    f"Journal shows {journal['execution_ready_count']} execution-ready scan(s), so the pipeline is producing actionable states.",
                    category="execution",
                )
            )
        return strengths or [self.item("Coach has enough structure to monitor performance, but needs more completed records for stronger conclusions.", category="psychology")]

    def build_weaknesses(self, journal: dict[str, Any], backtest: dict[str, Any]) -> list[dict[str, str]]:
        """Build coach weakness observations."""
        weaknesses: list[dict[str, str]] = []
        worst_symbol = backtest.get("worst_symbol", {})
        worst_killzone = backtest.get("worst_killzone", {})
        worst_confidence = backtest.get("worst_confidence_band", {})
        worst_narrative = backtest.get("worst_narrative_phase", {})

        if self.is_weak_bucket(worst_symbol):
            weaknesses.append(
                self.item(
                    f"{worst_symbol['name']} is the weakest symbol bucket ({self.metric_summary(worst_symbol.get('metrics', {}))}).",
                    severity="WARNING",
                    category="symbol",
                )
            )
        if self.is_weak_bucket(worst_killzone):
            weaknesses.append(
                self.item(
                    f"{self.display_killzone(worst_killzone['name'])} is the weakest session bucket ({self.metric_summary(worst_killzone.get('metrics', {}))}).",
                    severity="WARNING",
                    category="session",
                )
            )
        if self.is_weak_bucket(worst_confidence):
            weaknesses.append(
                self.item(
                    f"{worst_confidence['name']} confidence has weak historical performance ({self.metric_summary(worst_confidence.get('metrics', {}))}).",
                    severity="WARNING",
                    category="confidence",
                )
            )
        if self.is_weak_bucket(worst_narrative):
            weaknesses.append(
                self.item(
                    f"{str(worst_narrative['name']).replace('_', ' ').title()} narrative phase is underperforming.",
                    severity="WARNING",
                    category="psychology",
                )
            )
        for reason in journal.get("repeated_rejection_reasons", [])[:2]:
            weaknesses.append(
                self.item(
                    f"Repeated rejection reason: {reason['name']} ({reason['count']} time(s)).",
                    severity="WARNING",
                    category="execution",
                )
            )
        return weaknesses

    def build_recommendations(self, journal: dict[str, Any], backtest: dict[str, Any]) -> list[dict[str, str]]:
        """Build categorized coaching recommendations."""
        recommendations: list[dict[str, str]] = []
        best_symbol = backtest.get("best_symbol", {})
        worst_symbol = backtest.get("worst_symbol", {})
        best_killzone = backtest.get("best_killzone", {})
        worst_killzone = backtest.get("worst_killzone", {})
        worst_narrative = backtest.get("worst_narrative_phase", {})
        guardrail = backtest.get("guardrail_impact", {})

        if self.has_named_bucket(best_symbol) and self.has_named_bucket(worst_symbol) and best_symbol["name"] != worst_symbol["name"]:
            recommendations.append(
                self.item(
                    f"Favor {best_symbol['name']} over {worst_symbol['name']} until {worst_symbol['name']} performance improves.",
                    category="symbol",
                )
            )
        if self.has_named_bucket(best_killzone):
            recommendations.append(
                self.item(
                    f"{self.display_killzone(best_killzone['name'])} has strongest historical performance.",
                    category="session",
                )
            )
        if self.has_named_bucket(worst_killzone) and "continuation" in str(worst_killzone["name"]):
            recommendations.append(
                self.item(
                    f"Avoid {self.display_killzone(worst_killzone['name'])} unless SMT aligns and confidence remains high.",
                    severity="WARNING",
                    category="session",
                )
            )
        if self.has_named_bucket(worst_narrative) and str(worst_narrative["name"]) in {"range", "distribution"}:
            recommendations.append(
                self.item(
                    f"Treat {str(worst_narrative['name']).replace('_', ' ')} phase as caution-first; require cleaner confirmation.",
                    severity="WARNING",
                    category="psychology",
                )
            )
        if guardrail:
            recommendations.append(self.guardrail_recommendation(guardrail))
        else:
            recommendations.append(self.item("Keep adaptive guardrails enabled while Phase 3 research continues.", category="guardrail"))

        if journal.get("execution_ready_rate", 0.0) < float(self.config.get("execution_ready_target_rate", 0.10)):
            recommendations.append(
                self.item(
                    "Review rejected setups before changing thresholds; low readiness may reflect correct selectivity.",
                    category="confidence",
                )
            )
        recommendations.append(
            self.item(
                "Do not enable autonomous execution yet; keep Advisor and Assisted safety controls active.",
                severity="CRITICAL",
                category="execution",
            )
        )
        return self.dedupe_items(recommendations)

    def build_risk_notes(self, journal: dict[str, Any], backtest: dict[str, Any]) -> list[dict[str, str]]:
        """Build risk-focused notes."""
        notes: list[dict[str, str]] = []
        overall = backtest.get("overall", {})
        drawdown = float(overall.get("max_drawdown", 0.0) or 0.0)
        if drawdown >= float(self.config.get("critical_drawdown_percent", 6.0)):
            notes.append(self.item(f"Max drawdown is {drawdown}%, above the Phase 3 safety threshold.", severity="CRITICAL", category="risk"))
        elif drawdown > 0:
            notes.append(self.item(f"Max drawdown is controlled at {drawdown}%.", category="risk"))

        if journal.get("news_lock_rate", 0.0) >= float(self.config.get("news_lock_warning_rate", 0.25)):
            notes.append(
                self.item(
                    f"News locks appeared in {journal['news_lock_count']} of {journal['records']} journal record(s).",
                    severity="WARNING",
                    category="risk",
                )
            )
        if journal.get("risk_block_rate", 0.0) >= float(self.config.get("risk_block_warning_rate", 0.15)):
            notes.append(
                self.item(
                    f"Risk Governor blocks appeared in {journal['risk_block_count']} of {journal['records']} journal record(s).",
                    severity="WARNING",
                    category="risk",
                )
            )
        for warning in journal.get("risk_warnings", [])[:2]:
            notes.append(self.item(f"Risk warning repeated: {warning['name']}.", severity="WARNING", category="risk"))
        return notes or [self.item("No critical risk warning detected in the current coach inputs.", category="risk")]

    def build_next_actions(self, journal: dict[str, Any], backtest: dict[str, Any]) -> list[dict[str, str]]:
        """Build immediate next actions for the trader/operator."""
        actions = [
            self.item("Review the weakest symbol and session buckets before the next live scan.", category="psychology"),
            self.item("Keep journaling every scan so coach recommendations have enough sample size.", category="execution"),
        ]
        if not backtest.get("available"):
            actions.insert(0, self.item("Attach or run a fresh backtest diagnostics summary for performance-grade coaching.", category="guardrail"))
        if journal.get("guardrail_reasons"):
            actions.append(self.item("Audit repeated guardrail reasons before relaxing any filters.", category="guardrail"))
        return actions

    def build_summary(
        self,
        journal: dict[str, Any],
        backtest: dict[str, Any],
        recommendations: list[dict[str, str]],
    ) -> str:
        """Return the short command-center coach line."""
        best_symbol = backtest.get("best_symbol", {}).get("name")
        best_killzone = backtest.get("best_killzone", {}).get("name")
        pieces = []
        if best_symbol and best_symbol != "none":
            pieces.append(f"Favor {best_symbol}")
        if best_killzone and best_killzone != "none":
            pieces.append(self.display_killzone(best_killzone))
        if not pieces and journal.get("records", 0):
            pieces.append(f"{journal['records']} journal records reviewed")
        if not pieces:
            pieces.append("Collect more journal and backtest data")
        execution_warning = next((item["message"] for item in recommendations if item.get("category") == "execution" and item.get("severity") == "CRITICAL"), "")
        if execution_warning:
            pieces.append("Autonomous execution not recommended")
        return "Coach: " + " / ".join(pieces) + "."

    def empty_report(self, summary: str, *, severity: str = "INFO") -> dict[str, Any]:
        """Return a report when the coach cannot analyze."""
        return {
            "coach_status": "READY",
            "summary": summary,
            "strengths": [],
            "weaknesses": [],
            "recommendations": [self.item(summary, severity=severity, category="execution")],
            "risk_notes": [],
            "next_actions": [],
        }

    def guardrail_recommendation(self, impact: dict[str, Any]) -> dict[str, str]:
        """Return a recommendation from guardrail impact metrics."""
        before = impact.get("before", {})
        after = impact.get("after", {})
        before_pf = float(before.get("profit_factor", 0.0) or 0.0)
        after_pf = float(after.get("profit_factor", 0.0) or 0.0)
        removed = int(impact.get("trades_removed", 0) or 0)
        if removed > 0 and after_pf >= before_pf:
            return self.item("Keep adaptive guardrails enabled; filtered trades improved or preserved profit factor.", category="guardrail")
        if removed > 0:
            return self.item("Review adaptive guardrail impact before adding stricter hard blocks.", severity="WARNING", category="guardrail")
        return self.item("Keep adaptive guardrails enabled and monitor whether they remove weak conditions.", category="guardrail")

    def bucket_from_diagnostics(self, diagnostics: dict[str, Any], key: str) -> dict[str, Any]:
        """Return a normalized bucket from diagnostic best/worst fields."""
        value = diagnostics.get(key, {})
        if not isinstance(value, dict):
            return {}
        name = value.get("name")
        metrics = value.get("metrics", {})
        if not name:
            return {}
        return {"name": name, "metrics": metrics or {}}

    def best_bucket(self, buckets: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Return the strongest performance bucket."""
        eligible = self.eligible_buckets(buckets)
        if not eligible:
            return {}
        name, metrics = max(eligible, key=lambda item: self.bucket_score(item[1]))
        return {"name": name, "metrics": metrics}

    def worst_bucket(self, buckets: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Return the weakest performance bucket."""
        eligible = self.eligible_buckets(buckets)
        if not eligible:
            return {}
        name, metrics = min(eligible, key=lambda item: self.bucket_score(item[1]))
        return {"name": name, "metrics": metrics}

    def eligible_buckets(self, buckets: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
        """Return buckets with enough trades for coaching."""
        minimum = int(self.config.get("minimum_bucket_trades", 1))
        return [
            (str(name), metrics)
            for name, metrics in buckets.items()
            if isinstance(metrics, dict) and int(metrics.get("trades_approved", metrics.get("trades", 0)) or 0) >= minimum
        ]

    @staticmethod
    def bucket_score(metrics: dict[str, Any]) -> tuple[float, float, float, int]:
        """Return a sortable bucket score."""
        return (
            float(metrics.get("win_rate", metrics.get("winrate", 0.0)) or 0.0),
            float(metrics.get("profit_factor", 0.0) or 0.0),
            float(metrics.get("average_rr", metrics.get("avg_rr", 0.0)) or 0.0),
            int(metrics.get("trades_approved", metrics.get("trades", 0)) or 0),
        )

    def is_weak_bucket(self, bucket: dict[str, Any]) -> bool:
        """Return whether a bucket deserves a warning."""
        if not self.has_named_bucket(bucket):
            return False
        metrics = bucket.get("metrics", {})
        return float(metrics.get("win_rate", metrics.get("winrate", 0.0)) or 0.0) < float(self.config.get("low_win_rate_warning", 45.0))

    @staticmethod
    def has_named_bucket(bucket: dict[str, Any]) -> bool:
        """Return whether a normalized bucket has a meaningful name."""
        return bool(bucket and bucket.get("name") not in (None, "", "none"))

    @staticmethod
    def metric_summary(metrics: dict[str, Any]) -> str:
        """Return compact metric text."""
        if not metrics:
            return "no metrics"
        return (
            f"PF {metrics.get('profit_factor', 0.0)}, "
            f"WR {metrics.get('win_rate', metrics.get('winrate', 0.0))}%, "
            f"trades {metrics.get('trades_approved', metrics.get('trades', 0))}"
        )

    @staticmethod
    def common_values(counter: Counter) -> list[dict[str, Any]]:
        """Return common counter values as serializable dicts."""
        return [{"name": str(name), "count": int(count)} for name, count in counter.most_common() if name]

    @staticmethod
    def rate(count: int, total: int) -> float:
        """Return a rounded frequency rate."""
        return round(count / total, 4) if total else 0.0

    @staticmethod
    def normalized_text(value: Any, *, default: str = "none") -> str:
        """Return a normalized string for analytics."""
        text = str(value or "").strip()
        return text if text else default

    @classmethod
    def extract_killzone(cls, record: dict[str, Any]) -> str:
        """Extract killzone name from journal/backtest-shaped records."""
        if record.get("killzone") and isinstance(record["killzone"], str):
            return record["killzone"]
        killzone = record.get("killzone", {})
        return cls.normalized_text(killzone.get("active_killzone") if isinstance(killzone, dict) else None)

    @classmethod
    def extract_narrative_phase(cls, record: dict[str, Any]) -> str:
        """Extract narrative phase from flexible record shapes."""
        for path in (
            ("narrative_phase",),
            ("narrative", "phase"),
            ("narrative", "market_phase"),
            ("trade_plan", "narrative_phase"),
            ("trade_plan", "engine_stack", "narrative_phase"),
        ):
            value: Any = record
            for key in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(key)
            if value:
                return cls.normalized_text(value).lower()
        return "none"

    @staticmethod
    def extract_loss_cluster(diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
        """Return repeated loss-cluster traits if present."""
        cluster = diagnostics.get("losing_trade_analysis", {}).get("most_common", {})
        if not isinstance(cluster, dict):
            return []
        return [{"name": str(key), "value": value} for key, value in cluster.items() if value not in (None, "", "none")]

    @classmethod
    def item(cls, message: str, *, severity: str = "INFO", category: str = "psychology") -> dict[str, str]:
        """Build a normalized coach item."""
        normalized_severity = severity.upper().strip()
        normalized_category = category.lower().strip()
        return {
            "severity": normalized_severity if normalized_severity in cls.SEVERITIES else "INFO",
            "category": normalized_category if normalized_category in cls.CATEGORIES else "psychology",
            "message": message,
        }

    @staticmethod
    def dedupe_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
        """Preserve-order de-duplication by message/category/severity."""
        seen = set()
        deduped = []
        for item in items:
            key = (item.get("severity"), item.get("category"), item.get("message"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def display_killzone(name: str) -> str:
        """Return readable killzone text."""
        if not name or name == "none":
            return "None"
        return KillzoneAnalyzer.display_name(str(name))

    @classmethod
    def sanitize_record(cls, value: Any) -> Any:
        """Remove sensitive keys recursively before analysis or output."""
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                if str(key).lower() in cls.SENSITIVE_KEYS:
                    continue
                sanitized[key] = cls.sanitize_record(item)
            return sanitized
        if isinstance(value, list):
            return [cls.sanitize_record(item) for item in value]
        return value

    @classmethod
    def synthetic_journal_records(cls) -> list[dict[str, Any]]:
        """Return safe sample records for empty local journals and smoke tests."""
        return [
            {
                "symbol": "XAUUSD",
                "state": "EXECUTION_READY",
                "decision": "APPROVED",
                "confidence": 96,
                "killzone": {"active_killzone": "new_york_open"},
                "news": {"lock_active": False, "status": "CLEAR"},
                "risk": {"status": "OK", "warnings": [], "block_reasons": []},
                "guardrail": {"status": "PASS", "reasons": [], "warnings": []},
                "trade_plan": {"execution_allowed": True},
            },
            {
                "symbol": "GBPUSD",
                "state": "HOT",
                "decision": "REJECTED",
                "confidence": 91,
                "killzone": {"active_killzone": "london_continuation"},
                "news": {"lock_active": False, "status": "CLEAR"},
                "risk": {"status": "OK", "warnings": [], "block_reasons": []},
                "guardrail": {
                    "status": "BLOCKED",
                    "reasons": ["GBPUSD disabled by strategy guardrail"],
                    "warnings": ["Forex without SMT penalty"],
                },
                "trade_plan": {"execution_allowed": False},
                "rejection_reasons": ["Adjusted confidence below execution threshold"],
            },
            {
                "symbol": "US30",
                "state": "WARM",
                "decision": "WAIT",
                "confidence": 68,
                "killzone": {"active_killzone": "new_york_open"},
                "news": {"lock_active": True, "status": "LOCKED"},
                "risk": {"status": "OK", "warnings": [], "block_reasons": []},
                "guardrail": {"status": "PASS", "reasons": [], "warnings": []},
                "trade_plan": {"execution_allowed": False},
                "rejection_reasons": ["High impact news lock active"],
            },
        ]

    @property
    def enabled(self) -> bool:
        """Return whether the AI Coach is enabled."""
        return bool(self.config.get("enabled", True))

    @property
    def mode(self) -> str:
        """Return normalized coach mode."""
        return str(self.config.get("mode", "advisor")).lower().strip()

    @property
    def fallback_to_synthetic(self) -> bool:
        """Return whether empty journal fallback is enabled."""
        return bool(self.config.get("fallback_to_synthetic", True))

    @property
    def journal_path(self) -> Path:
        """Return resolved journal path."""
        configured_path = Path(str(self.config.get("journal_path", self.DEFAULT_CONFIG["journal_path"])))
        if configured_path.is_absolute():
            return configured_path
        return self.project_root / configured_path

    def _load_config(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "ai_coach.yaml")
        return self._deep_merge(self.DEFAULT_CONFIG, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using AI Coach defaults.", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise AICoachError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

