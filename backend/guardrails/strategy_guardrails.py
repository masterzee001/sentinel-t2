"""Post-diagnostic strategy guardrails for Project Sentinel Advisor Mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from backend.shared.confidence_band_registry import ENGINE_ACTIONS, guardrail_adjusted_band


class StrategyGuardrailsError(RuntimeError):
    """Raised when strategy guardrails cannot be loaded."""


class StrategyGuardrails:
    """Apply non-scoring veto rules after Sentinel confidence analysis."""

    DEFAULT_CONFIG = {
        "enabled": True,
        "symbol_expansion": {
            "observer_only": True,
            "affect_production": False,
        },
        "disabled_trade_symbols": ["GBPUSD"],
        "blocked_killzones": ["london_continuation"],
        "symbol_execution_tiers": {
            "production": ["US30"],
            "filtered_production": ["XAUUSD"],
            "observer_only": ["EURUSD", "GBPUSD", "BTCUSD", "NAS100"],
        },
        "minimum_execution_confidence": 95,
        "minimum_execution_confidence_by_symbol_type": {
            "priority": 90,
            "forex": 95,
        },
        "adaptive_penalties": {
            "london_open": 12,
            "london_continuation": 100,
            "range_phase": 8,
            "no_smt_confirmation": 5,
            "no_smt_expansion": 5,
            "forex_without_smt": 7,
            "distribution_without_smt": 5,
        },
        "adaptive_bonuses": {
            "new_york_continuation": 5,
            "smt_confirmation": 3,
        },
        "robustness_365d": {
            "enabled": True,
            "eurusd_disabled_reason": "EURUSD observer mode: 365D PF below threshold",
            "xauusd_require_smt_or_confidence": 95,
            "xauusd_smt_min_sample": 10,
            "xauusd_smt_sample_trades": 0,
            "xauusd_block_london_open_without_smt": True,
            "xauusd_block_london_continuation": True,
            "us30_allowed_killzones": ["new_york_open", "new_york_continuation"],
            "us30_preferred_killzone": "new_york_continuation",
            "block_london_continuation": True,
            "london_open_requires_smt": True,
            "no_smt_expansion_penalty": True,
            "warn_no_smt_loss_cluster": True,
        },
        "forex_rules": {
            "require_smt_confirmation": True,
            "allowed_symbols": ["EURUSD"],
            "disabled_symbols": ["GBPUSD"],
        },
        "narrative_rules": {
            "block_range_phase": True,
            "caution_distribution_without_smt": True,
        },
    }
    ACTIONS = ENGINE_ACTIONS

    def __init__(self, config_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.config = self._deep_merge(self.DEFAULT_CONFIG, self._load_yaml_file(self.config_dir / "strategy_guardrails.yaml"))

    def evaluate(
        self,
        *,
        symbol: str,
        total_confidence: int | float,
        killzone: dict[str, Any] | None = None,
        smt: dict[str, Any] | None = None,
        narrative_phase: str | None = None,
        risk_blocked: bool = False,
        news_lock_active: bool = False,
        mss_confirmed: bool = True,
        rr_to_final: float | None = None,
        mode: str = "adaptive",
    ) -> dict[str, Any]:
        """Return guardrail status using adaptive scoring by default."""
        if mode == "hard":
            return self.evaluate_hard(
                symbol=symbol,
                total_confidence=total_confidence,
                killzone=killzone,
                smt=smt,
                narrative_phase=narrative_phase,
            )
        if mode not in {"adaptive", "hard"}:
            raise ValueError("Guardrail mode must be 'adaptive' or 'hard'.")
        return self.evaluate_adaptive(
            symbol=symbol,
            total_confidence=total_confidence,
            killzone=killzone,
            smt=smt,
            narrative_phase=narrative_phase,
            risk_blocked=risk_blocked,
            news_lock_active=news_lock_active,
            mss_confirmed=mss_confirmed,
            rr_to_final=rr_to_final,
        )

    def evaluate_hard(
        self,
        *,
        symbol: str,
        total_confidence: int | float,
        killzone: dict[str, Any] | None = None,
        smt: dict[str, Any] | None = None,
        narrative_phase: str | None = None,
    ) -> dict[str, Any]:
        """Return guardrail status, reasons, warnings, and display metadata."""
        normalized_symbol = symbol.upper().strip()
        total = int(total_confidence)
        adjusted_band, adjusted_action = self.adjust_confidence_band(total)
        if not self.enabled:
            return self.result(
                status="PASS",
                reasons=[],
                warnings=[],
                adjusted_confidence_band=adjusted_band,
                adjusted_recommended_action=adjusted_action,
                watchlist_only=False,
                enabled=False,
                original_confidence=total,
                adjusted_confidence=total,
                penalty_total=0,
            )

        killzone = killzone or {}
        smt = smt or {}
        active_killzone = str(killzone.get("active_killzone", "none"))
        phase = str(narrative_phase or "unknown").lower().strip()
        smt_detected = bool(smt.get("smt_detected", False))
        reasons: list[str] = []
        warnings: list[str] = []

        reasons.extend(self.symbol_execution_reasons(normalized_symbol))

        if active_killzone in self.blocked_killzones:
            reasons.append("London continuation blocked by strategy guardrail")

        reasons.extend(
            self.robustness_hard_reasons(
                symbol=normalized_symbol,
                active_killzone=active_killzone,
                phase=phase,
                total=total,
                smt_detected=smt_detected,
            )
        )

        if total < self.minimum_execution_confidence:
            reasons.append("Confidence below guarded execution threshold")

        if self.requires_smt(normalized_symbol) and not smt_detected:
            reasons.append("Forex setup requires SMT confirmation")

        if self.block_range_phase and phase == "range":
            reasons.append("Range phase blocked by strategy guardrail")

        if self.caution_distribution_without_smt and phase == "distribution" and not smt_detected:
            warnings.append("Distribution phase without SMT confirmation")

        reasons = self.dedupe(reasons)
        warnings = self.dedupe(warnings)
        status = "BLOCKED" if reasons else "PASS"
        return self.result(
            status=status,
            reasons=reasons,
            warnings=warnings,
            adjusted_confidence_band=adjusted_band,
            adjusted_recommended_action=adjusted_action,
            watchlist_only=bool(self.symbol_execution_reasons(normalized_symbol)),
            enabled=True,
            original_confidence=total,
            adjusted_confidence=total,
            penalty_total=0,
        )

    def evaluate_adaptive(
        self,
        *,
        symbol: str,
        total_confidence: int | float,
        killzone: dict[str, Any] | None = None,
        smt: dict[str, Any] | None = None,
        narrative_phase: str | None = None,
        risk_blocked: bool = False,
        news_lock_active: bool = False,
        mss_confirmed: bool = True,
        rr_to_final: float | None = None,
    ) -> dict[str, Any]:
        """Return adaptive guardrail status with soft penalties and hard vetoes."""
        normalized_symbol = symbol.upper().strip()
        total = int(total_confidence)
        if not self.enabled:
            adjusted_band, adjusted_action = self.adjust_confidence_band(total)
            return self.result(
                status="PASS",
                reasons=[],
                warnings=[],
                adjusted_confidence_band=adjusted_band,
                adjusted_recommended_action=adjusted_action,
                watchlist_only=False,
                enabled=False,
                original_confidence=total,
                adjusted_confidence=total,
                penalty_total=0,
            )

        killzone = killzone or {}
        smt = smt or {}
        active_killzone = str(killzone.get("active_killzone", "none"))
        phase = str(narrative_phase or "unknown").lower().strip()
        smt_detected = bool(smt.get("smt_detected", False))
        smt_available = bool(smt.get("available", True))
        smt_missing = not smt_detected
        reasons = self.hard_block_reasons(
            symbol=normalized_symbol,
            killzone=killzone,
            total=total,
            smt_detected=smt_detected,
            narrative_phase=phase,
            risk_blocked=risk_blocked,
            news_lock_active=news_lock_active,
            mss_confirmed=mss_confirmed,
            rr_to_final=rr_to_final,
        )
        warnings: list[str] = []
        penalties: list[dict[str, Any]] = []
        bonuses: list[dict[str, Any]] = []

        if active_killzone in self.blocked_killzones:
            penalties.append(self.penalty("London continuation penalty", self.adaptive_penalty("london_continuation")))

        if active_killzone == "london_open" and smt_detected:
            penalties.append(self.penalty("London Open robustness penalty", self.adaptive_penalty("london_open")))

        if phase == "range":
            penalties.append(self.penalty("Range phase penalty", self.adaptive_penalty("range_phase")))

        if normalized_symbol == "XAUUSD" and self.xau_smt_warning_only:
            if smt_detected and active_killzone not in self.blocked_killzones:
                bonuses.append(self.bonus("XAUUSD SMT confirmation bonus", self.adaptive_bonus("smt_confirmation")))
            else:
                warnings.append("XAUUSD SMT warning only until sample >= 10")

        if smt_available and not smt_detected:
            if normalized_symbol == "XAUUSD" and self.xau_smt_warning_only:
                warnings.append("XAUUSD missing SMT alignment")
            elif self.is_forex_symbol(normalized_symbol):
                penalties.append(self.penalty("Forex without SMT penalty", self.adaptive_penalty("forex_without_smt")))
            else:
                penalties.append(self.penalty("No SMT confirmation penalty", self.adaptive_penalty("no_smt_confirmation")))

        if smt_available and phase == "distribution" and not smt_detected:
            penalties.append(self.penalty("Distribution without SMT penalty", self.adaptive_penalty("distribution_without_smt")))

        if self.robustness_enabled and smt_missing and phase == "expansion" and not (normalized_symbol == "XAUUSD" and self.xau_smt_warning_only):
            penalties.append(self.penalty("No SMT expansion robustness penalty", self.adaptive_penalty("no_smt_expansion")))

        if self.robustness_enabled and self.no_smt_loss_cluster_warning(normalized_symbol, active_killzone, smt_missing):
            warnings.append("No SMT loss-cluster warning")

        if self.robustness_enabled and normalized_symbol == "US30" and active_killzone == self.us30_preferred_killzone:
            bonuses.append(self.bonus("NY Continuation robustness bonus", self.adaptive_bonus("new_york_continuation")))

        if 90 <= total <= 94:
            warnings.append("Confidence 90-94 requires caution")

        penalty_total = sum(int(item["value"]) for item in penalties)
        bonus_total = sum(int(item["value"]) for item in bonuses)
        adjusted_confidence = max(0, min(100, total + bonus_total - penalty_total))
        adjusted_band, adjusted_action = self.adjust_confidence_band(adjusted_confidence)
        minimum = self.minimum_execution_confidence_for_symbol(normalized_symbol)
        if adjusted_confidence < minimum:
            reasons.append("Adjusted confidence below execution threshold")

        reasons = self.dedupe(reasons)
        warning_values = warnings + [str(item["reason"]) for item in penalties]
        warning_values = self.dedupe(warning_values)
        status = "BLOCKED" if reasons else "PASS"
        return self.result(
            status=status,
            reasons=reasons,
            warnings=warning_values,
            adjusted_confidence_band=adjusted_band,
            adjusted_recommended_action=adjusted_action,
            watchlist_only=bool(self.symbol_execution_reasons(normalized_symbol)),
            enabled=True,
            original_confidence=total,
            adjusted_confidence=adjusted_confidence,
            penalty_total=penalty_total,
            penalties=penalties,
            bonuses=bonuses,
            bonus_total=bonus_total,
            minimum_execution_confidence=minimum,
        )

    def hard_block_reasons(
        self,
        *,
        symbol: str,
        killzone: dict[str, Any],
        risk_blocked: bool,
        news_lock_active: bool,
        mss_confirmed: bool,
        rr_to_final: float | None,
        total: int | float = 0,
        smt_detected: bool = False,
        narrative_phase: str | None = None,
    ) -> list[str]:
        """Return adaptive hard-veto reasons."""
        reasons: list[str] = []
        active_killzone = str(killzone.get("active_killzone", "none"))
        phase = str(narrative_phase or "unknown").lower().strip()
        if risk_blocked:
            reasons.append("Risk Governor blocked")
        if news_lock_active:
            reasons.append("High impact news lock active")
        if killzone and not bool(killzone.get("is_valid", True)):
            reasons.append("Outside valid killzone")
        if active_killzone in self.blocked_killzones:
            reasons.append("London continuation blocked by strategy guardrail")
        if not mss_confirmed:
            reasons.append("MSS not confirmed")
        if rr_to_final is not None and float(rr_to_final) < 3.0:
            reasons.append("RR below 3")
        reasons.extend(self.symbol_execution_reasons(symbol))
        reasons.extend(
            self.robustness_hard_reasons(
                symbol=symbol,
                active_killzone=active_killzone,
                phase=phase,
                total=int(total),
                smt_detected=smt_detected,
            )
        )
        return reasons

    def result(
        self,
        *,
        status: str,
        reasons: list[str],
        warnings: list[str],
        adjusted_confidence_band: str,
        adjusted_recommended_action: str,
        watchlist_only: bool,
        enabled: bool,
        original_confidence: int,
        adjusted_confidence: int,
        penalty_total: int,
        penalties: list[dict[str, Any]] | None = None,
        bonuses: list[dict[str, Any]] | None = None,
        bonus_total: int = 0,
        minimum_execution_confidence: int | None = None,
    ) -> dict[str, Any]:
        """Return the canonical guardrail payload."""
        return {
            "status": status,
            "reasons": reasons,
            "warnings": warnings,
            "enabled": enabled,
            "execution_allowed": status == "PASS",
            "minimum_execution_confidence": minimum_execution_confidence or self.minimum_execution_confidence,
            "original_confidence": original_confidence,
            "guardrail_penalty_total": penalty_total,
            "guardrail_bonus_total": bonus_total,
            "guardrail_adjusted_confidence": adjusted_confidence,
            "guardrail_warnings": warnings,
            "penalties": penalties or [],
            "bonuses": bonuses or [],
            "adjusted_confidence_band": adjusted_confidence_band,
            "adjusted_recommended_action": adjusted_recommended_action,
            "watchlist_only": watchlist_only,
        }

    def adjust_confidence_band(self, total_confidence: int | float) -> tuple[str, str]:
        """Return confidence band after guarded execution thresholds."""
        band = guardrail_adjusted_band(total_confidence)
        return band.band, band.engine_action

    def requires_smt(self, symbol: str) -> bool:
        """Return whether a symbol requires SMT confirmation."""
        if not bool(self.config.get("forex_rules", {}).get("require_smt_confirmation", False)):
            return False
        normalized_symbol = symbol.upper().strip()
        return normalized_symbol in self.forex_allowed_symbols or normalized_symbol in self.forex_disabled_symbols

    def minimum_execution_confidence_for_symbol(self, symbol: str) -> int:
        """Return adaptive execution threshold for priority and forex symbols."""
        thresholds = self.config.get("minimum_execution_confidence_by_symbol_type", {})
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol in self.filtered_production_symbols:
            if normalized_symbol == "XAUUSD" and self.xau_smt_warning_only:
                return int(thresholds.get("priority", 90))
            return int(self.config.get("robustness_365d", {}).get("xauusd_require_smt_or_confidence", 95))
        key = "forex" if self.is_forex_symbol(symbol) else "priority"
        return int(thresholds.get(key, 95 if key == "forex" else 90))

    def adaptive_penalty(self, key: str) -> int:
        """Return configured adaptive penalty for one guardrail condition."""
        return int(self.config.get("adaptive_penalties", {}).get(key, 0))

    def adaptive_bonus(self, key: str) -> int:
        """Return configured adaptive bonus for one robust condition."""
        return int(self.config.get("adaptive_bonuses", {}).get(key, 0))

    def is_forex_symbol(self, symbol: str) -> bool:
        """Return whether symbol should use forex guardrail treatment."""
        normalized_symbol = symbol.upper().strip()
        return normalized_symbol in self.forex_allowed_symbols or normalized_symbol in self.forex_disabled_symbols

    @staticmethod
    def penalty(reason: str, value: int) -> dict[str, Any]:
        """Return a normalized penalty object."""
        return {"reason": reason, "value": int(value)}

    @staticmethod
    def bonus(reason: str, value: int) -> dict[str, Any]:
        """Return a normalized bonus object."""
        return {"reason": reason, "value": int(value)}

    def symbol_execution_reasons(self, symbol: str) -> list[str]:
        """Return symbol-tier hard block reasons."""
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol == "EURUSD" and normalized_symbol in self.observer_only_symbols:
            return [str(self.config.get("robustness_365d", {}).get("eurusd_disabled_reason", "EURUSD observer mode: 365D PF below threshold"))]
        if normalized_symbol in self.demo_sandbox_symbols:
            return [f"{normalized_symbol} demo sandbox: production execution disabled"]
        if normalized_symbol in self.observer_only_symbols or normalized_symbol in self.disabled_trade_symbols or normalized_symbol in self.forex_disabled_symbols:
            return [f"{normalized_symbol} disabled by strategy guardrail"]
        return []

    def robustness_hard_reasons(
        self,
        *,
        symbol: str,
        active_killzone: str,
        phase: str,
        total: int,
        smt_detected: bool,
    ) -> list[str]:
        """Return 365D robustness veto reasons."""
        if not self.robustness_enabled:
            return []
        reasons: list[str] = []
        if self.config.get("robustness_365d", {}).get("block_london_continuation", True) and active_killzone == "london_continuation":
            reasons.append("London continuation blocked by strategy guardrail")
        if (
            active_killzone == "london_open"
            and self.config.get("robustness_365d", {}).get("london_open_requires_smt", True)
            and not smt_detected
            and not (symbol == "XAUUSD" and self.xau_smt_warning_only)
        ):
            reasons.append("London Open requires SMT alignment")
        if symbol == "XAUUSD":
            threshold = int(self.config.get("robustness_365d", {}).get("xauusd_require_smt_or_confidence", 95))
            if active_killzone == "london_continuation" and self.config.get("robustness_365d", {}).get("xauusd_block_london_continuation", True):
                reasons.append("XAUUSD London Continuation blocked by 365D robustness guardrail")
            if (
                active_killzone == "london_open"
                and self.config.get("robustness_365d", {}).get("xauusd_block_london_open_without_smt", True)
                and not smt_detected
                and not self.xau_smt_warning_only
            ):
                reasons.append("XAUUSD London Open requires SMT alignment")
            if not smt_detected and total < threshold and not self.xau_smt_warning_only:
                reasons.append("XAUUSD requires SMT alignment or confidence >= 95")
        if symbol == "US30":
            allowed = {str(killzone).strip() for killzone in self.config.get("robustness_365d", {}).get("us30_allowed_killzones", [])}
            if allowed and active_killzone not in allowed:
                reasons.append("US30 London session blocked by 365D robustness guardrail")
        return self.dedupe(reasons)

    def no_smt_loss_cluster_warning(self, symbol: str, active_killzone: str, smt_missing: bool) -> bool:
        """Return whether to warn for known No SMT loss clusters."""
        if not smt_missing:
            return False
        known_clusters = {
            ("EURUSD", "london_open"),
            ("US30", "new_york_open"),
            ("US30", "new_york_continuation"),
            ("XAUUSD", "london_open"),
            ("XAUUSD", "new_york_open"),
        }
        return (symbol.upper().strip(), active_killzone) in known_clusters

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def robustness_enabled(self) -> bool:
        return bool(self.config.get("robustness_365d", {}).get("enabled", True))

    @property
    def xau_smt_warning_only(self) -> bool:
        robustness = self.config.get("robustness_365d", {})
        sample = int(robustness.get("xauusd_smt_sample_trades", 0) or 0)
        minimum = int(robustness.get("xauusd_smt_min_sample", 10) or 10)
        return sample < minimum

    @property
    def disabled_trade_symbols(self) -> frozenset[str]:
        return frozenset(str(symbol).upper().strip() for symbol in self.config.get("disabled_trade_symbols", []))

    @property
    def blocked_killzones(self) -> frozenset[str]:
        return frozenset(str(killzone).strip() for killzone in self.config.get("blocked_killzones", []))

    @property
    def minimum_execution_confidence(self) -> int:
        return int(self.config.get("minimum_execution_confidence", 95))

    @property
    def production_symbols(self) -> frozenset[str]:
        symbols = self.config.get("symbol_execution_tiers", {}).get("production", [])
        return frozenset(str(symbol).upper().strip() for symbol in symbols)

    @property
    def filtered_production_symbols(self) -> frozenset[str]:
        symbols = self.config.get("symbol_execution_tiers", {}).get("filtered_production", [])
        return frozenset(str(symbol).upper().strip() for symbol in symbols)

    @property
    def demo_sandbox_symbols(self) -> frozenset[str]:
        symbols = self.config.get("symbol_execution_tiers", {}).get("demo_sandbox", [])
        return frozenset(str(symbol).upper().strip() for symbol in symbols)

    @property
    def observer_only_symbols(self) -> frozenset[str]:
        symbols = self.config.get("symbol_execution_tiers", {}).get("observer_only", [])
        return frozenset(str(symbol).upper().strip() for symbol in symbols)

    @property
    def us30_preferred_killzone(self) -> str:
        return str(self.config.get("robustness_365d", {}).get("us30_preferred_killzone", "new_york_continuation"))

    @property
    def forex_allowed_symbols(self) -> frozenset[str]:
        symbols = self.config.get("forex_rules", {}).get("allowed_symbols", [])
        return frozenset(str(symbol).upper().strip() for symbol in symbols)

    @property
    def forex_disabled_symbols(self) -> frozenset[str]:
        symbols = self.config.get("forex_rules", {}).get("disabled_symbols", [])
        return frozenset(str(symbol).upper().strip() for symbol in symbols)

    @property
    def block_range_phase(self) -> bool:
        return bool(self.config.get("narrative_rules", {}).get("block_range_phase", True))

    @property
    def caution_distribution_without_smt(self) -> bool:
        return bool(self.config.get("narrative_rules", {}).get("caution_distribution_without_smt", True))

    @staticmethod
    def dedupe(values: list[str]) -> list[str]:
        """Preserve-order de-duplication."""
        deduped: list[str] = []
        for value in values:
            if value and value not in deduped:
                deduped.append(value)
        return deduped

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist; using strategy guardrail defaults.", path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise StrategyGuardrailsError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
