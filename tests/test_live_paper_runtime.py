from __future__ import annotations

from backend.live_paper.live_paper_runtime import (
    LiveFeedHealthMonitor,
    LivePaperRuntime,
    drift_classification,
    live_execution_drift,
    simulate_slippage_points,
)


def test_paper_execution_lifecycle_never_submits_broker_order():
    report = LivePaperRuntime().run_sample_session()

    first_trade = report["paper_trades"][0]
    assert first_trade["state_history"][:4] == ["SIGNAL_DETECTED", "APPROVED", "ENTRY_SIMULATED", "IN_POSITION"]
    assert first_trade["state"] in {"TP_HIT", "SL_HIT", "BREAKEVEN", "CANCELLED"}
    assert first_trade["broker_order_submitted"] is False
    assert report["broker_order_submission"] is False
    assert report["autonomous_execution"] is False


def test_btcusd_cannot_create_live_paper_trade():
    trade = LivePaperRuntime().process_signal(
        {
            "symbol": "BTCUSD",
            "guardrails_pass": True,
            "readiness_pass": True,
            "result": "TP_HIT",
            "rr": 2.0,
        },
        index=1,
    )

    assert trade["state"] == "BLOCKED_OBSERVER_SYMBOL"
    assert trade["paper_trade_created"] is False
    assert trade["broker_order_submitted"] is False
    assert "paper_trade_id" not in trade


def test_slippage_simulation_increases_under_volatility():
    normal = simulate_slippage_points(20, volatile=False)
    volatile = simulate_slippage_points(20, volatile=True)

    assert normal > 0
    assert volatile > normal


def test_latency_tracking_records_signal_and_execution_delay():
    trade = LivePaperRuntime().process_signal(
        {
            "symbol": "XAUUSD",
            "entry": 4010.0,
            "signal_delay_ms": 250,
            "execution_delay_ms": 180,
            "result": "TP_HIT",
            "rr": 1.5,
        },
        index=1,
    )

    assert trade["signal_delay_ms"] == 250
    assert trade["execution_delay_ms"] == 180
    assert trade["latency"] == 430


def test_live_health_scoring_classifies_degraded_feed():
    health = LiveFeedHealthMonitor().score(
        [
            {"missing_candles": 2, "delayed_candle": True, "inconsistent_timestamp": False, "feed_interruption": False, "spread_anomaly": True},
            {"missing_candles": 0, "delayed_candle": False, "inconsistent_timestamp": True, "feed_interruption": True, "spread_anomaly": False},
        ]
    )

    assert health["score"] < 90
    assert health["classification"] in {"GOOD", "DEGRADED", "UNUSABLE"}
    assert health["missing_candles"] == 2


def test_drift_detection_flags_major_pf_divergence():
    drift = live_execution_drift(
        {"pf": 2.84, "win_rate": 72.6, "trades": 151, "max_drawdown": 3.72},
        {"pf": 2.1, "win_rate": 66.0, "trades": 20, "max_drawdown": 4.8},
    )

    assert drift["pf_drift"] < 0
    assert drift["classification"] in {"MODERATE DRIFT", "MAJOR DRIFT"}
    assert drift_classification(5) == "STABLE"
