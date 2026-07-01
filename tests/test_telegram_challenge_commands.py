from __future__ import annotations

import json
from pathlib import Path

from backend.challenge_mode.challenge_command_center import ChallengeCommandCenter
from backend.telegram_bot.telegram_command_bot import TelegramCommandBot


def write_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "telegram_bot.yaml").write_text(
        """
enabled: true
allowed_commands:
  - /challenge_status
  - /challenge_progress
  - /challenge_risk
  - /challenge_phase
  - /challenge_governor
  - /challenge_recommendation
  - /activate_challenge_mode
  - /deactivate_challenge_mode
advisor_mode_only: true
challenge_command_center_report_path: data/reports/challenge_command_center.json
symbols:
  XAUUSD: XAUUSD
""",
        encoding="utf-8",
    )


def write_report(root: Path) -> dict:
    report = ChallengeCommandCenter().build_report()
    path = root / "data" / "reports" / "challenge_command_center.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return report


def make_bot(tmp_path: Path, monkeypatch) -> TelegramCommandBot:
    write_config(tmp_path / "config")
    write_report(tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    challenge = TelegramCommandBot(config_dir=tmp_path / "config", project_root=tmp_path).load_challenge_command_center_summary()
    return TelegramCommandBot(
        config_dir=tmp_path / "config",
        project_root=tmp_path,
        snapshot_provider=lambda: {"challenge_command_center": challenge, "symbols": {}, "risk": {}, "news": {}},
    )


def test_challenge_telegram_status_commands(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    status = bot.handle_command("/challenge_status", "123")
    progress = bot.handle_command("/challenge_progress", "123")
    risk = bot.handle_command("/challenge_risk", "123")
    phase = bot.handle_command("/challenge_phase", "123")
    governor = bot.handle_command("/challenge_governor", "123")
    recommendation = bot.handle_command("/challenge_recommendation", "123")

    assert "CHALLENGE STATUS" in status["response_text"]
    assert "DISABLED" in status["response_text"]
    assert "Target Progress" in progress["response_text"]
    assert "CHALLENGE RISK BUFFER" in risk["response_text"]
    assert "PHASE_1" in phase["response_text"]
    assert "Risk Mode" in governor["response_text"]
    assert "Recommendation" in recommendation["response_text"]


def test_challenge_activation_commands_are_confirmation_only(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    activate = bot.handle_command("/activate_challenge_mode", "123")
    deactivate = bot.handle_command("/deactivate_challenge_mode", "123")

    assert "Confirmation-only command" in activate["response_text"]
    assert "No challenge mode state was changed" in activate["response_text"]
    assert "DISABLED" in activate["response_text"]
    assert "Confirmation-only command" in deactivate["response_text"]
