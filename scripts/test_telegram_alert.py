"""Smoke test for Project Sentinel Telegram alerts."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.alerts.alert_engine import AlertEngine


FORCE_TELEGRAM_TEST = False


def main() -> int:
    """Send a Telegram test message when Telegram alerts are enabled and configured."""
    load_dotenv()
    engine = AlertEngine()
    if FORCE_TELEGRAM_TEST:
        engine.config["telegram"] = True

    validation = engine.validate_telegram_config()

    print("TELEGRAM ALERT TEST")
    print(f"Alerts Enabled:   {engine.enabled}")
    print(f"Terminal Enabled: {engine.terminal_enabled}")
    print(f"Telegram Enabled: {engine.telegram_enabled}")
    print(f"Bot Token Loaded: {validation['token_loaded']}")
    print(f"Chat ID Loaded:   {validation['chat_id_loaded']}")

    if not engine.telegram_enabled:
        sent = False
        status = "Telegram disabled in config/alerts.yaml"
    elif not validation["token_loaded"]:
        sent = False
        status = "Telegram enabled but TELEGRAM_BOT_TOKEN missing"
    elif not validation["chat_id_loaded"]:
        sent = False
        status = "Telegram enabled but TELEGRAM_CHAT_ID missing"
    else:
        message = "\n".join(
            [
                "<b>PROJECT SENTINEL ALERT</b>",
                "",
                "Telegram alerts connected successfully.",
                "Advisor Mode only.",
            ]
        )
        sent = engine.send_telegram_alert(message)
        status = "Telegram message sent successfully" if sent else (engine.last_telegram_warning or "Telegram send failed")

    print("")
    print(f"Message Sent:     {sent}")
    print(f"HTTP Status:      {engine.last_telegram_http_status if engine.last_telegram_http_status is not None else 'n/a'}")
    print(f"Status:           {status}")
    print("Advisor Mode only: no execution action was taken.")
    return 0 if sent or not engine.telegram_enabled else 1


if __name__ == "__main__":
    raise SystemExit(main())
