"""Retrieve Telegram chat IDs for Project Sentinel alerts."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    """Fetch Telegram getUpdates and print available chat IDs."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN is missing. Add it to your local .env file.")
        return 1

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"Failed to retrieve Telegram updates: {exc}")
        return 1

    print("TELEGRAM CHAT IDS")
    results = payload.get("result", [])
    if not results:
        print("No chats found. Send a message to your bot, then run this script again.")
        return 0

    seen_chat_ids = set()
    for update in results:
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat_id in seen_chat_ids or chat_id is None:
            continue
        seen_chat_ids.add(chat_id)
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "unknown"
        print(f"Chat ID: {chat_id} | Name: {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
