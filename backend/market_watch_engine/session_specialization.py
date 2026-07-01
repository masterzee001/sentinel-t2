"""Symbol-specific session intelligence for Market Watch."""

from __future__ import annotations

from typing import Any


class SessionSpecialization:
    """Score session quality without changing production guardrails."""

    DEFAULT_RULES = {
        "US30": {
            "new_york_open": (92, "preferred", "US30 performs best during NY windows"),
            "new_york_continuation": (90, "preferred", "US30 performs best during NY continuation"),
            "london_open": (35, "weak", "US30 London Open remains weak until validated"),
            "london_continuation": (0, "blocked", "US30 London Continuation is blocked"),
        },
        "XAUUSD": {
            "new_york_open": (88, "preferred", "XAUUSD prefers New York Open"),
            "new_york_continuation": (64, "caution", "XAUUSD New York Continuation requires caution"),
            "london_open": (45, "diagnostic_only", "XAUUSD London Open remains diagnostic only"),
            "london_continuation": (25, "avoid", "XAUUSD London Continuation is weak"),
        },
        "BTCUSD": {
            "asia": (58, "observer", "BTC observer evaluates Asia behavior"),
            "new_york_open": (55, "observer", "BTC observer evaluates NY overlap"),
            "new_york_continuation": (55, "observer", "BTC observer evaluates NY overlap"),
            "weekend": (50, "observer", "BTC observer evaluates weekend behavior"),
        },
        "NAS100": {
            "new_york_open": (84, "observer", "NAS100 observer evaluates NY Open"),
            "new_york_continuation": (82, "observer", "NAS100 observer evaluates NY Continuation"),
            "london_open": (30, "observer_weak", "NAS100 London behavior is observer-only"),
            "london_continuation": (0, "observer_blocked", "NAS100 London Continuation remains blocked"),
        },
        "EURUSD": {
            "london_open": (40, "observer_only", "EURUSD remains observer-only"),
            "new_york_open": (38, "observer_only", "EURUSD remains observer-only"),
        },
        "GBPUSD": {
            "london_open": (25, "disabled", "GBPUSD remains disabled from execution"),
            "new_york_open": (25, "disabled", "GBPUSD remains disabled from execution"),
        },
    }

    ALIASES = {
        "BTC": "BTCUSD",
        "USTEC": "NAS100",
        "USTECH100M": "NAS100",
        "NAS100.CASH": "NAS100",
    }

    def evaluate(self, symbol: str, session: str | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return symbol-specific session quality."""
        context = context or {}
        normalized_symbol = self.normalize_symbol(symbol)
        normalized_session = str(session or context.get("active_killzone") or context.get("session") or "none").lower().strip()
        rules = self.DEFAULT_RULES.get(normalized_symbol, {})
        quality, status, reason = rules.get(
            normalized_session,
            (50 if normalized_symbol not in {"EURUSD", "GBPUSD"} else 20, "neutral", f"{normalized_symbol} session has no validated edge"),
        )
        affects_production = False if normalized_symbol in {"BTCUSD", "NAS100", "EURUSD", "GBPUSD"} else False
        return {
            "symbol": normalized_symbol,
            "session": normalized_session,
            "session_quality": int(quality),
            "session_status": status,
            "reason": reason,
            "affects_production": affects_production,
        }

    @classmethod
    def normalize_symbol(cls, symbol: Any) -> str:
        value = str(symbol or "").upper().strip()
        return cls.ALIASES.get(value, value)
