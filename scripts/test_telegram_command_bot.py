"""Offline smoke test for Project Sentinel Telegram command bot."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.telegram_bot.telegram_command_bot import TelegramCommandBot


def sample_snapshot() -> dict[str, Any]:
    """Return a safe mocked Sentinel snapshot."""
    return {
        "risk": {
            "account": {"balance": 2000.0, "equity": 1995.0, "currency": "USD"},
            "risk": {"risk_amount": 10.0},
            "permission": {"status": "ALLOWED", "warnings": ["Daily loss history unavailable"], "block_reasons": []},
        },
        "news": {"lock_active": False, "event_name": None, "reason": "", "status": "CLEAR"},
        "symbols": {
            "XAUUSD": {
                "state": "HOT",
                "confidence": 82,
                "decision": "REJECTED",
                "killzone": "new_york_open",
                "narrative_summary": "Bullish displacement forming.",
                "smt": {"smt_detected": True, "direction": "bullish", "pair_name": "XAUUSD vs EURUSD"},
                "entry": 4001.5,
                "sl": 3990.0,
                "tp1": 4013.0,
                "tp2": 4024.5,
                "tp3": 4036.0,
                "lot_size": 0.02,
                "plan_quality": "diagnostic_only",
                "execution_allowed": False,
                "rejection_reasons": ["MSS not confirmed"],
            },
            "US30": {"state": "COLD", "confidence": 45, "decision": "REJECTED", "plan_quality": "diagnostic_only", "execution_allowed": False},
            "EURUSD": {"state": "COLD", "confidence": 38, "decision": "REJECTED", "plan_quality": "diagnostic_only", "execution_allowed": False},
            "GBPUSD": {"state": "COLD", "confidence": 35, "decision": "REJECTED", "plan_quality": "diagnostic_only", "execution_allowed": False},
            "BTCUSD": {
                "symbol": "BTCUSD",
                "display_symbol": "BTCUSD (EXPERIMENTAL)",
                "experimental": True,
                "mode": "DEMO_SANDBOX",
                "state": "WARM",
                "confidence": 50,
                "decision": "REJECTED",
                "killzone": "new_york_open",
                "narrative_summary": "BTCUSD observer mode.",
                "smt": {"smt_detected": False},
                "entry": 0.0,
                "sl": 0.0,
                "tp1": 0.0,
                "tp2": 0.0,
                "tp3": 0.0,
                "lot_size": 0.0,
                "plan_quality": "observer_only",
                "execution_allowed": False,
                "rejection_reasons": ["BTCUSD demo sandbox: production execution disabled"],
            },
        },
        "positions": [],
        "journal": [
            {
                "timestamp": "2026-06-28T14:30:00+01:00",
                "symbol": "XAUUSD",
                "confidence": 82,
                "decision": "REJECTED",
                "trade_plan": {"plan_quality": "diagnostic_only"},
                "rejection_reasons": ["MSS not confirmed"],
            }
        ],
        "backtest": {
            "available": True,
            "data": {
                "generated_at": "2026-06-28T00:00:00+00:00",
                "adaptive_guardrails": True,
                "days_30": {"pf": 2.0, "win_rate": 63.64, "trades": 13, "max_drawdown": 0.99, "net_rr": 4.0},
                "days_90": {"pf": 1.75, "win_rate": 61.29, "trades": 45, "max_drawdown": 1.0, "net_rr": 9.0},
                "phase_decision": "Phase 3 Qualified: Execution Automation Research",
            },
        },
        "live_data": {
            "available": True,
            "total_records": 6,
            "symbols": {
                "XAUUSD": {"total_scans": 3, "warm": 2, "hot": 1, "execution_ready": 0, "symbol_mode": "production"},
                "US30": {"total_scans": 1, "warm": 1, "hot": 0, "execution_ready": 0, "symbol_mode": "production"},
                "BTCUSD": {"total_scans": 2, "warm": 1, "hot": 1, "execution_ready": 0, "symbol_mode": "demo_sandbox"},
            },
            "killzones": {"london_open": 2},
            "narratives": {"distribution": 2},
            "rejection_reasons": {"no SMT": 2},
        },
        "stress": {
            "available": True,
            "safe_risk_percent": 0.5,
            "autonomous_mode_recommended": False,
            "trades_used": 56,
            "risk_models": {
                "0.5%": {
                    "drawdown": {"p95_dd": 3.8, "max_dd": 4.5},
                    "streaks": {"worst_losing_streak": 8},
                    "risk_of_ruin": {"breach_4_percent": 3.0, "breach_6_percent": 0.0},
                }
            },
            "risk_notes": [],
            "recommendations": ["0.5% is optimal."],
        },
        "readiness": {
            "ready": False,
            "score": 9,
            "checks_passed": 9,
            "checks_failed": 2,
            "results": [
                {"check": "news_clear", "status": "FAIL", "reason": "News lock active"},
                {"check": "spread_acceptable", "status": "FAIL", "reason": "Spread too high: 100 > 80"},
            ],
            "blocking_reasons": ["News lock active", "Spread too high: 100 > 80"],
        },
        "settings": {
            "execution_mode": "advisor",
            "telegram_enabled": True,
            "alerts_enabled": True,
            "guardrails_enabled": True,
            "news_filter_enabled": True,
            "journal_enabled": True,
        },
        "coach": {
            "summary": "Coach: Favor XAUUSD / New York Open. Autonomous execution not recommended.",
            "recommendations": [
                {"severity": "INFO", "category": "symbol", "message": "Favor XAUUSD over GBPUSD."},
                {"severity": "CRITICAL", "category": "execution", "message": "Do not enable autonomous execution yet."},
            ],
        },
    }


def main() -> int:
    """Run offline command handling examples without Telegram network calls."""
    bot = TelegramCommandBot(snapshot_provider=sample_snapshot)
    chat_id = "123"
    import os

    os.environ["TELEGRAM_CHAT_ID"] = chat_id

    print("TELEGRAM COMMAND BOT OFFLINE TEST")
    print("---------------------------------")
    for command in ["/ping", "/help", "/status", "/xauusd", "/btcusd", "/positions", "/plans", "/journal", "/backtest", "/live_stats", "/stress", "/readiness", "/settings", "/coach", "/unknown"]:
        result = bot.handle_command(command, chat_id)
        print("")
        print(f"{command}: success={result['success']} error={result['error']}")
        print(result["response_text"])

    unauthorized = bot.handle_command("/status", "999")
    print("")
    print(f"unauthorized: success={unauthorized['success']} error={unauthorized['error']}")
    print(unauthorized["response_text"])
    print("")
    print("No Telegram messages were sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
