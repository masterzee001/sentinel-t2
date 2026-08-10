"""Tier Authority: Organizational wrapper over existing decision logic.

Per SENTINEL_CONSTITUTION.md, the 7-tier decision hierarchy provides structure
and measurability to rejection reasoning. This module wraps existing engines
(ConfidenceAnalyzer, guardrails) into tier-organized methods WITHOUT changing
decision outcomes.

CONSTRAINT: This module calls existing engine methods. It does NOT add new logic,
change thresholds, or modify confidence scoring. All tiers use current logic paths.
"""

from __future__ import annotations

from typing import Any, Protocol
from backend.shared.reason_ledger import ReasonTier, ReasonAction, ReasonEntry, DecisionMode


class ConfidenceEngine(Protocol):
    """Protocol for ConfidenceAnalyzer-compatible engines."""

    def evaluate_hard_rejections(
        self,
        symbol: str,
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
        scores: dict[str, int],
        total_confidence: int,
        context: dict[str, Any],
        direction: str | None,
    ) -> list[str]:
        """Return hard rejection reasons."""

    def evaluate_guardrails(
        self,
        symbol: str,
        total_confidence: int,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return guardrail evaluation result."""

    def is_forex_symbol(self, symbol: str) -> bool:
        """Check if symbol is FX."""


# ============================================================================
# O2C: TIER 1 HARD SAFETY CENTRALIZATION
# ============================================================================

# ============================================================================
# O2D: TIER 2/3 CLASSIFICATION SCAFFOLDING
# ============================================================================

class Tier2MacroTruthChecker:
    """
    Tier 2A: Macro Truth Veto (Constitutional tier per Law 3).

    SPEC COMPLIANT: May veto only when genuinely toxic or contradiction-level.
    Examples:
    - HTF narrative contradiction
    - macro liquidity absence
    - toxic regime
    - asset-specific macro conflict
    """

    TIER_2A_SEVERITY = 0.8  # High severity but potentially defeatable with strong structure

    @staticmethod
    def classify_tier_2a_reasons(reasons: list[str]) -> tuple[list[str], dict[str, Any]]:
        """Extract Tier 2A macro truth vetoes from rejection list."""
        tier_2a_reasons = []
        tier_2a_metadata = {}

        # Per Constitution: Macro truth veto only for contradiction/toxicity level issues
        tier_2a_keywords = {
            "Forex daily bias not aligned": {
                "classification": "FX_DAILY_BIAS_CONTRADICTION",
                "severity": 0.8,
                "veto_capable": True,
            },
            "Forex 4H narrative not aligned": {
                "classification": "FX_4H_NARRATIVE_CONTRADICTION",
                "severity": 0.8,
                "veto_capable": True,
            },
            "HTF narrative opposite": {
                "classification": "HTF_CONTRADICTION",
                "severity": 0.8,
                "veto_capable": True,
            },
            "Macro liquidity absent": {
                "classification": "MACRO_LIQUIDITY_ABSENCE",
                "severity": 0.8,
                "veto_capable": True,
            },
        }

        for reason in reasons:
            for keyword, metadata in tier_2a_keywords.items():
                if keyword in reason:
                    tier_2a_reasons.append(reason)
                    tier_2a_metadata[reason] = metadata
                    break

        return tier_2a_reasons, tier_2a_metadata


class Tier2MacroConfidenceChecker:
    """
    Tier 2B: Macro Confidence Penalty (Constitutional tier per Law 3).

    SPEC COMPLIANT: Never direct veto. Always penalty/scaling only.
    Examples:
    - partial macro alignment
    - unclear liquidity target
    - mixed regime
    - session/killzone weakness when not hard-blocked
    """

    TIER_2B_SEVERITY = 0.3  # Penalty tier, never fatal

    @staticmethod
    def classify_tier_2b_reasons(reasons: list[str]) -> tuple[list[str], dict[str, Any]]:
        """Extract Tier 2B macro confidence penalties from rejection list."""
        tier_2b_reasons = []
        tier_2b_metadata = {}

        # Per Constitution: Macro confidence is penalty-only, never veto by itself
        tier_2b_keywords = {
            # Session/timeframe weakness (penalty, not hard block)
            "london_open": {
                "classification": "LONDON_SESSION_WEAK",
                "severity": 0.3,
                "action": "penalty",
            },
            "london_continuation": {
                "classification": "LONDON_CONTINUATION_WEAK",
                "severity": 0.3,
                "action": "penalty",
            },
            "range_phase": {
                "classification": "RANGE_PHASE_WEAK",
                "severity": 0.3,
                "action": "penalty",
            },
        }

        for reason in reasons:
            for keyword, metadata in tier_2b_keywords.items():
                if keyword in reason:
                    tier_2b_reasons.append(reason)
                    tier_2b_metadata[reason] = metadata
                    break

        return tier_2b_reasons, tier_2b_metadata


class Tier3StructuralValidityChecker:
    """
    Tier 3A: Structural Validity Veto (Constitutional tier per Law 3).

    SPEC COMPLIANT: May veto because macro bias without execution structure is not a trade.
    Examples:
    - MSS absent
    - no executable FVG/order-block structure
    - invalid stop structure
    - invalid setup geometry
    - impossible RR structure
    """

    TIER_3A_SEVERITY = 0.9  # High severity veto for missing structure

    @staticmethod
    def classify_tier_3a_reasons(reasons: list[str]) -> tuple[list[str], dict[str, Any]]:
        """Extract Tier 3A structural validity vetoes from rejection list."""
        tier_3a_reasons = []
        tier_3a_metadata = {}

        # Per Constitution: Structure is required for execution, never optional
        tier_3a_keywords = {
            "MSS not confirmed": {
                "classification": "MSS_ABSENT",
                "severity": 0.9,
                "veto_capable": True,
            },
            "FVG not detected": {
                "classification": "NO_EXECUTABLE_FVG",
                "severity": 0.9,
                "veto_capable": True,
            },
            "Premium/discount unavailable": {
                "classification": "INVALID_PREMIUM_DISCOUNT",
                "severity": 0.9,
                "veto_capable": True,
            },
            "FVG direction not aligned with MSS": {
                "classification": "FVG_MSS_MISALIGNMENT",
                "severity": 0.9,
                "veto_capable": True,
            },
            "Order block direction not aligned with MSS": {
                "classification": "OB_MSS_MISALIGNMENT",
                "severity": 0.9,
                "veto_capable": True,
            },
            "RR below 3": {
                "classification": "IMPOSSIBLE_RR_STRUCTURE",
                "severity": 0.9,
                "veto_capable": True,
            },
            "No directional setup": {
                "classification": "NO_DIRECTIONAL_STRUCTURE",
                "severity": 0.9,
                "veto_capable": True,
            },
            "Invalid stop structure": {
                "classification": "INVALID_STOP_STRUCTURE",
                "severity": 0.9,
                "veto_capable": True,
            },
        }

        for reason in reasons:
            for keyword, metadata in tier_3a_keywords.items():
                if keyword in reason:
                    tier_3a_reasons.append(reason)
                    tier_3a_metadata[reason] = metadata
                    break

        return tier_3a_reasons, tier_3a_metadata


class Tier3SetupQualityChecker:
    """
    Tier 3B: Setup Quality Scaling (Constitutional tier per Law 3).

    SPEC COMPLIANT: Never direct veto. Always scaling/penalty only.
    Important: Weak confluence must not veto by itself.
    Examples:
    - weak SMT
    - mediocre FVG grade
    - average order-block clarity
    - weak displacement quality
    """

    TIER_3B_SEVERITY = 0.2  # Low severity, scaling/penalty only

    @staticmethod
    def classify_tier_3b_reasons(reasons: list[str]) -> tuple[list[str], dict[str, Any]]:
        """Extract Tier 3B setup quality penalties from rejection list."""
        tier_3b_reasons = []
        tier_3b_metadata = {}

        # Per Constitution: Setup quality is scaling-only, never veto by itself
        tier_3b_keywords = {
            "Weak SMT": {
                "classification": "WEAK_SMT_CONFLUENCE",
                "severity": 0.2,
                "action": "scaling",
                "note": "Does not veto independently",
            },
            "FVG quality": {
                "classification": "MEDIOCRE_FVG_GRADE",
                "severity": 0.2,
                "action": "scaling",
                "note": "Does not veto independently",
            },
            "Weak FVG": {
                "classification": "WEAK_FVG_CLARITY",
                "severity": 0.2,
                "action": "scaling",
                "note": "Does not veto independently",
            },
        }

        for reason in reasons:
            for keyword, metadata in tier_3b_keywords.items():
                if keyword in reason:
                    tier_3b_reasons.append(reason)
                    tier_3b_metadata[reason] = metadata
                    break

        return tier_3b_reasons, tier_3b_metadata

class Tier1SafetyChecker:
    """
    Centralized Tier 1 hard safety checks per SENTINEL_CONSTITUTION.md.

    SPEC COMPLIANT: Tier 1 contains only true hard safety vetoes:
    - daily loss breach (fatal)
    - max drawdown breach (fatal)
    - kill switch (fatal)
    - broker lock (fatal)
    - news lock (fatal)
    - symbol lock (fatal)
    - severe spread/slippage breach (fatal)

    TEMPORARY BACKWARD COMPATIBILITY: Killzone is currently hard-blocked but
    will be softened to Tier 2B/3B in future phases. Preserved here for behavior
    consistency pending architectural change.
    """

    # Per SENTINEL_CONSTITUTION.md Law 6: Every veto must be severity-ranked.
    # Tier 1 = fatal veto = severity 1.0 (absolute vetoes)
    TIER_1_SEVERITY = 1.0

    @staticmethod
    def classify_tier_1_reasons(reasons: list[str]) -> tuple[list[str], dict[str, Any]]:
        """
        Extract Tier 1 hard safety reasons from rejection list.

        Returns:
            (tier_1_reasons, tier_1_metadata)
            tier_1_metadata includes severity and classification for each reason
        """
        tier_1_reasons = []
        tier_1_metadata = {}

        # SPEC COMPLIANT: True Tier 1 hard safety checks only
        tier_1_keywords = {
            "Daily loss limit hit": {
                "classification": "DAILY_LOSS_BREACH",
                "severity": 1.0,
                "permanent": True,
            },
            "Max trades per day hit": {
                "classification": "MAX_TRADES_EXCEEDED",
                "severity": 1.0,
                "permanent": True,
            },
            "High impact news lock active": {
                "classification": "NEWS_LOCK",
                "severity": 1.0,
                "permanent": True,
            },
            "Broker locked": {
                "classification": "BROKER_LOCK",
                "severity": 1.0,
                "permanent": True,
            },
            "Symbol locked": {
                "classification": "SYMBOL_LOCK",
                "severity": 1.0,
                "permanent": True,
            },
            # TEMPORARY BACKWARD COMPATIBILITY: Killzone is currently hard-blocked
            # but belongs in Tier 2B/3B architecturally. Preserved here for behavior
            # consistency pending refactor to soften killzone weak-ness to penalty-only.
            "Outside valid killzone": {
                "classification": "KILLZONE_INVALID",
                "severity": 1.0,
                "permanent": False,
                "note": "TEMPORARY BACKWARD COMPATIBILITY: Will be softened to Tier 2B/3B",
            },
        }

        for reason in reasons:
            for keyword, metadata in tier_1_keywords.items():
                if keyword in reason:
                    tier_1_reasons.append(reason)
                    tier_1_metadata[reason] = metadata
                    break

        return tier_1_reasons, tier_1_metadata

    @staticmethod
    def extract_tier_1_with_severity(
        tier_1_reasons: list[str],
        tier_1_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Convert Tier 1 reasons to structured entries with severity.

        Returns list of dicts with: reason, severity, classification, permanent_tier_1
        """
        entries = []
        for reason in tier_1_reasons:
            meta = tier_1_metadata.get(reason, {})
            entries.append({
                "reason": reason,
                "severity": meta.get("severity", 1.0),
                "classification": meta.get("classification", "UNKNOWN_TIER_1"),
                "permanent_tier_1": meta.get("permanent", True),
                "note": meta.get("note"),
            })
        return entries


class TierAuthority:
    """
    Organizational wrapper for SDA tier hierarchy.

    Maps current decision logic to constitutional tiers without changing behavior.
    """

    def __init__(self, confidence_engine: ConfidenceEngine | None = None) -> None:
        self.confidence_engine = confidence_engine
        self.reason_entries: list[ReasonEntry] = []

    def evaluate_all_tiers(
        self,
        symbol: str,
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
        scores: dict[str, int],
        total_confidence: int,
        context: dict[str, Any],
        direction: str | None,
        decision_mode: DecisionMode = DecisionMode.LIVE,
    ) -> dict[str, Any]:
        """
        Evaluate all tiers sequentially and return aggregated result.

        Returns:
            {
                "tier_1_pass": bool,
                "tier_1_reasons": list[str],
                "tier_1_severity_analysis": list[dict],  # O2C
                "tier_2a_pass": bool,
                "tier_2a_reasons": list[str],  # O2D
                "tier_2a_severity_analysis": list[dict],  # O2D
                "tier_2b_multiplier": float (1.0 = no penalty),
                "tier_2b_reasons": list[str],  # O2D
                "tier_2b_severity_analysis": list[dict],  # O2D
                "tier_3a_pass": bool,
                "tier_3a_reasons": list[str],  # O2D
                "tier_3a_severity_analysis": list[dict],  # O2D
                "tier_3b_multiplier": float (1.0 = no penalty),
                "tier_3b_reasons": list[str],  # O2D
                "tier_3b_severity_analysis": list[dict],  # O2D
                "tier_4a_pass": bool,
                "all_tier_reasons": list[str],  # Aggregated rejection reasons
                "tier_breakdown": dict,  # Per-tier reason breakdown
            }
        """
        if not self.confidence_engine:
            return {
                "tier_1_pass": False,
                "tier_1_reasons": [],
                "tier_1_severity_analysis": [],
                "tier_2a_pass": False,
                "tier_2a_reasons": [],
                "tier_2a_severity_analysis": [],
                "tier_2b_multiplier": 1.0,
                "tier_2b_reasons": [],
                "tier_2b_severity_analysis": [],
                "tier_3a_pass": False,
                "tier_3a_reasons": [],
                "tier_3a_severity_analysis": [],
                "tier_3b_multiplier": 1.0,
                "tier_3b_reasons": [],
                "tier_3b_severity_analysis": [],
                "tier_4a_pass": False,
                "all_tier_reasons": ["Confidence engine unavailable"],
                "tier_breakdown": {},
            }

        # Call existing hard rejection evaluation (this aggregates all current logic)
        all_reasons = self.confidence_engine.evaluate_hard_rejections(
            symbol=symbol,
            trend=trend,
            liquidity=liquidity,
            ict=ict,
            scores=scores,
            total_confidence=total_confidence,
            context=context,
            direction=direction,
        )

        # SPEC COMPLIANT: Classify reasons into tiers based on current logic flow
        tier_breakdown = self._classify_reasons_by_tier(
            all_reasons,
            symbol=symbol,
            context=context,
            direction=direction,
            trend=trend,
            liquidity=liquidity,
            ict=ict,
        )

        # O2C: Extract Tier 1 with severity analysis
        tier_1_reasons = tier_breakdown.get("tier_1", [])
        tier_1_structured, tier_1_metadata = Tier1SafetyChecker.classify_tier_1_reasons(tier_1_reasons)
        tier_1_severity_analysis = Tier1SafetyChecker.extract_tier_1_with_severity(
            tier_1_structured, tier_1_metadata
        )

        # O2D: Extract Tier 2A with severity analysis
        tier_2a_reasons_untyped = tier_breakdown.get("tier_2a", [])
        tier_2a_reasons, tier_2a_metadata = Tier2MacroTruthChecker.classify_tier_2a_reasons(tier_2a_reasons_untyped)
        tier_2a_severity_analysis = [
            {
                "reason": reason,
                "severity": tier_2a_metadata[reason].get("severity", 0.8),
                "classification": tier_2a_metadata[reason].get("classification", "UNKNOWN_MACRO_TRUTH"),
                "veto_capable": tier_2a_metadata[reason].get("veto_capable", True),
            }
            for reason in tier_2a_reasons
        ]

        # O2D: Extract Tier 2B with severity analysis
        tier_2b_reasons_untyped = tier_breakdown.get("tier_2b", [])
        tier_2b_reasons, tier_2b_metadata = Tier2MacroConfidenceChecker.classify_tier_2b_reasons(tier_2b_reasons_untyped)
        tier_2b_severity_analysis = [
            {
                "reason": reason,
                "severity": tier_2b_metadata[reason].get("severity", 0.3),
                "classification": tier_2b_metadata[reason].get("classification", "UNKNOWN_MACRO_CONFIDENCE"),
                "action": tier_2b_metadata[reason].get("action", "penalty"),
            }
            for reason in tier_2b_reasons
        ]

        # O2D: Extract Tier 3A with severity analysis
        tier_3a_reasons_untyped = tier_breakdown.get("tier_3a", [])
        tier_3a_reasons, tier_3a_metadata = Tier3StructuralValidityChecker.classify_tier_3a_reasons(tier_3a_reasons_untyped)
        tier_3a_severity_analysis = [
            {
                "reason": reason,
                "severity": tier_3a_metadata[reason].get("severity", 0.9),
                "classification": tier_3a_metadata[reason].get("classification", "UNKNOWN_STRUCTURE"),
                "veto_capable": tier_3a_metadata[reason].get("veto_capable", True),
            }
            for reason in tier_3a_reasons
        ]

        # O2D: Extract Tier 3B with severity analysis
        tier_3b_reasons_untyped = tier_breakdown.get("tier_3b", [])
        tier_3b_reasons, tier_3b_metadata = Tier3SetupQualityChecker.classify_tier_3b_reasons(tier_3b_reasons_untyped)
        tier_3b_severity_analysis = [
            {
                "reason": reason,
                "severity": tier_3b_metadata[reason].get("severity", 0.2),
                "classification": tier_3b_metadata[reason].get("classification", "UNKNOWN_QUALITY"),
                "action": tier_3b_metadata[reason].get("action", "scaling"),
                "note": tier_3b_metadata[reason].get("note"),
            }
            for reason in tier_3b_reasons
        ]

        # Determine pass/fail per tier
        tier_1_pass = len(tier_1_reasons) == 0
        tier_2a_pass = len(tier_2a_reasons) == 0
        tier_3a_pass = len(tier_3a_reasons) == 0

        # Tiers 2B, 3B are penalties only (never hard veto)
        tier_2b_multiplier = 1.0  # Will be computed in O2E
        tier_3b_multiplier = 1.0  # Will be computed in O2E

        # Tier 4A (portfolio) currently doesn't exist in source code, passes for now
        tier_4a_pass = True  # Placeholder - no portfolio logic yet

        return {
            "tier_1_pass": tier_1_pass,
            "tier_1_reasons": tier_1_reasons,
            "tier_1_severity_analysis": tier_1_severity_analysis,
            "tier_2a_pass": tier_2a_pass,
            "tier_2a_reasons": tier_2a_reasons,
            "tier_2a_severity_analysis": tier_2a_severity_analysis,
            "tier_2b_multiplier": tier_2b_multiplier,
            "tier_2b_reasons": tier_2b_reasons,
            "tier_2b_severity_analysis": tier_2b_severity_analysis,
            "tier_3a_pass": tier_3a_pass,
            "tier_3a_reasons": tier_3a_reasons,
            "tier_3a_severity_analysis": tier_3a_severity_analysis,
            "tier_3b_multiplier": tier_3b_multiplier,
            "tier_3b_reasons": tier_3b_reasons,
            "tier_3b_severity_analysis": tier_3b_severity_analysis,
            "tier_4a_pass": tier_4a_pass,
            "all_tier_reasons": all_reasons,
            "tier_breakdown": tier_breakdown,
        }

    def _classify_reasons_by_tier(
        self,
        reasons: list[str],
        symbol: str,
        context: dict[str, Any],
        direction: str | None,
        trend: dict[str, Any],
        liquidity: dict[str, Any],
        ict: dict[str, Any],
    ) -> dict[str, list[str]]:
        """
        Classify rejection reasons into tiers.

        SPEC COMPLIANT: This mapping is based on current logic structure.
        - Tier 1: Daily loss, news lock, broker lock, symbol lock (safety)
        - Tier 2A: HTF narrative, macro alignment (truth)
        - Tier 3A: MSS, FVG, stops (structure)
        - Tier 3B: SMT, FVG grade quality issues (setup quality)
        - Tier 4A: Portfolio-level (not yet in current code, returns empty)
        - Tier 2B/Other: Penalties and warnings (not hard veto)
        """
        tier_1 = []
        tier_2a = []
        tier_2b = []
        tier_3a = []
        tier_3b = []
        tier_4a = []
        other = []

        tier_1_keywords = {
            "Daily loss limit hit",
            "Max trades per day hit",
            "High impact news lock active",
            "Broker locked",
            "Symbol locked",
            # TEMPORARY BACKWARD COMPATIBILITY: Killzone is currently hard-blocked but will be softened to Tier 2B/3B in future phases
            "Outside valid killzone",
        }

        tier_2a_keywords = {
            "Forex daily bias not aligned",
            "Forex 4H narrative not aligned",
            "HTF narrative opposite",
            "Macro liquidity absent",
        }

        tier_3a_keywords = {
            "MSS not confirmed",
            "FVG not detected",
            "Premium/discount unavailable",
            "FVG direction not aligned with MSS",
            "Order block direction not aligned with MSS",
            "RR below 3",
            "No directional setup",
            "Invalid stop structure",
        }

        tier_3b_keywords = {
            "Weak SMT",
            "FVG quality",
            "Weak FVG",
        }

        for reason in reasons:
            if any(kw in reason for kw in tier_1_keywords):
                tier_1.append(reason)
            elif any(kw in reason for kw in tier_2a_keywords):
                tier_2a.append(reason)
            elif any(kw in reason for kw in tier_3a_keywords):
                tier_3a.append(reason)
            elif any(kw in reason for kw in tier_3b_keywords):
                tier_3b.append(reason)
            elif "PORTFOLIO" in reason.upper() or "CORRELATION" in reason.upper():
                tier_4a.append(reason)
            else:
                # Confidence threshold, forex-specific, other guardrails
                other.append(reason)

        return {
            "tier_1": tier_1,
            "tier_2a": tier_2a,
            "tier_2b": tier_2b,
            "tier_3a": tier_3a,
            "tier_3b": tier_3b,
            "tier_4a": tier_4a,
            "other": other,
        }

    def add_reason_entry(
        self,
        tier: ReasonTier,
        action: ReasonAction,
        reason_code: str,
        severity: float,
        message: str,
        confidence_before: int,
        confidence_after: int,
        risk_before: float,
        risk_after: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a reason entry to the ledger."""
        entry = ReasonEntry(
            tier=tier,
            action=action,
            reason_code=reason_code,
            severity=severity,
            message=message,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            risk_before=risk_before,
            risk_after=risk_after,
            metadata=metadata or {},
        )
        self.reason_entries.append(entry)

    def clear_entries(self) -> None:
        """Clear reason entries (for new decision cycle)."""
        self.reason_entries.clear()

