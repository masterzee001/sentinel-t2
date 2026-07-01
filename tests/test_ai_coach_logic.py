from __future__ import annotations

import json

from backend.ai_coach.coach_analyzer import AICoachAnalyzer


def make_analyzer(tmp_path) -> AICoachAnalyzer:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "ai_coach.yaml").write_text(
        "enabled: true\nmode: advisor\njournal_path: data/journal/sentinel_decisions.jsonl\n",
        encoding="utf-8",
    )
    return AICoachAnalyzer(config_dir=config_dir, project_root=tmp_path)


def sample_backtest_summary():
    return {
        "overall": {"profit_factor": 1.75, "win_rate": 61.29, "trades_approved": 45, "max_drawdown": 1.0},
        "after_guardrails": {"profit_factor": 1.75, "win_rate": 61.29, "trades_approved": 45, "max_drawdown": 1.0},
        "by_symbol": {
            "XAUUSD": {"profit_factor": 2.0, "win_rate": 65.0, "trades_approved": 20, "average_rr": 0.5},
            "GBPUSD": {"profit_factor": 0.7, "win_rate": 35.0, "trades_approved": 10, "average_rr": -0.2},
        },
        "by_killzone": {
            "new_york_open": {"profit_factor": 2.1, "win_rate": 68.0, "trades_approved": 18, "average_rr": 0.45},
            "london_continuation": {"profit_factor": 0.8, "win_rate": 38.0, "trades_approved": 8, "average_rr": -0.1},
        },
        "by_confidence_band": {
            "EXECUTION_READY": {"profit_factor": 2.0, "win_rate": 66.0, "trades_approved": 20, "average_rr": 0.4},
            "HOT": {"profit_factor": 1.0, "win_rate": 42.0, "trades_approved": 12, "average_rr": 0.0},
        },
        "guardrail_impact": {
            "before": {"trades": 38, "winrate": 55.0, "profit_factor": 1.2},
            "after": {"trades": 30, "winrate": 61.29, "profit_factor": 1.75},
            "trades_removed": 8,
        },
    }


def test_empty_journal_fallback(tmp_path):
    analyzer = make_analyzer(tmp_path)

    report = analyzer.analyze(backtest_summary=sample_backtest_summary())

    assert report["coach_status"] == "READY"
    assert "Coach:" in report["summary"]
    assert report["recommendations"]


def test_best_and_worst_symbol_detection(tmp_path):
    analyzer = make_analyzer(tmp_path)
    analysis = analyzer.analyze_backtest(sample_backtest_summary())

    assert analysis["best_symbol"]["name"] == "XAUUSD"
    assert analysis["worst_symbol"]["name"] == "GBPUSD"


def test_recommendation_generation(tmp_path):
    analyzer = make_analyzer(tmp_path)

    report = analyzer.analyze(
        journal_records=AICoachAnalyzer.synthetic_journal_records(),
        backtest_summary=sample_backtest_summary(),
    )
    messages = [item["message"] for item in report["recommendations"]]

    assert any("Favor XAUUSD over GBPUSD" in message for message in messages)
    assert any("New York Open" in message for message in messages)
    assert any("Do not enable autonomous execution" in message for message in messages)


def test_severity_classification(tmp_path):
    analyzer = make_analyzer(tmp_path)

    report = analyzer.analyze(
        journal_records=AICoachAnalyzer.synthetic_journal_records(),
        backtest_summary=sample_backtest_summary(),
    )
    severities = {item["severity"] for item in report["recommendations"]}

    assert "CRITICAL" in severities
    assert all(item["severity"] in AICoachAnalyzer.SEVERITIES for item in report["recommendations"])
    assert all(item["category"] in AICoachAnalyzer.CATEGORIES for item in report["recommendations"])


def test_no_credential_leakage(tmp_path):
    analyzer = make_analyzer(tmp_path)
    records = [
        {
            "symbol": "XAUUSD",
            "state": "EXECUTION_READY",
            "password": "super-secret-password",
            "api_key": "secret-api-key",
            "account": {"mt5_login": "123456", "server": "broker"},
            "news": {"lock_active": False},
            "risk": {"status": "OK", "warnings": [], "block_reasons": []},
            "guardrail": {"status": "PASS", "reasons": [], "warnings": []},
            "trade_plan": {"execution_allowed": True},
        }
    ]
    backtest = sample_backtest_summary()
    backtest["credentials"] = {"token": "secret-token"}

    report = analyzer.analyze(journal_records=records, backtest_summary=backtest)
    text = json.dumps(report)

    assert "super-secret-password" not in text
    assert "secret-api-key" not in text
    assert "secret-token" not in text
    assert "mt5_login" not in text

