"""Optional live Telegram command bot readiness message."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.telegram_bot.telegram_command_bot import TelegramCommandBot


def main() -> int:
    """Send a readiness message only when Telegram credentials are present."""
    load_dotenv()
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        print("Telegram command live test skipped: token/chat ID not loaded.")
        return 0

    bot = TelegramCommandBot()
    sent = bot.send_message(os.getenv("TELEGRAM_CHAT_ID", ""), "Project Sentinel command bot ready. Send /status.")
    print(f"Telegram command live test sent: {sent}")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())

