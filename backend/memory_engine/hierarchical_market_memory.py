"""Hierarchical Market Memory for advisory-only Sentinel intelligence."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any


class HierarchicalMarketMemory:
    """Build macro, session, trigger, experience, and regime memory layers."""

    def __init__(self, *, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]

    def build_memory(self, *, symbol: str = "XAUUSD", source: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return full advisory market memory."""
        source = source or {}
        macro = self.capture_macro_memory(symbol=symbol, source=source.get("macro", {}))
        session = self.capture_session_memory(symbol=symbol, source=source.get("session", {}))
        trigger = self.capture_trigger_memory(symbol=symbol, source=source.get("trigger", {}))
        experience = self.capture_experience_memory(source.get("experience", {}))
        regime = self.capture_regime_memory(source.get("regime", {}))
        confidence = self.advisory_confidence_scores(
            {
                "macro": macro,
                "session": session,
                "trigger": trigger,
                "experience": experience,
                "regime": regime,
            },
            m15_structure_exists=bool(source.get("m15_structure_exists", session.get("m15_structure_exists", True))),
        )
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "ADVISORY_ONLY",
            "symbol": symbol.upper().strip(),
            "macro_memory": macro,
            "session_memory": session,
            "trigger_memory": trigger,
            "experience_memory": experience,
            "regime_memory": regime,
            "confidence_integration": confidence,
            "production_baseline_preserved": True,
            "production_metrics_modified": False,
            "decision": "PASS",
        }

    def capture_macro_memory(self, *, symbol: str, source: dict[str, Any]) -> dict[str, Any]:
        """Capture W1/D1/H4 memory."""
        return {
            "status": "PASS",
            "symbol": symbol.upper().strip(),
            "timeframes": ["W1", "D1", "H4"],
            "previous_week_high": float(source.get("previous_week_high", 4050.0)),
            "previous_week_low": float(source.get("previous_week_low", 3925.0)),
            "previous_day_high": float(source.get("previous_day_high", 4028.0)),
            "previous_day_low": float(source.get("previous_day_low", 3988.0)),
            "major_swing_highs": source.get("major_swing_highs", [{"timeframe": "H4", "price": 4042.0, "label": "major_supply"}]),
            "major_swing_lows": source.get("major_swing_lows", [{"timeframe": "H4", "price": 3970.0, "label": "major_demand"}]),
            "htf_order_blocks": source.get("htf_order_blocks", [{"timeframe": "H4", "direction": "bullish", "low": 3988.0, "high": 4002.0}]),
            "htf_fvgs": source.get("htf_fvgs", [{"timeframe": "H4", "direction": "bullish", "low": 4004.0, "high": 4012.0}]),
            "premium_discount_zones": source.get(
                "premium_discount_zones",
                {"range_high": 4050.0, "range_low": 3925.0, "equilibrium": 3987.5, "current_zone": "premium"},
            ),
        }

    def capture_session_memory(self, *, symbol: str, source: dict[str, Any]) -> dict[str, Any]:
        """Capture H1/M15 session memory."""
        return {
            "status": "PASS",
            "symbol": symbol.upper().strip(),
            "timeframes": ["H1", "M15"],
            "asian_range": source.get("asian_range", {"high": 4010.0, "low": 3992.0, "mid": 4001.0}),
            "london_session": source.get("london_session", {"high": 4022.0, "low": 3994.0}),
            "ny_session": source.get("ny_session", {"high": 4034.0, "low": 4002.0}),
            "intraday_order_blocks": source.get("intraday_order_blocks", [{"timeframe": "M15", "direction": "bullish", "low": 4006.0, "high": 4011.0}]),
            "intraday_fvgs": source.get("intraday_fvgs", [{"timeframe": "M15", "direction": "bullish", "low": 4012.0, "high": 4017.0, "mitigated": False}]),
            "session_liquidity_sweeps": source.get("session_liquidity_sweeps", [{"session": "new_york", "side": "sell_side", "price": 3992.0, "strength": "strong"}]),
            "m15_structure_exists": bool(source.get("m15_structure_exists", True)),
        }

    def capture_trigger_memory(self, *, symbol: str, source: dict[str, Any]) -> dict[str, Any]:
        """Capture M5/M1 trigger memory."""
        return {
            "status": "PASS",
            "symbol": symbol.upper().strip(),
            "timeframes": ["M5", "M1"],
            "micro_sweep_events": source.get("micro_sweep_events", [{"timeframe": "M5", "side": "sell_side", "price": 4004.0, "confirmed": True}]),
            "m5_mss": source.get("m5_mss", {"detected": True, "direction": "bullish", "displacement_score": 82}),
            "m1_mss": source.get("m1_mss", {"detected": True, "direction": "bullish", "displacement_score": 76}),
            "displacement_candles": source.get("displacement_candles", [{"timeframe": "M5", "direction": "bullish", "score": 82}]),
            "trigger_fvg": source.get("trigger_fvg", {"timeframe": "M1", "detected": True, "direction": "bullish", "mitigated": False}),
            "mitigation_state": source.get("mitigation_state", {"state": "unmitigated", "valid": True}),
        }

    def capture_experience_memory(self, source: dict[str, Any]) -> dict[str, Any]:
        """Capture existing learning memories from reports."""
        shadow = source.get("shadow") or self.load_report("shadow_learning_memory.json")
        ai = source.get("ai_policy") or self.load_report("ai_policy_memory.json")
        efde = source.get("efde") or self.load_report("efde_learning_memory.json")
        loss = source.get("loss") or self.load_report("loss_memory_database.json")
        return {
            "status": "PASS",
            "sources": ["shadow_learning", "ai_policy", "efde", "loss_memory", "trade_journal_expectancy"],
            "shadow_learning_available": bool(shadow),
            "ai_policy_available": bool(ai),
            "efde_available": bool(efde),
            "loss_memory_available": bool(loss),
            "summary": {
                "production_baseline_preserved": bool(
                    shadow.get("production_baseline_preserved", True)
                    if isinstance(shadow, dict) else True
                ),
                "ai_autonomous_execution": bool(ai.get("autonomous_execution", False)) if isinstance(ai, dict) else False,
            },
        }

    @staticmethod
    def capture_regime_memory(source: dict[str, Any]) -> dict[str, Any]:
        """Capture regime memory."""
        return {
            "status": "PASS",
            "trend_regime": source.get("trend_regime", "healthy_continuation_trend"),
            "range_regime": source.get("range_regime", "inactive"),
            "expansion_regime": source.get("expansion_regime", "active"),
            "news_regime": source.get("news_regime", "clear"),
            "active_regime": source.get("active_regime", "expansion"),
        }

    @staticmethod
    def advisory_confidence_scores(memory: dict[str, Any], *, m15_structure_exists: bool) -> dict[str, Any]:
        """Return optional advisory memory scores without changing production confidence."""
        trigger = memory.get("trigger", {})
        macro = memory.get("macro", {})
        session = memory.get("session", {})
        regime = memory.get("regime", {})
        if not m15_structure_exists:
            return {
                "status": "BLOCKED_NO_M15_STRUCTURE",
                "m5_trigger_score": 0,
                "m1_precision_score": 0,
                "memory_alignment_score": 0,
                "production_score_impact": 0,
                "notes": ["M1/M5 cannot create a trade without M15 structure."],
            }
        m5_mss = trigger.get("m5_mss", {})
        m1_mss = trigger.get("m1_mss", {})
        trigger_fvg = trigger.get("trigger_fvg", {})
        m5_score = 10 if m5_mss.get("detected") else -5
        if float(m5_mss.get("displacement_score", 0) or 0) >= 75:
            m5_score += 5
        m1_score = 7 if m1_mss.get("detected") else -4
        if trigger_fvg.get("detected") and not trigger_fvg.get("mitigated"):
            m1_score += 3
        alignment = 0
        if macro.get("premium_discount_zones", {}).get("current_zone") in {"discount", "premium"}:
            alignment += 5
        if session.get("session_liquidity_sweeps"):
            alignment += 5
        if regime.get("active_regime") in {"trend", "expansion"}:
            alignment += 5
        return {
            "status": "ADVISORY_READY",
            "m5_trigger_score": max(min(m5_score, 15), -10),
            "m1_precision_score": max(min(m1_score, 10), -10),
            "memory_alignment_score": max(min(alignment, 15), -10),
            "production_score_impact": 0,
            "notes": ["Advisory only: optional memory scores are reported separately from production confidence."],
        }

    @staticmethod
    def score_stickiness(records: list[dict[str, Any]], *, unchanged_threshold: int = 3) -> dict[str, Any]:
        """Return score stickiness diagnostics for repeated scan outputs."""
        streaks: dict[str, dict[str, Any]] = {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(str(record.get("symbol", "UNKNOWN")).upper(), []).append(record)
        for symbol, rows in grouped.items():
            score_streak = 0
            feature_streak = 0
            sentinel = object()
            previous_score: Any = sentinel
            previous_features: Any = sentinel
            score_values = []
            stale_flags = []
            score_refresh_count = 0
            feature_refresh_count = 0
            for row in rows:
                score = row.get("score")
                features = json.dumps(row.get("features") or {}, sort_keys=True, default=str)
                score_values.append(float(score or 0.0))
                if previous_score is not sentinel and previous_score != score:
                    score_refresh_count += 1
                if previous_features is not sentinel and previous_features != features:
                    feature_refresh_count += 1
                score_streak = score_streak + 1 if score == previous_score else 1
                feature_streak = feature_streak + 1 if features == previous_features else 1
                previous_score = score
                previous_features = features
                stale_flags.append(score_streak > unchanged_threshold or feature_streak > unchanged_threshold)
            variance = round(max(score_values) - min(score_values), 2) if score_values else 0.0
            scan_count = len(rows)
            streaks[symbol] = {
                "scan_count": scan_count,
                "unchanged_score_streak": score_streak,
                "unchanged_feature_streak": feature_streak,
                "score_variance": variance,
                "score_refresh_count": score_refresh_count,
                "feature_refresh_count": feature_refresh_count,
                "score_refresh_frequency": round(score_refresh_count / max(scan_count - 1, 1), 2),
                "feature_refresh_frequency": round(feature_refresh_count / max(scan_count - 1, 1), 2),
                "stale_symbol_score_warning": any(stale_flags),
            }
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "ADVISORY_DIAGNOSTIC",
            "unchanged_threshold": unchanged_threshold,
            "symbols": streaks,
            "warning_count": sum(1 for row in streaks.values() if row["stale_symbol_score_warning"]),
            "decision": "PASS",
        }

    def sample_score_records(self) -> list[dict[str, Any]]:
        """Return deterministic scan records for report generation."""
        return [
            {"symbol": "XAUUSD", "score": 91, "features": {"mss": True, "fvg": True}},
            {"symbol": "XAUUSD", "score": 91, "features": {"mss": True, "fvg": True}},
            {"symbol": "XAUUSD", "score": 91, "features": {"mss": True, "fvg": True}},
            {"symbol": "XAUUSD", "score": 91, "features": {"mss": True, "fvg": True}},
            {"symbol": "US30", "score": 84, "features": {"mss": True, "fvg": False}},
            {"symbol": "US30", "score": 87, "features": {"mss": True, "fvg": True}},
        ]

    def load_report(self, name: str) -> dict[str, Any]:
        """Load a report from data/reports when available."""
        path = self.project_root / "data" / "reports" / name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}


def distribution(values: list[str]) -> dict[str, int]:
    """Return a stable string-count distribution."""
    return dict(Counter(values))


def average(values: list[float]) -> float:
    """Return rounded mean or zero."""
    return round(mean(values), 2) if values else 0.0
