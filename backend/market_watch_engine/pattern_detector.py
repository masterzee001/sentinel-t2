"""Market pattern detection for Market Watch."""

from __future__ import annotations

from typing import Any


class PatternDetector:
    """Detect the dominant market pattern from candles and engine context."""

    PATTERNS = {
        "trend_continuation",
        "liquidity_sweep_reversal",
        "range_mean_reversion",
        "compression_breakout",
        "exhaustion_reversal",
        "noisy_chop",
        "no_clear_pattern",
    }

    def detect(
        self,
        *,
        symbol: str,
        candles: Any | None = None,
        trend: dict[str, Any] | None = None,
        liquidity: dict[str, Any] | None = None,
        ict: dict[str, Any] | None = None,
        narrative: dict[str, Any] | None = None,
        session: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return pattern scores and dominant pattern label."""
        context = context or {}
        trend = trend or context.get("trend", {})
        liquidity = liquidity or context.get("liquidity", {})
        ict = ict or context.get("ict", {})
        narrative = narrative or context.get("narrative", {})
        session = session or context.get("session", {})

        candle_stats = self.candle_stats(candles)
        trend_strength = self.value(context, "trend_strength", self.trend_strength(trend, candle_stats))
        range_score = self.value(context, "range_score", self.range_score(narrative, candle_stats))
        compression_score = self.value(context, "compression_score", self.compression_score(candle_stats))
        sweep_detected = bool(context.get("sweep_detected", liquidity.get("latest_sweep") or liquidity.get("sweep_detected", False)))
        exhaustion_score = self.value(context, "exhaustion_score", self.exhaustion_score(candle_stats, narrative))
        volatility_expansion = self.value(context, "volatility_expansion", self.volatility_expansion(candle_stats))
        overextension_score = self.value(context, "overextension_score", self.overextension_score(candle_stats, trend_strength))
        noise_score = self.value(context, "noise_score", self.noise_score(candle_stats, range_score))
        mss_detected = bool(context.get("mss_confirmed", ict.get("mss", {}).get("detected", False)))

        dominant = self.dominant_pattern(
            trend_strength=trend_strength,
            range_score=range_score,
            compression_score=compression_score,
            sweep_detected=sweep_detected,
            exhaustion_score=exhaustion_score,
            volatility_expansion=volatility_expansion,
            overextension_score=overextension_score,
            noise_score=noise_score,
            mss_detected=mss_detected,
        )
        return {
            "symbol": str(symbol).upper().strip(),
            "trend_strength": trend_strength,
            "range_score": range_score,
            "compression_score": compression_score,
            "sweep_detected": sweep_detected,
            "mss_confirmed": mss_detected,
            "exhaustion_score": exhaustion_score,
            "volatility_expansion": volatility_expansion,
            "overextension_score": overextension_score,
            "noise_score": noise_score,
            "session_quality": int(session.get("session_quality", context.get("session_quality", 0)) or 0),
            "smt_sample": int(context.get("smt_sample", 0) or 0),
            "dominant_pattern": dominant,
        }

    @classmethod
    def dominant_pattern(
        cls,
        *,
        trend_strength: int,
        range_score: int,
        compression_score: int,
        sweep_detected: bool,
        exhaustion_score: int,
        volatility_expansion: int,
        overextension_score: int,
        noise_score: int,
        mss_detected: bool,
    ) -> str:
        """Classify the dominant market pattern."""
        if noise_score >= 70:
            return "noisy_chop"
        if sweep_detected and (mss_detected or exhaustion_score >= 50):
            return "liquidity_sweep_reversal"
        if compression_score >= 65 and volatility_expansion >= 50:
            return "compression_breakout"
        if trend_strength >= 70 and range_score < 55:
            return "trend_continuation"
        if range_score >= 65 and overextension_score >= 45:
            return "range_mean_reversion"
        if exhaustion_score >= 70:
            return "exhaustion_reversal"
        return "no_clear_pattern"

    @staticmethod
    def value(context: dict[str, Any], key: str, fallback: int) -> int:
        try:
            return max(0, min(100, int(context.get(key, fallback) or 0)))
        except (TypeError, ValueError):
            return int(fallback)

    @staticmethod
    def candle_stats(candles: Any | None) -> dict[str, float]:
        """Return simple candle statistics from pandas-like or list data."""
        if candles is None:
            return {}
        try:
            if hasattr(candles, "empty") and candles.empty:
                return {}
            highs = [float(value) for value in candles["high"].tolist()]
            lows = [float(value) for value in candles["low"].tolist()]
            closes = [float(value) for value in candles["close"].tolist()]
        except Exception:
            try:
                rows = list(candles)
                highs = [float(row.get("high", 0.0)) for row in rows]
                lows = [float(row.get("low", 0.0)) for row in rows]
                closes = [float(row.get("close", 0.0)) for row in rows]
            except Exception:
                return {}
        if len(closes) < 2 or not highs or not lows:
            return {}
        ranges = [max(high - low, 0.0) for high, low in zip(highs, lows)]
        average_range = sum(ranges) / len(ranges) if ranges else 0.0
        recent_ranges = ranges[-5:] if len(ranges) >= 5 else ranges
        recent_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 0.0
        net_move = abs(closes[-1] - closes[0])
        full_range = max(highs) - min(lows)
        return {
            "average_range": average_range,
            "recent_range": recent_range,
            "net_move": net_move,
            "full_range": full_range,
            "close": closes[-1],
            "start": closes[0],
            "high": max(highs),
            "low": min(lows),
        }

    @staticmethod
    def trend_strength(trend: dict[str, Any], stats: dict[str, float]) -> int:
        if "trend_strength" in trend:
            return int(trend.get("trend_strength") or 0)
        if not stats:
            return 50
        ratio = stats.get("net_move", 0.0) / max(stats.get("full_range", 1.0), 1e-9)
        return int(max(0, min(100, ratio * 100)))

    @staticmethod
    def range_score(narrative: dict[str, Any], stats: dict[str, float]) -> int:
        phase = str(narrative.get("market_phase", narrative.get("phase", ""))).lower()
        if "range" in phase:
            return 80
        if not stats:
            return 30
        compression = 1 - min(stats.get("net_move", 0.0) / max(stats.get("full_range", 1.0), 1e-9), 1)
        return int(max(0, min(100, compression * 80)))

    @staticmethod
    def compression_score(stats: dict[str, float]) -> int:
        if not stats:
            return 30
        ratio = stats.get("recent_range", 0.0) / max(stats.get("average_range", 1.0), 1e-9)
        return int(max(0, min(100, (1 - min(ratio, 1)) * 100)))

    @staticmethod
    def exhaustion_score(stats: dict[str, float], narrative: dict[str, Any]) -> int:
        if bool(narrative.get("exhaustion")):
            return 80
        if not stats:
            return 35
        distance = abs(stats.get("close", 0.0) - ((stats.get("high", 0.0) + stats.get("low", 0.0)) / 2))
        return int(max(0, min(100, distance / max(stats.get("full_range", 1.0), 1e-9) * 100)))

    @staticmethod
    def volatility_expansion(stats: dict[str, float]) -> int:
        if not stats:
            return 40
        ratio = stats.get("recent_range", 0.0) / max(stats.get("average_range", 1.0), 1e-9)
        return int(max(0, min(100, ratio * 75)))

    @staticmethod
    def overextension_score(stats: dict[str, float], trend_strength: int) -> int:
        if not stats:
            return min(70, trend_strength)
        position = abs(stats.get("close", 0.0) - ((stats.get("high", 0.0) + stats.get("low", 0.0)) / 2))
        return int(max(0, min(100, (position / max(stats.get("full_range", 1.0), 1e-9) * 80) + trend_strength * 0.2)))

    @staticmethod
    def noise_score(stats: dict[str, float], range_score: int) -> int:
        if not stats:
            return 25
        if range_score > 70 and stats.get("net_move", 0.0) < stats.get("average_range", 0.0):
            return 75
        return max(0, min(100, 100 - int(stats.get("net_move", 0.0) / max(stats.get("full_range", 1.0), 1e-9) * 100)))
