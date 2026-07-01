from __future__ import annotations

import json
from pathlib import Path

from backend.telegram_bot.telegram_command_bot import TelegramCommandBot


def write_config(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "telegram_bot.yaml").write_text(
        """
enabled: true
polling_interval_seconds: 5
allowed_commands:
  - /start
  - /help
  - /status
  - /summary
  - /xauusd
  - /us30
  - /eurusd
  - /gbpusd
  - /btcusd
  - /nas100
  - /symbols
  - /risk
  - /news
  - /coach
  - /ping
  - /positions
  - /plans
  - /journal
  - /backtest
  - /live_stats
  - /stress
  - /readiness
  - /settings
  - /validation
  - /market_watch
symbols:
  XAUUSD: XAUUSD
  US30: US30
  EURUSD: EURUSD
  GBPUSD: GBPUSD
  BTCUSD: BTCUSD
  NAS100: NAS100
advisor_mode_only: true
""",
        encoding="utf-8",
    )


def sample_snapshot():
    return {
        "risk": {
            "account": {"balance": 2000.0, "equity": 1990.0, "currency": "USD", "login": "123456"},
            "risk": {"risk_amount": 10.0},
            "permission": {"status": "ALLOWED", "warnings": ["Daily loss history unavailable"], "block_reasons": []},
        },
        "news": {"lock_active": True, "event_name": "CPI", "reason": "Example CPI in 10 minutes"},
        "symbols": {
            "XAUUSD": {
                "state": "HOT",
                "confidence": 82,
                "decision": "REJECTED",
                "killzone": "new_york_open",
                "narrative_summary": "Bullish displacement forming.",
                "smt": {"smt_detected": True, "direction": "bullish", "pair_name": "XAUUSD vs EURUSD"},
                "entry": 4000.0,
                "sl": 3990.0,
                "tp1": 4010.0,
                "tp2": 4020.0,
                "tp3": 4030.0,
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
            "NAS100": {
                "symbol": "NAS100",
                "display_symbol": "NAS100 (OBSERVER)",
                "observer": True,
                "mode": "DEMO_SANDBOX",
                "state": "WARM",
                "confidence": 54,
                "decision": "REJECTED",
                "killzone": "new_york_open",
                "narrative_summary": "NAS100 observer mode.",
                "smt": {"smt_detected": False},
                "entry": 0.0,
                "sl": 0.0,
                "tp1": 0.0,
                "tp2": 0.0,
                "tp3": 0.0,
                "lot_size": 0.0,
                "plan_quality": "observer_only",
                "execution_allowed": False,
                "rejection_reasons": ["NAS100 demo sandbox: production execution disabled"],
                "tier": "Demo Sandbox",
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
        "backtest": {"available": False},
        "live_data": {
            "available": True,
            "total_records": 6,
            "symbols": {
                "XAUUSD": {"total_scans": 3, "warm": 2, "hot": 1, "execution_ready": 0, "symbol_mode": "production"},
                "US30": {"total_scans": 1, "warm": 1, "hot": 0, "execution_ready": 0, "symbol_mode": "production"},
                "BTCUSD": {"total_scans": 2, "warm": 1, "hot": 1, "execution_ready": 0, "symbol_mode": "demo_sandbox"},
                "NAS100": {"total_scans": 1, "warm": 1, "hot": 0, "execution_ready": 0, "symbol_mode": "demo_sandbox"},
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
            "token": "secret-token",
            "chat_id": "123",
        },
        "validation": {
            "available": True,
            "data": {
                "decision": "PASS",
                "matches_approved_baseline": True,
                "approved_robustness_baseline": {"pf": 1.58, "win_rate": 58.7, "trades": 56, "max_drawdown": 2.97},
                "symbol_expansion_observer_only": {"pf": 1.58, "win_rate": 58.7, "trades": 56, "max_drawdown": 2.97},
                "observer_diagnostics": {
                    "BTCUSD": {"display_symbol": "BTC", "data_status": "CANDLES_AVAILABLE_NO_SETUPS"},
                    "NAS100": {"display_symbol": "NAS100/USTEC", "data_status": "GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES"},
                    "EURUSD": {"display_symbol": "EURUSD", "data_status": "GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES"},
                    "GBPUSD": {"display_symbol": "GBPUSD", "data_status": "GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES"},
                },
                "xau_smt": {"dependency": "NO_SMT_SAMPLE", "rule": {"hard_block_enabled": False}},
            },
        },
        "market_watch": {
            "available": True,
            "data": {
                "decision": "PASS",
                "recommendation": "Keep advisory only",
                "market_watch": {"advisory_only": True, "affect_production": False},
                "strategy_diagnostics": {
                    "US30": {
                        "dominant_pattern": "liquidity_sweep_reversal",
                        "session_quality": 92,
                        "scores": {"ict_liquidity": 91, "trend_following": 57, "mean_reversion": 74},
                        "selected_strategy": "ict_liquidity",
                        "affects_production": False,
                    },
                    "NAS100": {
                        "dominant_pattern": "trend_continuation",
                        "session_quality": 82,
                        "scores": {"ict_liquidity": 54, "trend_following": 86, "mean_reversion": 28},
                        "selected_strategy": "trend_following",
                        "affects_production": False,
                    },
                },
            },
        },
        "coach": {
            "summary": "Coach: Favor XAUUSD / New York Open. Autonomous execution not recommended.",
            "recommendations": [
                {"severity": "INFO", "category": "symbol", "message": "Favor XAUUSD over GBPUSD."},
                {"severity": "CRITICAL", "category": "execution", "message": "Do not enable autonomous execution yet."},
            ],
        },
        "symbol_registry": [
            {"symbol": "US30", "tier": "Production", "pf": 1.69, "wr": 60.0, "trades": 47, "dd": 2.0, "status": "ACTIVE"},
            {"symbol": "XAUUSD", "tier": "Filtered Production", "pf": 1.0, "wr": 50.0, "trades": 9, "dd": 0.99, "status": "WATCH"},
            {"symbol": "BTCUSD", "tier": "Demo Sandbox", "pf": 0.0, "wr": 0.0, "trades": 0, "dd": 0.0, "status": "DEMO_SANDBOX"},
            {"symbol": "NAS100", "tier": "Demo Sandbox", "pf": 0.0, "wr": 0.0, "trades": 0, "dd": 0.0, "status": "DEMO_SANDBOX"},
        ],
        "TELEGRAM_BOT_TOKEN": "secret-token",
    }


def make_bot(tmp_path: Path, monkeypatch) -> TelegramCommandBot:
    config_dir = tmp_path / "config"
    write_config(config_dir)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    return TelegramCommandBot(config_dir=config_dir, project_root=tmp_path, snapshot_provider=sample_snapshot)


def test_command_routing(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/help", "123")

    assert result == {
        "command": "/help",
        "authorized": True,
        "success": True,
        "response_text": result["response_text"],
        "error": None,
    }
    assert "/status" in result["response_text"]


def test_authorization(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/status", "999")

    assert result["authorized"] is False
    assert result["success"] is False
    assert result["response_text"] == "Unauthorized."


def test_ping(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/ping", "123")

    assert result["success"] is True
    assert result["response_text"] == "Sentinel online."


def test_status_formatting(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/status", "123")

    assert "Risk Status: ALLOWED" in result["response_text"]
    assert "News Status: LOCKED - CPI" in result["response_text"]
    assert "XAUUSD: HOT / 82 / REJECTED" in result["response_text"]
    assert "login" not in result["response_text"].lower()


def test_symbol_formatting(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/xauusd", "123")

    assert "<b>XAUUSD Snapshot</b>" in result["response_text"]
    assert "Killzone: New York Open" in result["response_text"]
    assert "SMT: bullish XAUUSD vs EURUSD" in result["response_text"]
    assert "Entry: 4000.0" in result["response_text"]
    assert "Rejection Reasons: MSS not confirmed" in result["response_text"]


def test_btc_command_works_and_execution_is_blocked(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/btcusd", "123")

    assert result["success"] is True
    assert "<b>BTCUSD (EXPERIMENTAL) Snapshot</b>" in result["response_text"]
    assert "Mode: DEMO_SANDBOX" in result["response_text"]
    assert "Observer State: OBSERVER_WARM" in result["response_text"]
    assert "SANDBOX DEMO ONLY. NOT PRODUCTION. NOT FUNDED. NOT CHALLENGE." in result["response_text"]
    assert "Rejection Reasons: BTCUSD demo sandbox: production execution disabled" in result["response_text"]


def test_nas100_command_works_and_execution_is_blocked(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/nas100", "123")

    assert result["success"] is True
    assert "<b>NAS100 (OBSERVER) Snapshot</b>" in result["response_text"]
    assert "Tier: Demo Sandbox" in result["response_text"]
    assert "Mode: DEMO_SANDBOX" in result["response_text"]
    assert "Observer State: OBSERVER_WARM" in result["response_text"]
    assert "SANDBOX DEMO ONLY. NOT PRODUCTION. NOT FUNDED. NOT CHALLENGE." in result["response_text"]
    assert "Rejection Reasons: NAS100 demo sandbox: production execution disabled" in result["response_text"]


def test_symbols_command_formats_registry(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/symbols", "123")

    assert result["success"] is True
    assert "<b>Symbol Registry</b>" in result["response_text"]
    assert "<b>NAS100</b>" in result["response_text"]
    assert "Tier: Demo Sandbox" in result["response_text"]


def test_validation_command_formats_checkpoint(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/validation", "123")

    assert result["success"] is True
    assert "<b>SENTINEL VALIDATION CHECKPOINT</b>" in result["response_text"]
    assert "PF: 1.58" in result["response_text"]
    assert "Non-invasive: TRUE" in result["response_text"]
    assert "NAS100/USTEC: GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES" in result["response_text"]
    assert "PASS" in result["response_text"]


def test_market_watch_command_formats_advisory_summary(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/market_watch us30", "123")

    assert result["success"] is True
    assert "<b>SENTINEL MARKET WATCH</b>" in result["response_text"]
    assert "<b>US30</b>" in result["response_text"]
    assert "Pattern: Liquidity Sweep Reversal" in result["response_text"]
    assert "ICT: 91" in result["response_text"]
    assert "Advisory Only" in result["response_text"]
    assert "Production Impact:" in result["response_text"]
    assert "False" in result["response_text"]
    assert "Recommendation: Keep advisory only" in result["response_text"]


def test_positions_empty(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/positions", "123")

    assert result["response_text"] == "No open Sentinel positions or pending orders."


def test_plans_mocked(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/plans", "123")

    assert "<b>Trade Plans</b>" in result["response_text"]
    assert "XAUUSD" in result["response_text"]
    assert "Plan Quality: diagnostic_only" in result["response_text"]
    assert "Execution Allowed: False" in result["response_text"]


def test_journal_mocked(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/journal", "123")

    assert "<b>Last Journal Records</b>" in result["response_text"]
    assert "2026-06-28T14:30:00+01:00" in result["response_text"]
    assert "Top Rejection: MSS not confirmed" in result["response_text"]


def test_backtest_fallback(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/backtest", "123")

    assert result["response_text"] == "No backtest summary available. Run backtest first."


def test_live_stats_formatting(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/live_stats", "123")

    assert result["success"] is True
    assert "Live Data Stats" in result["response_text"]
    assert "XAUUSD:" in result["response_text"]
    assert "Warm: 2" in result["response_text"]
    assert "Hot: 1" in result["response_text"]
    assert "BTCUSD:" in result["response_text"]
    assert "Mode: DEMO_SANDBOX" in result["response_text"]


def test_stress_formatting(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/stress", "123")

    assert result["success"] is True
    assert "Monte Carlo Stress Test" in result["response_text"]
    assert "Safe Risk: 0.5%" in result["response_text"]
    assert "95% DD: 3.8%" in result["response_text"]
    assert "Worst Losing Streak: 8" in result["response_text"]
    assert "4% Breach Probability: 3.0%" in result["response_text"]
    assert "Autonomous Mode: NOT RECOMMENDED" in result["response_text"]


def test_readiness_formatting(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/readiness", "123")

    assert result["success"] is True
    assert "<b>Readiness</b>" in result["response_text"]
    assert "Status: BLOCKED" in result["response_text"]
    assert "Failed Checks: 2" in result["response_text"]
    assert "News lock active" in result["response_text"]


def test_backtest_cached_summary_formatting(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    text = bot.format_backtest(
        {
            "available": True,
            "data": {
                "generated_at": "2026-06-28T00:00:00+00:00",
                "adaptive_guardrails": True,
                "days_30": {"pf": 2.0, "win_rate": 63.64, "trades": 13, "max_drawdown": 0.99, "net_rr": 4.0},
                "days_90": {"pf": 1.75, "win_rate": 61.29, "trades": 45, "max_drawdown": 1.0, "net_rr": 9.0},
                "phase_decision": "Phase 3 Qualified: Execution Automation Research",
            },
        }
    )

    assert "<b>Backtest Summary</b>" in text
    assert "30D:" in text
    assert "PF: 2.0" in text
    assert "WR: 61.29%" in text
    assert "Trades: 45" in text
    assert "Phase 3 Qualified" in text


def test_settings_safe_output(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/settings", "123")

    assert "execution_mode: advisor" in result["response_text"]
    assert "telegram enabled: True" in result["response_text"]
    assert "secret-token" not in result["response_text"]
    assert "chat_id" not in result["response_text"]


def test_unknown_command(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/execute", "123")

    assert result["authorized"] is True
    assert result["success"] is False
    assert result["error"] == "UNKNOWN_COMMAND"
    assert "Unknown command" in result["response_text"]


def test_no_credential_leakage(tmp_path: Path, monkeypatch):
    bot = make_bot(tmp_path, monkeypatch)

    result = bot.handle_command("/status", "123")
    coach = bot.handle_command("/coach", "123")
    payload = json.dumps([result, coach])

    assert "secret-token" not in payload
    assert "TELEGRAM_BOT_TOKEN" not in payload
    assert "token" not in payload
    assert "123456" not in payload
