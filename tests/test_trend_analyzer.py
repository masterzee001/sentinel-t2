from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.trend_engine.trend_analyzer import TrendAnalyzer


def make_candles(highs, lows, closes=None) -> pd.DataFrame:
    if closes is None:
        closes = [(high + low) / 2 for high, low in zip(highs, lows)]
    opens = closes
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


def test_daily_bias_detects_higher_highs_and_higher_lows():
    candles = make_candles(
        highs=[10, 12, 11, 16, 12, 18, 14, 22, 16, 24, 17],
        lows=[7, 8, 6, 9, 7, 10, 8, 12, 9, 13, 10],
    )

    assert TrendAnalyzer.determine_daily_bias(candles) == "bullish"


def test_daily_bias_detects_lower_highs_and_lower_lows():
    candles = make_candles(
        highs=[24, 22, 23, 18, 21, 16, 19, 14, 17, 12, 15],
        lows=[15, 13, 14, 11, 13, 9, 11, 7, 9, 5, 8],
    )

    assert TrendAnalyzer.determine_daily_bias(candles) == "bearish"


def test_h4_bias_detects_upward_structure_break():
    highs = [100 + index for index in range(24)]
    lows = [high - 4 for high in highs]
    closes = [high - 2 for high in highs]
    closes[-1] = max(highs[:-1]) + 2
    candles = make_candles(highs, lows, closes)

    assert TrendAnalyzer.determine_h4_bias(candles) == "bullish"


def test_h1_context_detects_expansion():
    highs = [101.0] * 30
    lows = [100.0] * 30
    closes = [100.5] * 30
    highs[-1] = 104.0
    lows[-1] = 99.0
    closes[-1] = 103.0
    candles = make_candles(highs, lows, closes)

    assert TrendAnalyzer.determine_h1_context(candles) == "expansion"


def test_h1_context_detects_retracement():
    highs = [100 + index * 0.5 for index in range(30)]
    lows = [high - 1.0 for high in highs]
    closes = [low + 0.6 for low in lows]
    closes[-4] = 115.0
    closes[-3] = 114.5
    closes[-2] = 114.0
    closes[-1] = 113.5
    candles = make_candles(highs, lows, closes)

    assert TrendAnalyzer.determine_h1_context(candles) == "retracement"


def test_symbol_validation_uses_config(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "trading_rules.yaml").write_text(
        """
markets:
  allowed:
    - "XAUUSD"
""",
        encoding="utf-8",
    )
    (config_dir / "rule_weights.yaml").write_text(
        """
modes:
  balanced:
    daily_bias: 15
    h4_narrative: 20
    minimum_confidence: 90
""",
        encoding="utf-8",
    )
    analyzer = TrendAnalyzer(connector=object(), config_dir=config_dir)

    assert analyzer._validate_symbol("xauusd") == "XAUUSD"
    with pytest.raises(ValueError, match="Unsupported symbol"):
        analyzer._validate_symbol("US30")
