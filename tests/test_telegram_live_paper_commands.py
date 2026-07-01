from __future__ import annotations

import json
from pathlib import Path

from backend.telegram_bot.telegram_command_bot import TelegramCommandBot


def write_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "telegram_bot.yaml").write_text(
        """
enabled: true
allowed_commands:
  - /paper_status
  - /paper_trades
  - /paper_stats
  - /live_health
  - /live_signals
advisor_mode_only: true
live_paper_report_path: data/reports/live_paper_session.json
symbols:
  XAUUSD: XAUUSD
""",
        encoding="utf-8",
    )


def write_live_paper_report(root: Path) -> None:
    report_path = root / "data" / "reports" / "live_paper_session.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "runtime_ready": True,
                "live_feed_health": {
                    "score": 94.2,
                    "classification": "EXCELLENT",
                    "missing_candles": 0,
                    "delayed_candles": 1,
                    "inconsistent_timestamps": 0,
                    "symbol_feed_interruptions": 0,
                    "broker_spread_anomalies": 1,
                },
                "paper_stats": {
                    "pf": 3.1,
                    "win_rate": 75.0,
                    "trades": 4,
                    "max_drawdown": 1.2,
                    "avg_rr": 0.9,
                    "avg_spread": 19.4,
                    "avg_slippage": 2.2,
                    "avg_latency": 410,
                },
                "drift": {"classification": "STABLE"},
                "paper_trades": [
                    {
                        "paper_trade_id": "LP-0001",
                        "symbol": "XAUUSD",
                        "state": "TP_HIT",
                        "rr": 1.5,
                        "micro_regime": "institutional_continuation",
                        "strategy": "trend_following",
                        "quality_grade": "A+",
                        "confidence": 94,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def make_bot(tmp_path: Path, monkeypatch) -> TelegramCommandBot:
    write_config(tmp_path / "config")
    write_live_paper_report(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    live_paper = TelegramCommandBot(config_dir=tmp_path / "config", project_root=tmp_path).load_live_paper_summary()
    return TelegramCommandBot(
        config_dir=tmp_path / "config",
        project_root=tmp_path,
        snapshot_provider=lambda: {"live_paper": live_paper, "symbols": {}, "risk": {}, "news": {}},
    )


def test_paper_phase_telegram_commands(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    status = bot.handle_command("/paper_status", "123")
    trades = bot.handle_command("/paper_trades", "123")
    stats = bot.handle_command("/paper_stats", "123")
    health = bot.handle_command("/live_health", "123")
    signals = bot.handle_command("/live_signals", "123")

    assert "LIVE PAPER STATUS" in status["response_text"]
    assert "Broker Orders: DISABLED" in status["response_text"]
    assert "LP-0001" in trades["response_text"]
    assert "LIVE PAPER STATS" in stats["response_text"]
    assert "EXCELLENT" in health["response_text"]
    assert "institutional_continuation" in signals["response_text"]
