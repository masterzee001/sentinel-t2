"""Run the Project Sentinel assisted paper-trade drill."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.execution_engine.paper_trade_session import PaperTradeSession


def main() -> int:
    """Run a configurable paper drill scenario."""
    configure_terminal_logging()
    args = parse_args()
    session = PaperTradeSession()
    approval_callback = None
    if args.auto_approve:
        approval_callback = lambda _request: True
    if args.reject:
        approval_callback = lambda _request: False

    result = session.run(
        scenario=args.scenario,
        approval_callback=approval_callback,
        send_telegram=args.send_telegram,
        readiness_blocked=args.readiness_blocked,
        approval_rejected=args.reject,
    )
    print(result["terminal_output"])
    if args.show_telegram_messages:
        print("\nTELEGRAM LIFECYCLE MESSAGES")
        for message in result["telegram_messages"]:
            print("---")
            print(message)
    return 0 if result["passed"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a broker-safe assisted paper trade rehearsal.")
    parser.add_argument(
        "--scenario",
        default="A",
        help="A/FULL_WIN, B/BREAKEVEN, C/STOP_LOSS, READINESS_BLOCKED, or APPROVAL_REJECTED.",
    )
    parser.add_argument("--auto-approve", action="store_true", help="Approve the paper execution prompt automatically.")
    parser.add_argument("--reject", action="store_true", help="Reject the paper execution prompt automatically.")
    parser.add_argument("--readiness-blocked", action="store_true", help="Force the readiness checker to block the drill.")
    parser.add_argument("--send-telegram", action="store_true", help="Send formatted lifecycle messages if Telegram is configured.")
    parser.add_argument(
        "--show-telegram-messages",
        action="store_true",
        help="Print formatted lifecycle messages without requiring Telegram delivery.",
    )
    return parser.parse_args()


def configure_terminal_logging() -> None:
    """Keep drill output readable."""
    logger.remove()
    logger.add(sys.stderr, level="ERROR")


if __name__ == "__main__":
    raise SystemExit(main())
