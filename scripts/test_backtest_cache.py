"""Offline smoke test for backtest cache reporting integrations."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backtesting.report_cache import load_backtest_summary, save_backtest_summary
from backend.telegram_bot.telegram_command_bot import TelegramCommandBot
from dashboard.utils.data_loader import analytics_dataframe, analytics_summary, load_backtest_summary as load_dashboard_backtest_summary


SAMPLE_SUMMARY = {
    "generated_at": "2026-06-28T00:00:00+00:00",
    "adaptive_guardrails": True,
    "days_30": {"pf": 2.0, "win_rate": 63.64, "trades": 13, "max_drawdown": 0.99, "net_rr": 4.0},
    "days_90": {"pf": 1.75, "win_rate": 61.29, "trades": 45, "max_drawdown": 1.0, "net_rr": 9.0},
    "phase_decision": "Phase 3 Qualified: Execution Automation Research",
}


def main() -> int:
    """Exercise save/load, missing fallback, Telegram formatting, and dashboard loading."""
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        cache_path = root / "data" / "reports" / "latest_backtest_summary.json"

        saved = save_backtest_summary(SAMPLE_SUMMARY, cache_path)
        loaded = load_backtest_summary(cache_path)
        assert loaded == saved
        assert loaded["days_90"]["trades"] == 45

        missing = load_backtest_summary(root / "data" / "reports" / "missing.json")
        assert missing is None

        os.environ["TELEGRAM_CHAT_ID"] = "123"
        os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
        bot = TelegramCommandBot(project_root=root, config_dir=root / "config", snapshot_provider=lambda: {})
        telegram_text = bot.format_backtest({"available": True, "data": loaded})
        assert "Backtest Summary" in telegram_text
        assert "PF: 1.75" in telegram_text
        assert "Phase 3 Qualified" in telegram_text
        assert "test-token" not in telegram_text

        dashboard_summary = load_dashboard_backtest_summary(root, {"backtest_summary_paths": ["data/reports/latest_backtest_summary.json"]})
        cards = analytics_summary(dashboard_summary)
        analytics = analytics_dataframe(dashboard_summary)
        assert cards["days_30"]["pf"] == 2.0
        assert not analytics.empty
        assert float(analytics[analytics["metric"] == "Trade Count"]["value"].sum()) == 58.0

        json.loads(cache_path.read_text(encoding="utf-8"))

    print("CACHE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
