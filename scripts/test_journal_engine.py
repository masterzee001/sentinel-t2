"""Smoke test for Project Sentinel Journal Engine."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.journal.journal_engine import JournalEngine


def main() -> int:
    """Append a sample journal record and print confirmation."""
    journal = JournalEngine()
    record = journal.build_record(
        timestamp=datetime.now(ZoneInfo("Africa/Lagos")),
        environment="development",
        risk={
            "account": {
                "login": 0,
                "server": "sample",
                "account_mode": "demo",
                "balance": 0.0,
                "equity": 0.0,
                "currency": "USD",
            },
            "risk": {"risk_amount": 0.0},
            "permission": {"status": "ALLOWED", "warnings": [], "block_reasons": []},
        },
        news={"enabled": True, "lock_active": False, "event_name": None, "reason": ""},
        symbol="XAUUSD",
        trend={"daily_bias": "sample", "h4_bias": "sample", "h1_context": "sample"},
        ict={
            "mss": {"detected": False, "direction": None},
            "fvg": {"detected": False, "direction": None, "grade": None},
            "order_block": {"detected": False},
        },
        confidence={
            "confidence_band": "COLD",
            "total_confidence": 0,
            "decision": "REJECTED",
            "recommended_action": "Ignore",
            "rejection_reasons": ["Sample journal smoke test"],
        },
        trade_plan={
            "plan_quality": "diagnostic_only",
            "execution_allowed": False,
            "direction": None,
            "entry": {"price": 0.0},
            "stop_loss": {"price": 0.0},
            "take_profit": {"tp1": 0.0, "tp2": 0.0, "tp3": 0.0},
            "risk": {"lot_size": 0.0, "rr_to_tp1": 0.0, "rr_to_tp2": 0.0, "rr_to_tp3": 0.0},
        },
        commentary="Sample journal smoke test.",
    )
    journal.append_record(record)
    last_record = journal.read_last_records(1)[0]

    print("JOURNAL ENGINE")
    print(f"Journal Path:  {journal.journal_path}")
    print(f"Record Count:  {journal.count_records()}")
    print(f"Last Symbol:   {last_record['symbol']}")
    print(f"Last Decision: {last_record['decision']}")
    print("Advisor Mode only: no execution action was taken.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
