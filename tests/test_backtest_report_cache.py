from __future__ import annotations

import json
from pathlib import Path

from backend.backtesting.report_cache import load_backtest_summary, save_backtest_summary
from backend.telegram_bot.telegram_command_bot import TelegramCommandBot
from dashboard.utils.data_loader import analytics_dataframe, analytics_summary, load_backtest_summary as load_dashboard_backtest_summary


def sample_matrix() -> dict[int, dict[str, dict[str, dict[str, float]]]]:
    return {
        30: {
            "adaptive_guardrails": {
                "overall": {
                    "profit_factor": 2.0,
                    "win_rate": 63.64,
                    "trades_approved": 13,
                    "max_drawdown": 0.99,
                    "net_rr": 4.0,
                }
            }
        },
        90: {
            "adaptive_guardrails": {
                "overall": {
                    "profit_factor": 1.75,
                    "win_rate": 61.29,
                    "trades_approved": 45,
                    "max_drawdown": 1.0,
                    "net_rr": 9.0,
                }
            }
        },
    }


def sample_365_report() -> dict:
    return {
        "generated_at": "2026-06-28T00:00:00+00:00",
        "days": 365,
        "guardrails": "ADAPTIVE",
        "global_metrics": {
            "profit_factor": 1.6,
            "win_rate": 55.0,
            "trades_approved": 100,
            "max_drawdown": 4.5,
            "net_rr": 25.0,
        },
    }


def test_cache_write_and_read(tmp_path: Path):
    cache_path = tmp_path / "data" / "reports" / "latest_backtest_summary.json"

    saved = save_backtest_summary(sample_matrix(), cache_path)
    loaded = load_backtest_summary(cache_path)

    assert cache_path.exists()
    assert saved["days_30"]["pf"] == 2.0
    assert loaded == saved
    assert loaded["days_90"]["trades"] == 45
    assert loaded["phase_decision"] == "Phase 3 Qualified: Execution Automation Research"


def test_invalid_cache_fallback(tmp_path: Path):
    cache_path = tmp_path / "data" / "reports" / "latest_backtest_summary.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("{not-json", encoding="utf-8")

    assert load_backtest_summary(cache_path) is None


def test_telegram_backtest_formatter_uses_cached_schema(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    bot = TelegramCommandBot(config_dir=tmp_path / "config", project_root=tmp_path, snapshot_provider=lambda: {})
    summary = save_backtest_summary(sample_matrix(), tmp_path / "data" / "reports" / "latest_backtest_summary.json")

    text = bot.format_backtest({"available": True, "data": summary})

    assert "<b>Backtest Summary</b>" in text
    assert "30D:" in text
    assert "PF: 2.0" in text
    assert "WR: 63.64%" in text
    assert "Trades: 45" in text
    assert "Phase 3 Qualified" in text
    assert "token" not in text.lower()


def test_dashboard_analytics_loader_reads_cache(tmp_path: Path):
    cache_path = tmp_path / "data" / "reports" / "latest_backtest_summary.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(save_backtest_summary(sample_matrix(), cache_path)), encoding="utf-8")

    loaded = load_dashboard_backtest_summary(tmp_path, {"backtest_summary_paths": ["data/reports/latest_backtest_summary.json"]})
    summary = analytics_summary(loaded)
    dataframe = analytics_dataframe(loaded)

    assert loaded["available"] is True
    assert summary["days_90"]["pf"] == 1.75
    assert summary["phase_decision"] == "Phase 3 Qualified"
    assert dataframe[(dataframe["window"] == "30D") & (dataframe["metric"] == "Trade Count")].iloc[0]["value"] == 13.0


def test_dashboard_analytics_loader_reads_365d_report(tmp_path: Path):
    cache_path = tmp_path / "data" / "reports" / "backtest_365d_summary.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(sample_365_report()), encoding="utf-8")

    loaded = load_dashboard_backtest_summary(tmp_path, {"backtest_summary_paths": ["data/reports/backtest_365d_summary.json"]})
    summary = analytics_summary(loaded)
    dataframe = analytics_dataframe(loaded)

    assert loaded["available"] is True
    assert summary["days_365"]["pf"] == 1.6
    assert dataframe[(dataframe["window"] == "365D") & (dataframe["metric"] == "Trade Count")].iloc[0]["value"] == 100.0
