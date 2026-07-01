from __future__ import annotations

from pathlib import Path

from backend.emergency_live.emergency_live_protocol import EmergencyLiveProtocol, emergency_ready


def write_config(config_dir: Path) -> None:
    config_dir.mkdir(exist_ok=True)
    (config_dir / "emergency_live.yaml").write_text(
        """
enabled: true
risk_percent: 0.1
max_risk_percent: 0.25
allowed_symbols:
  - US30
  - XAUUSD
allowed_grades:
  - A+
human_approval_required: true
max_trades_per_day: 2
kill_switch:
  daily_loss_r: -1
  consecutive_losses: 3
  max_drawdown_percent: 2
""",
        encoding="utf-8",
    )


def valid_proposal() -> dict:
    return {
        "symbol": "XAUUSD",
        "strategy": "trend_following",
        "quality_grade": "A+",
        "regime": "institutional_continuation",
        "entry": 4010.0,
        "sl": 4000.0,
        "tp": 4030.0,
        "risk_percent": 0.1,
        "expected_pf": 2.84,
        "expected_wr": 72.6,
    }


def test_emergency_config_loading(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)

    assert protocol.config["enabled"] is True
    assert protocol.config["risk_percent"] == 0.1
    assert protocol.config["max_risk_percent"] == 0.25


def test_symbol_lock_blocks_observer_symbols(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)
    result = protocol.validate_proposal({**valid_proposal(), "symbol": "BTCUSD"})

    assert result["valid"] is False
    assert "Symbol not allowed for emergency live" in result["reasons"]


def test_nas100_cannot_enter_approval_queue(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)

    request = protocol.create_approval_request({**valid_proposal(), "symbol": "NAS100"})

    assert request["status"] == "BLOCKED"
    assert request["queue_action"] == "BLOCKED_NOT_QUEUED"
    assert "Symbol not allowed for emergency live" in request["validation"]["reasons"]
    assert protocol.approval_queue == []


def test_xauusd_and_us30_a_plus_proposals_enter_approval_queue(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)

    xau = protocol.create_approval_request({**valid_proposal(), "symbol": "XAUUSD"})
    us30 = protocol.create_approval_request({**valid_proposal(), "symbol": "US30"})

    assert xau["status"] == "PENDING"
    assert us30["status"] == "PENDING"
    assert [item["proposal"]["symbol"] for item in protocol.approval_queue] == ["XAUUSD", "US30"]


def test_a_plus_grade_enforcement(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)
    result = protocol.validate_proposal({**valid_proposal(), "quality_grade": "A"})

    assert result["valid"] is False
    assert "Only A+ setups are allowed" in result["reasons"]


def test_risk_cap_enforcement(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)
    result = protocol.validate_proposal({**valid_proposal(), "risk_percent": 0.5})

    assert result["valid"] is False
    assert "Risk exceeds emergency maximum" in result["reasons"]


def test_approval_workflow_never_allows_broker_submission(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)
    request = protocol.create_approval_request(valid_proposal())
    assert request["status"] == "PENDING"
    approved = protocol.approve_trade(request["approval_id"])
    rejected = protocol.reject_trade(request["approval_id"])

    assert approved["status"] == "APPROVED"
    assert approved["broker_order_submission_allowed"] is False
    assert rejected["status"] == "REJECTED"


def test_kill_switch_halts_on_daily_loss(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)
    result = protocol.evaluate_kill_switch({"daily_r": -1.2})

    assert result["triggered"] is True
    assert result["status"] == "LIVE_HALTED"
    assert "Daily loss <= -1R" in result["reasons"]


def test_live_halt_resume_requires_manual_override(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)
    protocol.halt_live("test halt")

    blocked = protocol.resume_live(manual_override=False)
    resumed = protocol.resume_live(manual_override=True)

    assert blocked["resumed"] is False
    assert resumed["resumed"] is True
    assert protocol.status == "LIVE_READY"


def test_status_report_is_emergency_ready(tmp_path: Path):
    write_config(tmp_path)
    protocol = EmergencyLiveProtocol(config_dir=tmp_path)
    status = protocol.status_report()

    assert status["risk_lock"]["locked"] is True
    assert status["grade_lock"]["locked"] is True
    assert status["symbol_lock"]["locked"] is True
    assert emergency_ready(status) is True
