"""Best-effort Telegram push for the live book.

Extracted from the champion runner when that engine was retired (2026-08-12):
the mean-reversion engine and the supervisor both depend on this, and neither
should import from a decommissioned engine. Deliberately dependency-light —
a raw HTTP post, not the old interactive command bot, which was removed with
the rest of the prop-firm advisor tree.
"""

from __future__ import annotations

import os

import requests


def notify_telegram(text: str) -> bool:
    """Send a message; never raise. Returns whether it was delivered.

    Telegram plus the JSONL journals are the operator's window into the book,
    so a failure here must degrade quietly rather than take an engine down.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False
