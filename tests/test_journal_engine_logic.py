from __future__ import annotations

from pathlib import Path

from backend.journal.journal_engine import JournalEngine


def write_journal_config(config_dir: Path, enabled: bool = True) -> None:
    config_dir.mkdir()
    (config_dir / "journal.yaml").write_text(
        f"""
enabled: {str(enabled).lower()}
storage_type: jsonl
path: data/journal/sentinel_decisions.jsonl
record_diagnostic_plans: true
record_rejected_setups: true
""",
        encoding="utf-8",
    )


def make_journal(tmp_path: Path, enabled: bool = True) -> JournalEngine:
    config_dir = tmp_path / "config"
    write_journal_config(config_dir, enabled=enabled)
    return JournalEngine(config_dir=config_dir, project_root=tmp_path)


def sample_record(journal: JournalEngine, symbol: str = "XAUUSD", decision: str = "REJECTED") -> dict:
    return journal.build_record(
        environment="development",
        risk={
            "account": {
                "login": 123,
                "server": "Demo",
                "account_mode": "demo",
                "balance": 2000.0,
                "equity": 2000.0,
                "currency": "USD",
            },
            "risk": {"risk_amount": 10.0},
            "permission": {"status": "ALLOWED", "warnings": [], "block_reasons": []},
        },
        news={"enabled": True, "lock_active": False, "event_name": None, "reason": ""},
        symbol=symbol,
        trend={"daily_bias": "bearish", "h4_bias": "range", "h1_context": "consolidation"},
        ict={
            "mss": {"detected": False, "direction": "bearish"},
            "fvg": {"detected": True, "direction": "bullish", "grade": "B"},
            "order_block": {"detected": True},
        },
        killzone={
            "active_killzone": "london_open",
            "quality_score": 10,
            "commentary": "London open raid window.",
        },
        confidence={
            "confidence_band": "WARM",
            "total_confidence": 51,
            "decision": decision,
            "recommended_action": "Monitor",
            "rejection_reasons": ["MSS not confirmed"],
            "guardrail_status": "BLOCKED",
            "guardrail_reasons": ["GBPUSD disabled by strategy guardrail"],
            "guardrail": {
                "status": "BLOCKED",
                "reasons": ["GBPUSD disabled by strategy guardrail"],
                "warnings": ["Distribution phase without SMT confirmation"],
            },
            "smt": {
                "smt_detected": True,
                "pair_name": "XAUUSD_EURUSD",
                "direction": "bearish",
                "confidence": 7,
            },
        },
        trade_plan={
            "plan_quality": "diagnostic_only",
            "execution_allowed": False,
            "direction": "bearish",
            "entry": {"price": 4079.61},
            "stop_loss": {"price": 4045.22},
            "take_profit": {"tp1": 4006.34, "tp2": 3989.93, "tp3": 3963.03},
            "risk": {"lot_size": 0.02, "rr_to_tp1": 2.13, "rr_to_tp2": 2.61, "rr_to_tp3": 3.39},
        },
        commentary="Narrative forming. Watch liquidity.",
    )


def test_appending_record(tmp_path: Path):
    journal = make_journal(tmp_path)

    written = journal.append_record(sample_record(journal))

    assert written is True
    assert journal.count_records() == 1
    assert journal.journal_path.exists()


def test_creates_journal_directory(tmp_path: Path):
    journal = make_journal(tmp_path)

    journal.append_record(sample_record(journal))

    assert (tmp_path / "data" / "journal").is_dir()


def test_disabling_journal_via_config(tmp_path: Path):
    journal = make_journal(tmp_path, enabled=False)

    written = journal.append_record(sample_record(journal))

    assert written is False
    assert journal.count_records() == 0
    assert not journal.journal_path.exists()


def test_preventing_credential_fields(tmp_path: Path):
    journal = make_journal(tmp_path)
    record = sample_record(journal)
    record["password"] = "never-store"
    record["nested"] = {"api_key": "never-store", "safe": "ok"}

    journal.append_record(record)
    last = journal.read_last_records(1)[0]

    assert "password" not in last
    assert "api_key" not in last["nested"]
    assert last["nested"]["safe"] == "ok"


def test_reading_last_records(tmp_path: Path):
    journal = make_journal(tmp_path)
    journal.append_record(sample_record(journal, symbol="XAUUSD"))
    journal.append_record(sample_record(journal, symbol="US30"))
    journal.append_record(sample_record(journal, symbol="EURUSD"))

    records = journal.read_last_records(2)

    assert [record["symbol"] for record in records] == ["US30", "EURUSD"]


def test_record_includes_killzone_context(tmp_path: Path):
    journal = make_journal(tmp_path)
    record = sample_record(journal)

    assert record["killzone"] == {
        "active_killzone": "london_open",
        "killzone_quality": 10,
        "killzone_commentary": "London open raid window.",
    }


def test_record_includes_smt_context(tmp_path: Path):
    journal = make_journal(tmp_path)
    record = sample_record(journal)

    assert record["smt"] == {
        "smt_detected": True,
        "smt_pair": "XAUUSD_EURUSD",
        "smt_direction": "bearish",
        "smt_confidence": 7,
    }


def test_record_includes_guardrail_context(tmp_path: Path):
    journal = make_journal(tmp_path)
    record = sample_record(journal)

    assert record["guardrail"] == {
        "status": "BLOCKED",
        "reasons": ["GBPUSD disabled by strategy guardrail"],
        "warnings": ["Distribution phase without SMT confirmation"],
    }
