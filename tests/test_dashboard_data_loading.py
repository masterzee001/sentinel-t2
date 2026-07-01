from __future__ import annotations

import json
from pathlib import Path

from dashboard.utils.data_loader import (
    analytics_dataframe,
    analytics_summary,
    btc_symbol_snapshot,
    emergency_approval_dataframe,
    fallback_snapshot,
    journal_dataframe,
    load_backtest_summary,
    load_dashboard_config,
    load_emergency_live_summary,
    load_journal_records,
    load_live_data_summary,
    load_live_paper_summary,
    load_market_watch_summary,
    load_monte_carlo_summary,
    nas100_symbol_snapshot,
    live_data_symbol_dataframe,
    live_paper_execution_dataframe,
    live_paper_trade_dataframe,
    plan_dataframe,
    readiness_summary,
    symbol_dataframe,
    symbol_registry_dataframe,
    symbol_registry_rows,
)
from backend.observer.btc_observer import BTCObserver


def test_load_dashboard_config_merges_defaults(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("symbols:\n  - XAUUSD\nrefresh_seconds: 15\n", encoding="utf-8")

    config = load_dashboard_config(config_path)

    assert config["refresh_seconds"] == 15
    assert config["symbols"] == ["XAUUSD"]
    assert config["advisor_mode_only"] is True


def test_load_journal_records_and_dataframe(tmp_path: Path):
    journal_path = tmp_path / "data" / "journal" / "sentinel_decisions.jsonl"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-28T14:30:00+01:00",
                "symbol": "XAUUSD",
                "state": "HOT",
                "confidence": 82,
                "decision": "REJECTED",
                "trade_plan": {"plan_quality": "diagnostic_only"},
                "rejection_reasons": ["MSS not confirmed"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = load_journal_records(tmp_path, {"journal_path": "data/journal/sentinel_decisions.jsonl"})
    dataframe = journal_dataframe(records)

    assert len(records) == 1
    assert dataframe.iloc[0]["symbol"] == "XAUUSD"
    assert dataframe.iloc[0]["top_rejection_reason"] == "MSS not confirmed"


def test_load_backtest_summary_and_analytics_dataframe(tmp_path: Path):
    summary_path = tmp_path / "data" / "backtest_summary.json"
    summary_path.parent.mkdir()
    summary_path.write_text(
        json.dumps(
            {
                "30": {"adaptive_guardrails": {"overall": {"profit_factor": 1.4, "win_rate": 55.0, "max_drawdown": 2.0, "net_rr": 3.0}}},
                "90": {"adaptive_guardrails": {"overall": {"profit_factor": 1.75, "win_rate": 61.29, "max_drawdown": 1.0, "net_rr": 9.0}}},
            }
        ),
        encoding="utf-8",
    )

    summary = load_backtest_summary(tmp_path, {"backtest_summary_paths": ["data/backtest_summary.json"]})
    cards = analytics_summary(summary)
    analytics = analytics_dataframe(summary)

    assert summary["available"] is True
    assert cards["days_90"]["pf"] == 1.75
    assert analytics[(analytics["window"] == "90D") & (analytics["metric"] == "Profit Factor")].iloc[0]["value"] == 1.75
    assert analytics[(analytics["window"] == "30D") & (analytics["metric"] == "Trade Count")].iloc[0]["value"] == 0.0


def test_load_market_watch_summary(tmp_path: Path):
    report_path = tmp_path / "data" / "reports" / "market_watch_365d_summary.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "decision": "PASS",
                "recommendation": "Keep advisory only",
                "market_watch": {"advisory_only": True, "affect_production": False},
            }
        ),
        encoding="utf-8",
    )

    summary = load_market_watch_summary(tmp_path, {"market_watch_report_path": "data/reports/market_watch_365d_summary.json"})

    assert summary["available"] is True
    assert summary["data"]["decision"] == "PASS"
    assert summary["data"]["market_watch"]["affect_production"] is False


def test_load_live_data_summary_and_dataframe(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "live_data.yaml").write_text(
        """
enabled: true
symbols:
  - XAUUSD
  - BTCUSD
storage:
  format: jsonl
  path: data/live_data/live_signals.jsonl
retention:
  max_records: 100
""",
        encoding="utf-8",
    )
    data_path = tmp_path / "data" / "live_data" / "live_signals.jsonl"
    data_path.parent.mkdir(parents=True)
    data_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-28T08:00:00+01:00",
                        "symbol": "XAUUSD",
                        "state": "WARM",
                        "confidence": 63,
                        "adjusted_confidence": 58,
                        "decision": "MONITOR",
                        "bias": "bearish",
                        "narrative_phase": "distribution",
                        "killzone": "london_open",
                        "killzone_quality": 10,
                        "smt_detected": True,
                        "smt_direction": "bearish",
                        "risk_status": "ALLOWED",
                        "news_status": "CLEAR",
                        "execution_allowed": False,
                        "rejection_reasons": ["No SMT confirmation"],
                        "symbol_mode": "production",
                        "setup_id": "XAUUSD-20260628-test",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-28T08:00:00+01:00",
                        "symbol": "BTCUSD",
                        "state": "HOT",
                        "confidence": 70,
                        "adjusted_confidence": 65,
                        "decision": "MONITOR",
                        "bias": "bearish",
                        "narrative_phase": "distribution",
                        "killzone": "london_open",
                        "killzone_quality": 10,
                        "smt_detected": True,
                        "smt_direction": "bearish",
                        "risk_status": "ALLOWED",
                        "news_status": "CLEAR",
                        "execution_allowed": False,
                        "rejection_reasons": [],
                        "symbol_mode": "demo_sandbox",
                        "setup_id": "BTCUSD-20260628-test",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = load_live_data_summary(tmp_path, {"live_data_config_path": "config/live_data.yaml"})
    dataframe = live_data_symbol_dataframe(summary)

    assert summary["available"] is True
    assert summary["symbols"]["XAUUSD"]["warm"] == 1
    assert summary["symbols"]["BTCUSD"]["hot"] == 1
    assert summary["killzones"]["london_open"] == 2
    assert dataframe[dataframe["symbol"] == "BTCUSD"].iloc[0]["symbol_mode"] == "demo_sandbox"


def test_load_live_paper_summary_and_dashboard_widgets(tmp_path: Path):
    report_path = tmp_path / "data" / "reports" / "live_paper_session.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "runtime_ready": True,
                "live_feed_health": {"score": 94.0, "classification": "EXCELLENT"},
                "paper_stats": {"pf": 3.1, "win_rate": 75.0, "trades": 4, "max_drawdown": 1.2},
                "paper_trades": [
                    {
                        "paper_trade_id": "LP-0001",
                        "timestamp": "2026-06-29T10:00:00+00:00",
                        "symbol": "XAUUSD",
                        "state": "TP_HIT",
                        "strategy": "trend_following",
                        "micro_regime": "institutional_continuation",
                        "quality_grade": "A+",
                        "rr": 1.5,
                        "spread": 18,
                        "slippage": 2.1,
                        "latency": 410,
                        "expected_entry": 4010.0,
                        "actual_simulated_entry": 4010.2,
                        "slippage_points": 2.0,
                        "signal_delay_ms": 230,
                        "execution_delay_ms": 180,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = load_live_paper_summary(tmp_path, {"live_paper_report_path": "data/reports/live_paper_session.json"})
    trades = live_paper_trade_dataframe(summary)
    execution = live_paper_execution_dataframe(summary)

    assert summary["available"] is True
    assert summary["data"]["runtime_ready"] is True
    assert trades.iloc[0]["state"] == "TP_HIT"
    assert execution.iloc[0]["slippage_points"] == 2.0


def test_load_emergency_live_summary_and_approval_queue(tmp_path: Path):
    report_path = tmp_path / "data" / "reports" / "emergency_live_status.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "status": "LIVE_READY",
                "risk_lock": {"locked": True},
                "grade_lock": {"locked": True},
                "symbol_lock": {"locked": True},
                "config": {"risk_percent": 0.1, "max_risk_percent": 0.25},
                "approval_queue": [
                    {
                        "approval_id": "ELIVE-TEST",
                        "status": "PENDING",
                        "proposal": {
                            "symbol": "XAUUSD",
                            "strategy": "trend_following",
                            "quality_grade": "A+",
                            "risk_percent": 0.1,
                            "expected_pf": 2.84,
                            "expected_wr": 72.6,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = load_emergency_live_summary(tmp_path, {"emergency_live_report_path": "data/reports/emergency_live_status.json"})
    queue = emergency_approval_dataframe(summary)

    assert summary["available"] is True
    assert summary["data"]["risk_lock"]["locked"] is True
    assert queue.iloc[0]["approval_id"] == "ELIVE-TEST"
    assert queue.iloc[0]["quality_grade"] == "A+"


def test_fallback_snapshot_symbols_and_plans():
    snapshot = fallback_snapshot({"symbols": ["XAUUSD", "BTCUSD"]}, error="MT5 unavailable")
    symbols = symbol_dataframe(snapshot)
    plans = plan_dataframe(snapshot)

    assert snapshot["connected"] is False
    assert snapshot["readiness"]["ready"] is False
    assert "MT5 unavailable" in snapshot["readiness"]["blocking_reasons"]
    assert set(symbols["symbol"]) == {"XAUUSD", "BTCUSD"}
    assert "execution_allowed" in plans.columns
    assert bool(plans[plans["symbol"] == "BTCUSD"].iloc[0]["execution_allowed"]) is False


def test_dashboard_btc_badge_and_execution_blocked():
    row = btc_symbol_snapshot(
        {
            "confidence": {
                "confidence_band": "WARM",
                "total_confidence": 50,
                "decision": "REJECTED",
                "rejection_reasons": [BTCObserver.REJECTION_REASON],
            },
            "trade_plan": {"plan_quality": "observer_only"},
            "killzone": {"active_killzone": "new_york_open"},
            "narrative": {"summary": "BTCUSD observer mode."},
        }
    )

    assert row["symbol"] == "BTCUSD"
    assert row["badge"] == "DEMO_SANDBOX"
    assert row["mode"] == "DEMO_SANDBOX"
    assert row["observer_state"] == "OBSERVER_WARM"
    assert row["display_state"] == "WARM"
    assert "SANDBOX DEMO ONLY" in row["observer_note"]
    assert row["execution_allowed"] is False
    assert row["plan_quality"] == "observer_only"


def test_dashboard_nas100_badge_and_execution_blocked():
    row = nas100_symbol_snapshot(
        {
            "confidence": {
                "confidence_band": "WARM",
                "total_confidence": 54,
                "decision": "REJECTED",
                "rejection_reasons": ["NAS100 demo sandbox: production execution disabled"],
            },
            "trade_plan": {"plan_quality": "observer_only"},
            "killzone": {"active_killzone": "new_york_open"},
            "narrative": {"summary": "NAS100 observer mode."},
        }
    )

    assert row["symbol"] == "NAS100"
    assert row["badge"] == "DEMO_SANDBOX"
    assert row["mode"] == "DEMO_SANDBOX"
    assert row["observer_state"] == "OBSERVER_WARM"
    assert row["display_state"] == "WARM"
    assert "SANDBOX DEMO ONLY" in row["observer_note"]
    assert row["execution_allowed"] is False
    assert row["plan_quality"] == "observer_only"


def test_symbol_registry_dataframe_reads_cached_symbol_metrics(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "symbol_registry.yaml").write_text(
        """
tier_1_production:
  - US30
tier_2_filtered_production:
  - XAUUSD
tier_3_demo_sandbox:
  - BTCUSD
  - NAS100
tier_4_observer_only:
  - EURUSD
  - GBPUSD
""",
        encoding="utf-8",
    )
    report_path = tmp_path / "data" / "reports" / "backtest_365d_summary.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "symbol_breakdown": {
                    "US30": {"profit_factor": 1.69, "win_rate": 60.0, "trades": 47, "max_drawdown": 2.0},
                    "NAS100": {"profit_factor": 1.2, "win_rate": 50.0, "trades": 20, "max_drawdown": 2.5},
                }
            }
        ),
        encoding="utf-8",
    )

    rows = symbol_registry_rows(
        tmp_path,
        {
            "symbol_registry_path": "config/symbol_registry.yaml",
            "backtest_summary_paths": ["data/reports/backtest_365d_summary.json"],
        },
    )
    dataframe = symbol_registry_dataframe(rows)

    assert dataframe[dataframe["symbol"] == "US30"].iloc[0]["tier"] == "Production"
    assert dataframe[dataframe["symbol"] == "NAS100"].iloc[0]["status"] == "DEMO_SANDBOX"


def test_symbol_registry_dataframe_reads_observer_diagnostics(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "symbol_registry.yaml").write_text(
        """
tier_1_production:
  - US30
tier_2_filtered_production:
  - XAUUSD
tier_3_demo_sandbox:
  - BTCUSD
  - NAS100
tier_4_observer_only:
  - EURUSD
  - GBPUSD
""",
        encoding="utf-8",
    )
    report = {
        "available": True,
        "data": {
            "production_portfolio": {
                "symbol_breakdown": {
                    "US30": {"profit_factor": 1.69, "win_rate": 60.0, "trades": 47, "max_drawdown": 2.0},
                }
            },
            "observer_diagnostics": {
                "NAS100": {
                    "metrics": {"profit_factor": 1.2, "win_rate": 50.0, "trades": 20, "max_drawdown": 2.5}
                }
            },
        },
    }

    rows = symbol_registry_rows(
        tmp_path,
        {"symbol_registry_path": "config/symbol_registry.yaml"},
        backtest_summary=report,
    )
    dataframe = symbol_registry_dataframe(rows)
    nas100 = dataframe[dataframe["symbol"] == "NAS100"].iloc[0]

    assert nas100["tier"] == "Demo Sandbox"
    assert nas100["pf"] == 1.2
    assert nas100["status"] == "DEMO_SANDBOX"


def test_load_monte_carlo_summary_from_365d_report(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "monte_carlo.yaml").write_text(
        """
enabled: true
simulations: 50
random_seed: 7
risk_models:
  - 0.5
drawdown_limits:
  internal_limit: 4.0
  firm_limit: 6.0
starting_balance: 2000
backtest_report_path: data/reports/backtest_365d_summary.json
""",
        encoding="utf-8",
    )
    report_path = tmp_path / "data" / "reports" / "backtest_365d_summary.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "days_365": {
                    "wins": 2,
                    "losses": 1,
                    "breakevens": 1,
                    "gross_profit": 100.0,
                    "gross_loss": 25.0,
                    "net_rr": 3.0,
                }
            }
        ),
        encoding="utf-8",
    )

    summary = load_monte_carlo_summary(tmp_path, {"monte_carlo_config_path": "config/monte_carlo.yaml"})

    assert summary["available"] is True
    assert summary["trades_used"] == 4
    assert "0.5%" in summary["risk_models"]


def test_readiness_summary_defaults_blocked():
    summary = readiness_summary()

    assert summary["ready"] is False
    assert summary["checks_failed"] == 1
    assert summary["blocking_reasons"] == ["No assisted trade plan selected"]
