from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.alerts.alert_engine import AlertEngine


def write_alert_config(config_dir: Path, telegram: bool = False) -> None:
    config_dir.mkdir()
    (config_dir / "alerts.yaml").write_text(
        f"""
enabled: true
terminal: true
desktop: false
telegram: {str(telegram).lower()}
telegram_settings:
  parse_mode: HTML
  disable_web_page_preview: true
alert_on:
  - WARM_TO_HOT
  - HOT_TO_EXECUTION_READY
  - EXECUTION_READY_TO_LOWER
  - RISK_BLOCKED
  - NEWS_LOCK_ACTIVE
cooldown_minutes: 5
observer_symbols:
  - BTCUSD
  - NAS100
observer_hot_realert_confidence_delta: 5
""",
        encoding="utf-8",
    )


def make_engine(tmp_path: Path, telegram: bool = False) -> AlertEngine:
    config_dir = tmp_path / "config"
    write_alert_config(config_dir, telegram=telegram)
    return AlertEngine(config_dir=config_dir)


def test_transition_detection_warm_to_hot(tmp_path: Path):
    engine = make_engine(tmp_path)

    alert = engine.evaluate(
        symbol="XAUUSD",
        previous_state="WARM",
        current_state="HOT",
        confidence=78,
        timestamp=datetime(2026, 6, 28, 9, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    )

    assert alert["alert_triggered"] is True
    assert alert["transition"] == "WARM_TO_HOT"
    assert alert["message"] == "XAUUSD upgraded WARM -> HOT. Setup close. Wait for confirmation."


def test_transition_detection_hot_to_execution_ready(tmp_path: Path):
    engine = make_engine(tmp_path)

    alert = engine.evaluate(
        symbol="US30",
        previous_state="HOT",
        current_state="EXECUTION_READY",
        confidence=92,
    )

    assert alert["alert_triggered"] is True
    assert alert["transition"] == "HOT_TO_EXECUTION_READY"


def test_observer_diagnostic_alerts_still_allowed(tmp_path: Path):
    engine = make_engine(tmp_path)

    alert = engine.evaluate(
        symbol="NAS100",
        previous_state="WARM",
        current_state="HOT",
        confidence=76,
    )

    assert alert["alert_triggered"] is True
    assert alert["symbol"] == "NAS100"
    assert alert["transition"] == "WARM_TO_HOT"


def test_cooldown_suppression(tmp_path: Path):
    engine = make_engine(tmp_path)
    now = datetime(2026, 6, 28, 9, 0, tzinfo=ZoneInfo("Africa/Lagos"))

    first = engine.evaluate(
        symbol="XAUUSD",
        previous_state="WARM",
        current_state="HOT",
        confidence=78,
        timestamp=now,
    )
    second = engine.evaluate(
        symbol="XAUUSD",
        previous_state="WARM",
        current_state="HOT",
        confidence=79,
        timestamp=now + timedelta(minutes=1),
    )

    assert first["alert_triggered"] is True
    assert second["alert_triggered"] is False
    assert second["suppressed_by_cooldown"] is True


def test_observer_warm_hot_flapping_requires_stronger_move_or_cooldown(tmp_path: Path):
    engine = make_engine(tmp_path)
    now = datetime(2026, 6, 28, 9, 0, tzinfo=ZoneInfo("Africa/Lagos"))

    first = engine.evaluate(
        symbol="NAS100",
        previous_state="WARM",
        current_state="HOT",
        confidence=52,
        timestamp=now,
    )
    flapping = engine.evaluate(
        symbol="NAS100",
        previous_state="WARM",
        current_state="HOT",
        confidence=53,
        timestamp=now + timedelta(minutes=1),
    )
    stronger = engine.evaluate(
        symbol="NAS100",
        previous_state="WARM",
        current_state="HOT",
        confidence=57,
        timestamp=now + timedelta(minutes=2),
    )

    assert first["alert_triggered"] is True
    assert flapping["alert_triggered"] is False
    assert flapping["suppressed_by_cooldown"] is True
    assert stronger["alert_triggered"] is True


def test_risk_blocked_alert(tmp_path: Path):
    engine = make_engine(tmp_path)

    alert = engine.evaluate(
        symbol="EURUSD",
        previous_state="HOT",
        current_state="HOT",
        confidence=80,
        risk_status="BLOCKED",
    )

    assert alert["alert_triggered"] is True
    assert alert["transition"] == "RISK_BLOCKED"
    assert "blocked by risk" in alert["message"]


def test_news_lock_alert(tmp_path: Path):
    engine = make_engine(tmp_path)

    alert = engine.evaluate(
        symbol="GBPUSD",
        previous_state="WARM",
        current_state="WARM",
        confidence=55,
        news_lock_active=True,
    )

    assert alert["alert_triggered"] is True
    assert alert["transition"] == "NEWS_LOCK_ACTIVE"
    assert "blocked by news lock" in alert["message"]


def test_telegram_disabled_does_nothing(tmp_path: Path):
    engine = make_engine(tmp_path, telegram=False)

    assert engine.send_telegram_alert("Project Sentinel Telegram alerts connected.") is False


def test_telegram_enabled_missing_credentials_warns_without_crash(tmp_path: Path, monkeypatch):
    engine = make_engine(tmp_path, telegram=True)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    alert = engine.evaluate(
        symbol="XAUUSD",
        previous_state="WARM",
        current_state="HOT",
        confidence=78,
    )

    assert alert["alert_triggered"] is True
    assert alert["telegram_sent"] is False
    assert "Telegram credentials missing" in alert["warnings"]
    assert engine.last_telegram_warning == "Telegram enabled but TELEGRAM_BOT_TOKEN missing"


def test_validate_telegram_config(tmp_path: Path, monkeypatch):
    engine = make_engine(tmp_path, telegram=True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    validation = engine.validate_telegram_config()

    assert validation == {
        "telegram_enabled": True,
        "token_loaded": True,
        "chat_id_loaded": False,
        "valid": False,
    }


def test_telegram_enabled_missing_chat_id(tmp_path: Path, monkeypatch):
    engine = make_engine(tmp_path, telegram=True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    sent = engine.send_telegram_alert("Project Sentinel Telegram alerts connected.")

    assert sent is False
    assert engine.last_telegram_warning == "Telegram enabled but TELEGRAM_CHAT_ID missing"


def test_telegram_successful_send_mocked(tmp_path: Path, monkeypatch):
    engine = make_engine(tmp_path, telegram=True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["data"] = request.data.decode("utf-8")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("backend.alerts.alert_engine.urllib.request.urlopen", fake_urlopen)

    sent = engine.send_telegram_alert("Project Sentinel Telegram alerts connected.")

    assert sent is True
    assert captured["url"] == "https://api.telegram.org/bottoken/sendMessage"
    assert "chat_id=123" in captured["data"]
    assert "parse_mode=HTML" in captured["data"]
    assert "disable_web_page_preview=true" in captured["data"]
    assert captured["timeout"] == 10


def test_telegram_failed_send_response(tmp_path: Path, monkeypatch):
    engine = make_engine(tmp_path, telegram=True)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    class FakeResponse:
        status = 500

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr("backend.alerts.alert_engine.urllib.request.urlopen", lambda request, timeout: FakeResponse())

    sent = engine.send_telegram_alert("Project Sentinel Telegram alerts connected.")

    assert sent is False
    assert engine.last_telegram_warning == "Telegram API returned status 500"


def test_format_telegram_message():
    message = AlertEngine.format_telegram_message(
        {
            "symbol": "XAUUSD",
            "previous_state": "WARM",
            "current_state": "HOT",
            "confidence": 78,
            "action": "PREPARE",
            "commentary": "Setup close. Wait for confirmation.",
        }
    )

    assert "<b>PROJECT SENTINEL ALERT</b>" in message
    assert "Symbol: XAUUSD" in message
    assert "Transition: WARM \u2192 HOT" in message
    assert "Advisor Mode only \u2014 no trade execution." in message
