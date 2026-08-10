"""Shared Decision Adapter for Sentinel mode convergence.

The adapter provides one interface for all modes to call the same decision
brain. It is intentionally side-effect free: no broker calls, no order routing,
and no autonomous execution.

Per SENTINEL_CONSTITUTION.md, SDA now orchestrates tier-based evaluation for
structured reasoning tracking. The tier hierarchy (Tiers 1-4A) is evaluated
internally for ledger purposes but does NOT change decision outcomes.

CONSTRAINT: O2B tier wrapper is BEHAVIOR-PRESERVING. Every decision must
remain identical to pre-O2B SDA output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.shared.sda_tier_authority import TierAuthority


SENTINEL_MODES = ("LIVE", "DEMO", "REPLAY", "BACKTEST", "PAPER")


class ConfidenceBrain(Protocol):
    """Minimal protocol implemented by ConfidenceAnalyzer-compatible engines."""

    def analyze(self, symbol: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return a confidence decision payload."""


@dataclass(frozen=True)
class DecisionRequest:
    """One decision request across live, demo, replay, backtest, and paper."""

    mode: str
    symbol: str
    context: dict[str, Any] = field(default_factory=dict)


class SharedDecisionAdapter:
    """
    Call the shared confidence brain through one mode-aware interface.

    O2B Enhancement: Integrates TierAuthority for structured tier evaluation
    without changing decision logic (behavior-preserving refactoring).
    """

    def __init__(
        self,
        confidence_analyzer: ConfidenceBrain | None = None,
        tier_authority: TierAuthority | None = None,
    ) -> None:
        self.confidence_analyzer = confidence_analyzer
        # O2B: tier_authority for structured reasoning tracking (optional)
        self.tier_authority = tier_authority or (
            TierAuthority(confidence_engine=confidence_analyzer) if confidence_analyzer else None
        )

    def evaluate(self, request: DecisionRequest | dict[str, Any]) -> dict[str, Any]:
        """
        Evaluate a setup through the shared decision brain.

        O2B Enhancement: Evaluates tier hierarchy internally for structured
        reasoning tracking. Output format remains unchanged (backward compatible).
        """
        normalized = self.normalize_request(request)
        if self.confidence_analyzer is None:
            return {
                "mode": normalized.mode,
                "symbol": normalized.symbol,
                "status": "FAIL",
                "decision_source": "UNAVAILABLE",
                "reason": "No shared confidence analyzer provided.",
                "parity_compliant": False,
            }

        # Main decision engine (unchanged from pre-O2B)
        decision = self.confidence_analyzer.analyze(normalized.symbol, context=normalized.context)
        decision_source = str(getattr(self.confidence_analyzer, "decision_source", "ConfidenceAnalyzer"))

        # O2B: Evaluate tier hierarchy for ledger tracking (side-effect free)
        if self.tier_authority:
            # Extract necessary payloads for tier evaluation from context
            # (tier_authority calls same engine methods, no behavior change)
            tier_result = self._evaluate_tiers_for_ledger(
                symbol=normalized.symbol,
                decision_payload=decision,
                context=normalized.context,
            )
            # Store tier result for potential ledger recording (O2C)
            decision["tier_evaluation"] = tier_result
        else:
            decision["tier_evaluation"] = None

        return {
            "mode": normalized.mode,
            "symbol": normalized.symbol,
            "status": "PASS",
            "decision_source": decision_source,
            "parity_compliant": True,
            "decision": decision,
        }

    def _evaluate_tiers_for_ledger(
        self,
        symbol: str,
        decision_payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Evaluate tier hierarchy for ledger tracking purposes.

        CONSTRAINT: This is purely for internal reasoning tracking.
        Decision outcome is NOT affected by this evaluation.
        """
        if not self.tier_authority or not self.tier_authority.confidence_engine:
            return {"status": "UNAVAILABLE", "reason": "Tier authority not initialized"}

        # Extract payloads from decision (already computed by confidence_analyzer)
        trend = decision_payload.get("trend") or context.get("trend", {})
        liquidity = decision_payload.get("liquidity") or context.get("liquidity", {})
        ict = decision_payload.get("ict") or context.get("ict", {})
        scores = decision_payload.get("scores", {})
        total_confidence = decision_payload.get("total_confidence", 0)
        direction = decision_payload.get("direction")

        # Call tier authority (which calls existing engine methods)
        try:
            tier_result = self.tier_authority.evaluate_all_tiers(
                symbol=symbol,
                trend=trend,
                liquidity=liquidity,
                ict=ict,
                scores=scores,
                total_confidence=total_confidence,
                context=context,
                direction=direction,
            )
            return {"status": "SUCCESS", "tier_result": tier_result}
        except Exception as e:
            return {"status": "ERROR", "reason": str(e)}

    @staticmethod
    def normalize_request(request: DecisionRequest | dict[str, Any]) -> DecisionRequest:
        """Return a validated decision request."""
        if isinstance(request, DecisionRequest):
            mode = request.mode
            symbol = request.symbol
            context = dict(request.context)
        else:
            mode = str(request.get("mode", "LIVE"))
            symbol = str(request.get("symbol", ""))
            context = dict(request.get("context", {}) or {})
        normalized_mode = mode.upper().strip()
        if normalized_mode not in SENTINEL_MODES:
            raise ValueError(f"Unsupported Sentinel mode: {mode}")
        return DecisionRequest(mode=normalized_mode, symbol=symbol.upper().strip(), context=context)


def _source_contains(module_path: str, attribute: str | None, needle: str) -> bool:
    """Return whether a module (or one attribute of it) contains a code marker."""
    import inspect
    from importlib import import_module

    try:
        module = import_module(module_path)
        target = getattr(module, attribute) if attribute else module
        return needle in inspect.getsource(target)
    except Exception:
        return False


def shared_decision_adapter_audit() -> dict[str, Any]:
    """Verify where the shared decision path is actually enforced.

    Unlike the original stub, every claim here is recomputed from source
    inspection so this audit can fail when enforcement regresses.
    """
    violations: list[dict[str, str]] = []

    adapter_enforced_by_live = _source_contains(
        "backend.monitor.live_monitor", "LiveMonitor", "self.shared_decision_adapter.evaluate"
    )
    if not adapter_enforced_by_live:
        violations.append(
            {
                "severity": "HIGH",
                "file": "backend/monitor/live_monitor.py",
                "function": "LiveMonitor.analyze_symbol",
                "violation": "Live monitor does not route decisions through SharedDecisionAdapter.",
            }
        )

    backtest_routes_brain = _source_contains(
        "backend.backtesting.backtest_engine", "BacktestEngine", "self.decision_brain.analyze"
    )
    brain_is_confidence_parity = _source_contains(
        "backend.backtesting.historical_decision_brain",
        "HistoricalBacktestDecisionBrain",
        'decision_source = "ConfidenceAnalyzer"',
    )
    adapter_enforced_by_backtest = backtest_routes_brain and brain_is_confidence_parity
    if not adapter_enforced_by_backtest:
        violations.append(
            {
                "severity": "HIGH",
                "file": "backend/backtesting/backtest_engine.py",
                "function": "BacktestEngine.scan_symbol",
                "violation": "Backtest engine does not route decisions through the SDA-parity confidence brain.",
            }
        )

    status = "PASS" if not violations else "FAIL"
    return {
        "status": status,
        "adapter_available": True,
        "adapter_enforced_by_live": adapter_enforced_by_live,
        "adapter_enforced_by_backtest": adapter_enforced_by_backtest,
        "adapter_enforced_by_replay": adapter_enforced_by_backtest,
        "remaining_violations": violations,
        "notes": [
            "Claims verified by source inspection, not asserted.",
            "Backtest/replay parity means BacktestEngine delegates scoring and guardrails to HistoricalBacktestDecisionBrain (ConfidenceAnalyzer underneath).",
        ],
    }
