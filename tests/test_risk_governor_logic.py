from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.risk_manager.risk_governor import RiskGovernor


class FakeConnector:
    def __init__(self, account_info: dict):
        self.account_info = account_info

    def get_account_info(self) -> dict:
        return self.account_info


def write_configs(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "risk_profile.yaml").write_text(
        """
drawdown:
  firm_max_drawdown_percent: 6.0
  internal_max_drawdown_percent: 4.0
daily_limits:
  max_trades_per_day: 2
  daily_loss_limit_percent: 1.0
  stop_after_consecutive_losses: 2
  cooldown_after_stop_loss_minutes: 15
risk_per_trade:
  week_1_percent: 0.5
  week_2_percent_if_profitable: 1.0
  forex_development_percent: 0.25
portfolio_limits:
  max_total_open_planned_risk_percent: 1.0
""",
        encoding="utf-8",
    )
    (config_dir / "trading_rules.yaml").write_text(
        """
markets:
  allowed:
    - "XAUUSD"
    - "US30"
    - "EURUSD"
    - "GBPUSD"
  forex:
    symbols:
      - "EURUSD"
      - "GBPUSD"
""",
        encoding="utf-8",
    )


def make_governor(
    tmp_path: Path,
    balance: float = 5000.0,
    equity: float = 5000.0,
    environment_mode: str = "development",
    account_mode: str = "demo",
) -> RiskGovernor:
    config_dir = tmp_path / "config"
    write_configs(config_dir)
    connector = FakeConnector(
        {
            "login": 123456,
            "server": "Demo-Server",
            "currency": "USD",
            "balance": balance,
            "equity": equity,
            "profit": equity - balance,
        }
    )
    return RiskGovernor(
        connector=connector,
        config_dir=config_dir,
        environment_mode=environment_mode,
        account_mode=account_mode,
    )


def safe_state() -> dict:
    return {
        "daily_loss_percent": 0.0,
        "trades_taken_today": 0,
        "consecutive_losses": 0,
        "analysis_time": datetime(2026, 6, 26, 9, 30, tzinfo=ZoneInfo("Africa/Lagos")),
    }


def test_risk_amount_calculation():
    assert RiskGovernor.calculate_risk_amount(5000.0, 0.5) == 25.0
    assert RiskGovernor.calculate_risk_amount(5125.0, 1.0) == 51.25


def test_drawdown_calculation():
    assert RiskGovernor.calculate_drawdown_percent(5000.0, 4800.0) == 4.0
    assert RiskGovernor.calculate_drawdown_percent(5000.0, 5100.0) == 0.0


def test_internal_drawdown_block(tmp_path: Path):
    governor = make_governor(tmp_path, balance=5000.0, equity=4800.0)
    governor.peak_equity = 5000.0

    result = governor.evaluate(safe_state())

    assert result["permission"]["trade_allowed"] is False
    assert result["permission"]["status"] == "BLOCKED"
    assert "Internal drawdown limit hit" in result["permission"]["block_reasons"]


def test_firm_drawdown_block(tmp_path: Path):
    governor = make_governor(tmp_path, balance=5000.0, equity=4700.0)
    governor.peak_equity = 5000.0

    result = governor.evaluate(safe_state())

    assert result["permission"]["trade_allowed"] is False
    assert result["permission"]["status"] == "HARD_BLOCKED"
    assert "Firm drawdown limit hit" in result["permission"]["block_reasons"]


def test_max_trades_block(tmp_path: Path):
    governor = make_governor(tmp_path)
    state = {**safe_state(), "trades_taken_today": 2}

    result = governor.evaluate(state)

    assert "Max trades per day hit" in result["permission"]["block_reasons"]


def test_consecutive_losses_block(tmp_path: Path):
    governor = make_governor(tmp_path)
    state = {**safe_state(), "consecutive_losses": 2}

    result = governor.evaluate(state)

    assert "Max consecutive losses hit" in result["permission"]["block_reasons"]


def test_cooldown_block(tmp_path: Path):
    governor = make_governor(tmp_path)
    state = {
        **safe_state(),
        "analysis_time": datetime(2026, 6, 26, 10, 10, tzinfo=ZoneInfo("Africa/Lagos")),
        "last_loss_time": datetime(2026, 6, 26, 10, 0, tzinfo=ZoneInfo("Africa/Lagos")),
    }

    result = governor.evaluate(state)

    assert result["limits"]["cooldown_active"] is True
    assert "Cooldown after loss active" in result["permission"]["block_reasons"]


def test_daily_loss_history_unavailable_warns_in_development(tmp_path: Path):
    governor = make_governor(tmp_path)
    state = {"trades_taken_today": 0, "consecutive_losses": 0}

    result = governor.evaluate(state)

    assert result["permission"]["trade_allowed"] is True
    assert "Daily loss history unavailable" not in result["permission"]["block_reasons"]
    assert "Daily loss history unavailable" in result["permission"]["warnings"]
    assert result["environment"] == "development"


def test_daily_loss_history_unavailable_hard_blocks_in_production(tmp_path: Path):
    governor = make_governor(tmp_path, environment_mode="production")
    state = {"trades_taken_today": 0, "consecutive_losses": 0}

    result = governor.evaluate(state)

    assert result["permission"]["trade_allowed"] is False
    assert result["permission"]["status"] == "HARD_BLOCKED"
    assert "Daily loss history unavailable" in result["permission"]["block_reasons"]
    assert result["permission"]["warnings"] == []
    assert result["environment"] == "production"


def test_sentinel_env_controls_environment_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SENTINEL_ENV", "production")
    config_dir = tmp_path / "config"
    write_configs(config_dir)
    connector = FakeConnector(
        {
            "login": 123456,
            "server": "Demo-Server",
            "currency": "USD",
            "balance": 5000.0,
            "equity": 5000.0,
            "profit": 0.0,
        }
    )

    governor = RiskGovernor(connector=connector, config_dir=config_dir)
    result = governor.evaluate({"trades_taken_today": 0, "consecutive_losses": 0})

    assert result["environment"] == "production"
    assert result["permission"]["status"] == "HARD_BLOCKED"


def test_trade_allowed_when_all_limits_clear(tmp_path: Path):
    governor = make_governor(tmp_path)

    result = governor.evaluate(safe_state())

    assert result["permission"]["trade_allowed"] is True
    assert result["permission"]["status"] == "ALLOWED"
    assert result["risk"]["risk_amount"] == 25.0


def test_forex_risk_percent_uses_quarter_percent(tmp_path: Path):
    governor = make_governor(tmp_path)

    result = governor.evaluate({**safe_state(), "symbol": "EURUSD"})

    assert result["risk"]["risk_percent"] == 0.25
    assert result["risk"]["risk_amount"] == 12.5


def test_total_open_planned_risk_block(tmp_path: Path):
    governor = make_governor(tmp_path)

    result = governor.evaluate({**safe_state(), "total_open_planned_risk_percent": 1.1})

    assert result["permission"]["trade_allowed"] is False
    assert "Max total open planned risk exceeded" in result["permission"]["block_reasons"]
    assert result["risk"]["max_total_open_planned_risk_percent"] == 1.0


def test_account_mode_and_size_reference_are_reported(tmp_path: Path):
    governor = make_governor(tmp_path, account_mode="funded")

    result = governor.evaluate(safe_state())

    assert result["account_mode"] == "funded"
    assert result["account_size_reference"] == "live_mt5"
    assert result["account"]["account_mode"] == "funded"
    assert result["account"]["account_size_reference"] == "live_mt5"


def test_live_account_balance_and_equity_are_sourced_from_connector(tmp_path: Path):
    governor = make_governor(tmp_path, balance=7321.45, equity=7288.12)

    result = governor.evaluate(safe_state())

    assert result["account"]["balance"] == 7321.45
    assert result["account"]["equity"] == 7288.12
    assert result["risk"]["risk_amount"] == 36.44
