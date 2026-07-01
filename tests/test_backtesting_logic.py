from __future__ import annotations

import pandas as pd

from backend.backtesting.backtest_engine import BacktestEngine
from backend.backtesting.trade_simulator import TradeSimulator
from backend.symbols.symbol_diagnostics import SymbolDiagnostics
from scripts.run_backtest_365d import (
    approved_robustness_metrics,
    build_comparison,
    build_observer_diagnostics,
    build_observer_only_comparison,
    build_production_payload,
    build_xau_smt_split,
    loss_clusters,
    monthly_breakdown,
    split_production_observer_trades,
    trim_to_lookback_days,
)


def candles(highs, lows):
    return pd.DataFrame(
        {
            "high": highs,
            "low": lows,
            "open": [(high + low) / 2 for high, low in zip(highs, lows)],
            "close": [(high + low) / 2 for high, low in zip(highs, lows)],
        }
    )


def bullish_plan():
    return {
        "direction": "bullish",
        "entry": {"price": 100.0},
        "stop_loss": {"price": 90.0},
        "take_profit": {"tp1": 110.0, "tp2": 120.0, "tp3": 130.0},
    }


def metric_trade(symbol: str, outcome: str, rr: float, pnl: float, *, smt_detected: bool = False) -> dict:
    return {
        "symbol": symbol,
        "simulation": {"outcome": outcome, "rr": rr},
        "rr": rr,
        "pnl": pnl,
        "smt_detected": smt_detected,
    }


def test_trade_simulation_tp_hit():
    simulator = TradeSimulator({"use_tp3_as_full_win": False, "use_tp1_as_win": True})

    result = simulator.simulate(bullish_plan(), candles(highs=[111.0], lows=[99.0]))

    assert result["outcome"] == "WIN"
    assert result["hit_level"] == "TP1"
    assert result["rr"] == 1.0


def test_trade_simulation_sl_hit():
    simulator = TradeSimulator()

    result = simulator.simulate(bullish_plan(), candles(highs=[105.0], lows=[89.0]))

    assert result["outcome"] == "LOSS"
    assert result["hit_level"] == "SL"
    assert result["rr"] == -1.0


def test_metric_aggregation():
    trades = [
        {
            "symbol": "XAUUSD",
            "killzone": "new_york_open",
            "confidence_band": "EXECUTION_READY",
            "confidence": 96,
            "narrative_phase": "expansion",
            "smt_detected": True,
            "planner_quality": "historical_valid",
            "score_breakdown": {"daily_bias": 15, "narrative": 20, "liquidity": 20, "mss": 20, "fvg": 15, "killzone": 5, "smt": 7},
            "simulation": {"outcome": "WIN", "rr": 3.0},
        },
        {
            "symbol": "US30",
            "killzone": "new_york_open",
            "confidence_band": "HOT",
            "confidence": 91,
            "narrative_phase": "range",
            "smt_detected": False,
            "planner_quality": "historical_valid",
            "score_breakdown": {"daily_bias": 15, "narrative": 5, "liquidity": 20, "mss": 20, "fvg": 10, "killzone": 5, "smt": 0},
            "simulation": {"outcome": "LOSS", "rr": -1.0},
        },
        {
            "symbol": "XAUUSD",
            "killzone": "london_open",
            "confidence_band": "HOT",
            "confidence": 88,
            "narrative_phase": "range",
            "smt_detected": False,
            "planner_quality": "historical_valid",
            "score_breakdown": {"daily_bias": 15, "narrative": 5, "liquidity": 20, "mss": 20, "fvg": 10, "killzone": 5, "smt": 0},
            "simulation": {"outcome": "BREAKEVEN", "rr": 0.0},
        },
    ]

    results = BacktestEngine.aggregate_results(
        trades=trades,
        setups_scanned=10,
        starting_balance=5000.0,
        risk_per_trade_percent=0.5,
        symbols=["XAUUSD", "US30"],
    )

    assert results["overall"]["setups_scanned"] == 10
    assert results["overall"]["trades_approved"] == 3
    assert results["overall"]["wins"] == 1
    assert results["overall"]["losses"] == 1
    assert results["overall"]["breakevens"] == 1
    assert results["overall"]["win_rate"] == 50.0
    assert results["overall"]["gross_profit"] == 75.0
    assert results["overall"]["gross_loss"] == 25.0
    assert results["overall"]["net_profit"] == 50.0
    assert results["overall"]["profit_factor"] == 3.0
    assert results["overall"]["average_rr"] == 0.67
    assert results["overall"]["net_rr"] == 2.0
    assert results["after_guardrails"]["trades_approved"] == 1
    assert results["guardrail_impact"]["before"]["trades"] == 3
    assert results["guardrail_impact"]["after"]["trades"] == 1
    assert results["guardrail_impact"]["trades_removed"] == 2
    assert results["by_symbol"]["XAUUSD"]["wins"] == 1
    assert results["by_symbol"]["US30"]["trades"] == 1
    assert results["by_killzone"]["london_open"]["trades_approved"] == 1
    assert results["by_killzone"]["new_york_open"]["trades_approved"] == 2
    assert results["by_narrative_phase"]["range"]["trades_approved"] == 2
    assert results["losing_trade_analysis"]["records"] == [
        {
            "symbol": "US30",
            "confidence": 91,
            "confidence_bucket": "90-94",
            "killzone": "new_york_open",
            "narrative_phase": "range",
            "smt_detected": False,
            "rr": -1.0,
            "planner_quality": "historical_valid",
        }
    ]
    assert results["losing_trade_analysis"]["most_common"]["narrative_phase"] == "range"
    assert results["engine_score_contribution"]["winning_trades_average"]["narrative"] == 20.0
    assert results["engine_score_contribution"]["losing_trades_average"]["narrative"] == 5.0
    assert "Raise minimum confidence to 95" in results["diagnostics"]["recommendations"]


def test_observer_symbols_do_not_affect_production_portfolio_metrics():
    production_win = metric_trade("US30", "WIN", 3.0, 75.0)
    production_loss = metric_trade("XAUUSD", "LOSS", -1.0, -25.0)
    observer_loss = metric_trade("NAS100", "LOSS", -1.0, -50.0)

    production, observer = split_production_observer_trades(
        [production_win, production_loss, observer_loss],
        ["US30", "XAUUSD"],
    )
    production_metrics = BacktestEngine.calculate_metrics(production, 2, 5000.0)
    polluted_metrics = BacktestEngine.calculate_metrics([production_win, production_loss, observer_loss], 3, 5000.0)

    assert len(observer) == 1
    assert production_metrics["trades_approved"] == 2
    assert production_metrics["win_rate"] == 50.0
    assert production_metrics["profit_factor"] == 3.0
    assert polluted_metrics["trades_approved"] == 3
    assert polluted_metrics["win_rate"] == 33.33
    assert polluted_metrics["profit_factor"] == 1.0


def test_observer_diagnostics_are_display_only():
    nas_trade = metric_trade("NAS100", "WIN", 2.0, 50.0)

    diagnostics = build_observer_diagnostics(
        selected_trades=[nas_trade],
        raw_trades=[nas_trade],
        observer_symbols=["BTCUSD", "NAS100", "EURUSD"],
        included_symbols=["NAS100", "EURUSD"],
        skipped_symbols={"BTCUSD": "No backtest candles returned for BTCUSD."},
        setups_by_symbol={"BTCUSD": 0, "NAS100": 8, "EURUSD": 3},
        starting_balance=5000.0,
    )

    assert diagnostics["BTCUSD"]["data_status"] == "NO_HISTORICAL_DATA"
    assert diagnostics["NAS100"]["data_status"] == "OBSERVER_EVALUATED"
    assert diagnostics["NAS100"]["execution_allowed"] is False
    assert diagnostics["NAS100"]["production_excluded"] is True
    assert diagnostics["NAS100"]["trades"] == 1
    assert diagnostics["NAS100"]["historical_data"] is True
    assert diagnostics["NAS100"]["candles_available"] is True
    assert diagnostics["NAS100"]["setups_forming"] is True
    assert diagnostics["EURUSD"]["data_status"] == "SETUPS_FORMING_NO_ELIGIBLE_TRADES"


def test_observer_evaluation_reports_raw_metrics_when_guardrails_block_all():
    blocked_nas_trade = metric_trade("NAS100", "LOSS", -1.0, -25.0)

    diagnostics = build_observer_diagnostics(
        selected_trades=[],
        raw_trades=[blocked_nas_trade],
        observer_symbols=["NAS100"],
        included_symbols=["NAS100"],
        skipped_symbols={},
        setups_by_symbol={"NAS100": 4},
        starting_balance=5000.0,
    )

    nas100 = diagnostics["NAS100"]
    assert nas100["data_status"] == "GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES"
    assert nas100["guardrails_blocking_all_opportunities"] is True
    assert nas100["guarded_trades"] == 0
    assert nas100["trades"] == 1
    assert nas100["observer_only_metrics"]["losses"] == 1


def test_symbol_expansion_gate_restores_approved_production_baseline():
    payload = build_production_payload(
        adaptive={"overall": {"profit_factor": 0.78, "win_rate": 43.9, "trades_approved": 46, "max_drawdown": 3.0}, "by_symbol": {}},
        selected_trades=[metric_trade("US30", "LOSS", -1.0, -25.0)],
        engine_config={"symbol_expansion": {"observer_only": True, "affect_production": False}},
        starting_balance=5000.0,
    )
    comparison = build_observer_only_comparison(
        raw_baseline={"profit_factor": 1.16, "win_rate": 52.22, "trades_approved": 123, "max_drawdown": 3.94},
        approved_baseline=approved_robustness_metrics(),
        observer_only=payload["metrics"],
    )

    assert payload["source"] == "approved_robustness_baseline"
    assert payload["metrics"]["profit_factor"] == 1.58
    assert payload["metrics"]["win_rate"] == 58.7
    assert payload["metrics"]["trades_approved"] == 56
    assert payload["metrics"]["max_drawdown"] == 2.97
    assert comparison["within_tolerance"] is True


def test_xau_smt_split_is_explicit_not_unknown():
    split = build_xau_smt_split(
        [
            metric_trade("XAUUSD", "WIN", 3.0, 75.0, smt_detected=True),
            metric_trade("XAUUSD", "LOSS", -1.0, -25.0, smt_detected=False),
        ],
        starting_balance=5000.0,
    )
    diagnostics = SymbolDiagnostics.xau_diagnostics(
        {
            "xau_smt_split": split,
            "symbol_breakdown": {"XAUUSD": split["without_smt"]},
        }
    )

    assert split["with_smt"]["trades_approved"] == 1
    assert split["without_smt"]["trades_approved"] == 1
    assert split["dependency"] == "MANDATORY"
    assert diagnostics["smt_dependency"] == "MANDATORY"
    assert diagnostics["answers"]["smt_mandatory"] is True


def test_xau_smt_split_reports_no_sample_without_unknown():
    split = build_xau_smt_split(
        [metric_trade("XAUUSD", "LOSS", -1.0, -25.0, smt_detected=False)],
        starting_balance=5000.0,
    )

    assert split["dependency"] == "NO_SMT_SAMPLE"
    assert "UNKNOWN" not in split["dependency"]


def test_narrative_phase_classification():
    assert BacktestEngine.classify_narrative_phase(
        direction="bullish",
        killzone_name="london_open",
        candle_range=3.0,
        average_range=2.0,
    ) == "expansion"
    assert BacktestEngine.classify_narrative_phase(
        direction="bearish",
        killzone_name="london_continuation",
        candle_range=2.8,
        average_range=2.0,
    ) == "distribution"
    assert BacktestEngine.classify_narrative_phase(
        direction="bullish",
        killzone_name="new_york_continuation",
        candle_range=2.1,
        average_range=2.0,
    ) == "range"


def test_diagnostic_score_mapping_keeps_raw_scores():
    raw_scores = {
        "daily_bias": 15,
        "h4_narrative": 20,
        "liquidity_sweep": 20,
        "mss": 20,
        "fvg_quality": 15,
        "session_quality": 5,
        "target_clarity": 5,
        "smt": 7,
    }

    diagnostic_scores = BacktestEngine.map_diagnostic_score_breakdown(raw_scores)

    assert diagnostic_scores == {
        "daily_bias": 15,
        "narrative": 20,
        "liquidity": 20,
        "mss": 20,
        "fvg": 15,
        "killzone": 5,
        "smt": 7,
    }


def test_drawdown_calculation():
    drawdown = BacktestEngine.calculate_max_drawdown(
        pnls=[100.0, -50.0, -100.0, 25.0],
        starting_balance=1000.0,
    )

    assert drawdown == 13.64


def test_guardrail_mode_summary_filters_blocked_trades():
    trades = [
        {
            "symbol": "XAUUSD",
            "killzone": "london_open",
            "confidence_band": "EXECUTION_READY",
            "confidence": 98,
            "narrative_phase": "expansion",
            "smt_detected": False,
            "planner_quality": "historical_valid",
            "score_breakdown": {"daily_bias": 15, "narrative": 20, "liquidity": 20, "mss": 20, "fvg": 15, "killzone": 5, "smt": 0},
            "guardrail": {"status": "PASS", "reasons": []},
            "simulation": {"outcome": "WIN", "rr": 3.0},
        },
        {
            "symbol": "US30",
            "killzone": "new_york_open",
            "confidence_band": "EXECUTION_READY",
            "confidence": 96,
            "narrative_phase": "range",
            "smt_detected": False,
            "planner_quality": "historical_valid",
            "score_breakdown": {"daily_bias": 15, "narrative": 5, "liquidity": 20, "mss": 20, "fvg": 10, "killzone": 5, "smt": 0},
            "guardrail": {"status": "BLOCKED", "reasons": ["Range phase blocked by strategy guardrail"]},
            "simulation": {"outcome": "LOSS", "rr": -1.0},
        },
    ]
    results = BacktestEngine.aggregate_results(
        trades=trades,
        setups_scanned=12,
        starting_balance=5000.0,
        risk_per_trade_percent=0.5,
        symbols=["XAUUSD", "US30"],
    )

    off = BacktestEngine.summarize_guardrail_mode(
        results,
        guardrails_enabled=False,
        starting_balance=5000.0,
        symbols=["XAUUSD", "US30"],
    )
    on = BacktestEngine.summarize_guardrail_mode(
        results,
        guardrails_enabled=True,
        starting_balance=5000.0,
        symbols=["XAUUSD", "US30"],
    )

    assert off["overall"]["trades_approved"] == 2
    assert off["overall"]["profit_factor"] == 3.0
    assert on["overall"]["trades_approved"] == 1
    assert on["overall"]["losses"] == 0
    assert on["by_symbol"]["XAUUSD"]["trades_approved"] == 1
    assert on["by_symbol"]["US30"]["trades_approved"] == 0
    assert on["by_killzone"]["london_open"]["trades_approved"] == 1
    assert on["by_killzone"]["new_york_open"]["trades_approved"] == 0
    assert on["by_narrative"]["expansion"]["trades_approved"] == 1
    assert on["by_narrative"]["range"]["trades_approved"] == 0


def test_phase_three_qualification_rules():
    assert BacktestEngine.qualifies_for_phase_three(
        {"profit_factor": 1.51, "win_rate": 50.01, "max_drawdown": 5.99}
    )
    assert not BacktestEngine.qualifies_for_phase_three(
        {"profit_factor": 1.5, "win_rate": 55.0, "max_drawdown": 5.0}
    )
    assert not BacktestEngine.qualifies_for_phase_three(
        {"profit_factor": 2.0, "win_rate": 50.0, "max_drawdown": 5.0}
    )
    assert not BacktestEngine.qualifies_for_phase_three(
        {"profit_factor": 2.0, "win_rate": 55.0, "max_drawdown": 6.0}
    )


def test_365d_loss_clusters_and_monthly_breakdown():
    trades = BacktestEngine.enrich_trade_pnl(
        [
            {
                "symbol": "XAUUSD",
                "timestamp": "2026-01-15T08:00:00+00:00",
                "killzone": "london_open",
                "guarded_confidence_band": "HOT",
                "confidence_band": "EXECUTION_READY",
                "narrative_phase": "distribution",
                "smt_detected": False,
                "simulation": {"outcome": "LOSS", "rr": -1.0},
            },
            {
                "symbol": "XAUUSD",
                "timestamp": "2026-01-20T08:00:00+00:00",
                "killzone": "london_open",
                "guarded_confidence_band": "HOT",
                "confidence_band": "EXECUTION_READY",
                "narrative_phase": "distribution",
                "smt_detected": False,
                "simulation": {"outcome": "WIN", "rr": 3.0},
            },
            {
                "symbol": "US30",
                "timestamp": "2026-02-01T14:00:00+00:00",
                "killzone": "new_york_open",
                "guarded_confidence_band": "HOT",
                "confidence_band": "HOT",
                "narrative_phase": "range",
                "smt_detected": True,
                "simulation": {"outcome": "LOSS", "rr": -1.0},
            },
        ],
        risk_amount=25.0,
    )

    clusters = loss_clusters(trades)
    monthly = monthly_breakdown(trades, starting_balance=5000.0)

    assert clusters[0] == {
        "symbol": "XAUUSD",
        "killzone": "london_open",
        "narrative": "distribution",
        "confidence_band": "HOT",
        "smt_state": "No SMT",
        "losses": 1,
    }
    assert monthly["2026-01"]["trades_approved"] == 2
    assert monthly["2026-01"]["profit_factor"] == 3.0
    assert monthly["2026-02"]["losses"] == 1


def test_365d_candle_trim_uses_calendar_days():
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(["2024-12-31", "2025-12-31", "2026-01-01"], utc=True),
            "open": [1, 1, 1],
            "high": [2, 2, 2],
            "low": [0, 0, 0],
            "close": [1, 1, 1],
        }
    )

    trimmed = trim_to_lookback_days(frame, 365)

    assert list(trimmed["time"].dt.strftime("%Y-%m-%d")) == ["2025-12-31", "2026-01-01"]


def test_365d_comparison_target_flags():
    comparison = build_comparison(
        {"profit_factor": 1.16, "win_rate": 52.22, "trades_approved": 123, "max_drawdown": 3.94},
        {"profit_factor": 1.55, "win_rate": 56.0, "trades_approved": 55, "max_drawdown": 3.5},
    )

    assert comparison["before"]["pf"] == 1.16
    assert comparison["after"]["pf"] == 1.55
    assert comparison["targets"] == {
        "pf_above_1_5": True,
        "wr_above_55": True,
        "max_dd_below_4": True,
        "trades_at_least_50": True,
    }
