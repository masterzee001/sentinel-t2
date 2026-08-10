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
  - /live_mode
  - /live_limits
  - /live_killswitch
  - /approve_trade
  - /reject_trade
  - /halt_live
  - /resume_live
advisor_mode_only: true
emergency_live_report_path: data/reports/emergency_live_status.json
symbols:
  XAUUSD: XAUUSD
""",
        encoding="utf-8",
    )


def write_report(root: Path) -> None:
    report_path = root / "data" / "reports" / "emergency_live_status.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "status": "LIVE_READY",
                "halt_reason": "",
                "config": {
                    "risk_percent": 0.1,
                    "max_risk_percent": 0.25,
                    "allowed_symbols": ["US30", "XAUUSD"],
                    "allowed_grades": ["A+"],
                    "max_trades_per_day": 2,
                    "human_approval_required": True,
                    "kill_switch": {"daily_loss_r": -1, "consecutive_losses": 3, "max_drawdown_percent": 2},
                },
                "approval_queue": [
                    {
                        "approval_id": "ELIVE-OBSERVER",
                        "status": "PENDING",
                        "proposal": {"symbol": "NAS100", "quality_grade": "A+", "risk_percent": 0.1},
                        "validation": {"valid": False, "broker_order_submission_allowed": False},
                    },
                    {
                        "approval_id": "ELIVE-TEST",
                        "status": "PENDING",
                        "proposal": {"symbol": "US30", "quality_grade": "A+", "risk_percent": 0.1},
                        "validation": {"valid": True, "broker_order_submission_allowed": False},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def make_bot(tmp_path: Path, monkeypatch) -> TelegramCommandBot:
    write_config(tmp_path / "config")
    write_report(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    emergency_live = TelegramCommandBot(config_dir=tmp_path / "config", project_root=tmp_path).load_emergency_live_summary()
    return TelegramCommandBot(
        config_dir=tmp_path / "config",
        project_root=tmp_path,
        snapshot_provider=lambda: {"emergency_live": emergency_live, "symbols": {}, "risk": {}, "news": {}},
    )


def test_emergency_live_telegram_commands(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    mode = bot.handle_command("/live_mode", "123")
    limits = bot.handle_command("/live_limits", "123")
    kill = bot.handle_command("/live_killswitch", "123")
    approve = bot.handle_command("/approve_trade", "123")
    reject = bot.handle_command("/reject_trade", "123")
    halt = bot.handle_command("/halt_live", "123")
    resume = bot.handle_command("/resume_live", "123")

    assert "CONTROLLED ASSISTED LIVE" in mode["response_text"]
    assert "0.25" in limits["response_text"]
    assert "Manual override required" in kill["response_text"]
    assert "ELIVE-TEST" in approve["response_text"]
    assert "ELIVE-OBSERVER" not in approve["response_text"]
    assert "ELIVE-TEST" in reject["response_text"]
    assert "LIVE HALT requested" in halt["response_text"]
    assert "LIVE RESUME requested" in resume["response_text"]
