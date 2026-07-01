"""Run Project Sentinel Telegram command bot."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.telegram_bot.telegram_command_bot import TelegramCommandBot, TelegramCommandBotError


def main() -> int:
    """Start Telegram polling with the live Sentinel stack."""
    configure_terminal_logging()
    load_dotenv()
    connector = MT5Connector()
    try:
        bot = TelegramCommandBot(connector=connector)
        validation = bot.validate_runtime()
        if not validation["valid"]:
            print("PROJECT SENTINEL TELEGRAM BOT")
            print("Advisor Mode only")
            print("Commands disabled")
            print("Telegram token/chat ID missing or bot disabled.")
            return 1

        connector.connect()
        print("PROJECT SENTINEL TELEGRAM BOT")
        print("Advisor Mode only")
        print("Commands enabled")
        bot.run_polling()
        return 0
    except KeyboardInterrupt:
        print("\nProject Sentinel Telegram bot stopped by user.")
        return 0
    except (MT5ConnectorError, TelegramCommandBotError, RuntimeError, ValueError) as exc:
        print(f"Project Sentinel Telegram bot failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def configure_terminal_logging() -> None:
    """Keep bot terminal output readable."""
    logger.remove()
    logger.add(sys.stderr, level="ERROR")


if __name__ == "__main__":
    raise SystemExit(main())

