"""Prop firm risk protection layer for Project Sentinel Advisor Mode."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from loguru import logger

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.risk_manager.risk_state_store import RiskStateStore


class RiskGovernorError(RuntimeError):
    """Raised when risk governance cannot be completed."""


class RiskGovernor:
    """Apply prop firm risk limits before any setup can be considered tradable."""

    DEFAULT_RISK_PROFILE = {
        "drawdown": {
            "firm_max_drawdown_percent": 6.0,
            "internal_max_drawdown_percent": 4.0,
        },
        "daily_limits": {
            "max_trades_per_day": 2,
            "daily_loss_limit_percent": 1.0,
            "stop_after_consecutive_losses": 2,
            "cooldown_after_stop_loss_minutes": 15,
        },
        "risk_per_trade": {
            "week_1_percent": 0.5,
            "week_2_percent_if_profitable": 1.0,
            "forex_development_percent": 0.25,
        },
        "portfolio_limits": {
            "max_total_open_planned_risk_percent": 1.0,
        },
    }
    DEFAULT_TRADING_RULES = {
        "markets": {
            "allowed": ["XAUUSD", "US30", "EURUSD", "GBPUSD"],
            "forex": {"symbols": ["EURUSD", "GBPUSD"]},
        }
    }
    WAT_TIMEZONE = ZoneInfo("Africa/Lagos")

    def __init__(
        self,
        connector: MT5Connector | None = None,
        config_dir: str | Path | None = None,
        mode: str = "balanced",
        environment_mode: str | None = None,
        account_mode: str | None = None,
        state_store: RiskStateStore | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.connector = connector or MT5Connector()
        self.mode = mode
        self.risk_profile = self._load_risk_profile()
        self.trading_rules = self._load_trading_rules()
        self.environment_mode = self.normalize_environment_mode(
            environment_mode
            or os.getenv("SENTINEL_ENV")
            or os.getenv("SENTINEL_ENVIRONMENT_MODE")
            or str(self.risk_profile.get("environment", "development"))
        )
        self.account_mode = self.normalize_account_mode(
            account_mode or os.getenv("SENTINEL_ACCOUNT_MODE") or str(self.risk_profile.get("account_mode", "demo"))
        )
        self.starting_balance: float | None = None
        self.peak_equity: float | None = None
        self.state_store = state_store

    def evaluate(self, runtime_state: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return risk permission status using live MT5 account information."""
        state = dict(runtime_state or {})
        logger.info("Starting Risk Governor evaluation.")

        try:
            raw_account = self.connector.get_account_info()
        except MT5ConnectorError as exc:
            raise RiskGovernorError(f"Could not read MT5 account information: {exc}") from exc

        account = self.normalize_account_info(raw_account)
        balance = account["balance"]
        equity = account["equity"]
        profit = account["profit"]

        if self.state_store is not None:
            # Persisted counters fill the runtime state; explicit caller keys win.
            persisted = self.state_store.observe_account(balance=balance, equity=equity)
            state = {**self.state_store.runtime_state(), **state}
            self.starting_balance = float(persisted["starting_balance"])
            self.peak_equity = float(persisted["peak_equity"])
        else:
            if self.starting_balance is None:
                self.starting_balance = balance
            self.peak_equity = max(self.peak_equity or equity, equity)

        risk_percent = self.select_risk_percent(
            balance=balance,
            starting_balance=self.starting_balance,
            state=state,
        )
        risk_amount = self.calculate_risk_amount(equity, risk_percent)
        current_drawdown = self.calculate_drawdown_percent(self.peak_equity, equity)
        daily_loss_percent, daily_loss_verified = self.get_daily_loss_percent(state)
        limits = self.get_limits(state)

        block_reasons = self.evaluate_blocks(
            daily_loss_percent=daily_loss_percent,
            daily_loss_verified=daily_loss_verified,
            current_drawdown_percent=current_drawdown,
            trades_taken_today=limits["trades_taken_today"],
            consecutive_losses=limits["consecutive_losses"],
            cooldown_active=limits["cooldown_active"],
            total_open_planned_risk_percent=float(state.get("total_open_planned_risk_percent", 0.0) or 0.0),
        )
        warnings = self.evaluate_warnings(daily_loss_verified)
        status = self.permission_status(block_reasons, current_drawdown)

        result = {
            "environment": self.environment_mode,
            "account_mode": self.account_mode,
            "account_size_reference": "live_mt5",
            "account": {
                "login": account["login"],
                "server": account["server"],
                "currency": account["currency"],
                "balance": balance,
                "equity": equity,
                "profit": profit,
                "account_mode": self.account_mode,
                "account_size_reference": "live_mt5",
            },
            "risk": {
                "risk_percent": risk_percent,
                "risk_amount": risk_amount,
                "daily_loss_percent": daily_loss_percent,
                "current_drawdown_percent": current_drawdown,
                "internal_drawdown_limit": self.internal_drawdown_limit,
                "firm_drawdown_limit": self.firm_drawdown_limit,
                "total_open_planned_risk_percent": float(state.get("total_open_planned_risk_percent", 0.0) or 0.0),
                "max_total_open_planned_risk_percent": self.max_total_open_planned_risk_percent,
            },
            "limits": limits,
            "permission": {
                "trade_allowed": not block_reasons,
                "status": status,
                "block_reasons": block_reasons,
                "warnings": warnings,
            },
            "explanation": self.build_explanation(
                account=account,
                environment_mode=self.environment_mode,
                risk_percent=risk_percent,
                risk_amount=risk_amount,
                daily_loss_verified=daily_loss_verified,
                current_drawdown=current_drawdown,
                status=status,
                block_reasons=block_reasons,
            ),
        }
        logger.info("Completed Risk Governor evaluation: {}", result)
        return result

    def evaluate_blocks(
        self,
        daily_loss_percent: float,
        daily_loss_verified: bool,
        current_drawdown_percent: float,
        trades_taken_today: int,
        consecutive_losses: int,
        cooldown_active: bool,
        total_open_planned_risk_percent: float = 0.0,
    ) -> list[str]:
        """Return all active risk block reasons."""
        reasons: list[str] = []

        if not daily_loss_verified:
            if self.environment_mode == "production":
                reasons.append("Daily loss history unavailable")
        elif daily_loss_percent >= self.daily_loss_limit:
            reasons.append("Daily loss limit hit")

        if self.is_internal_drawdown_blocked(current_drawdown_percent, self.internal_drawdown_limit):
            reasons.append("Internal drawdown limit hit")
        if self.is_firm_drawdown_blocked(current_drawdown_percent, self.firm_drawdown_limit):
            reasons.append("Firm drawdown limit hit")
        if self.is_max_trades_blocked(trades_taken_today, self.max_trades_per_day):
            reasons.append("Max trades per day hit")
        if self.is_consecutive_losses_blocked(consecutive_losses, self.max_consecutive_losses):
            reasons.append("Max consecutive losses hit")
        if cooldown_active:
            reasons.append("Cooldown after loss active")
        if total_open_planned_risk_percent > self.max_total_open_planned_risk_percent:
            reasons.append("Max total open planned risk exceeded")

        return self.dedupe_reasons(reasons)

    def evaluate_warnings(self, daily_loss_verified: bool) -> list[str]:
        """Return non-blocking risk warnings."""
        warnings: list[str] = []
        if not daily_loss_verified and self.environment_mode == "development":
            warnings.append("Daily loss history unavailable")
        return warnings

    @staticmethod
    def runtime_state_from_result(result: dict[str, Any]) -> dict[str, Any]:
        """Translate an evaluate() result back into runtime-state input keys.

        Callers that hold a governor result and need to re-evaluate must use
        this instead of passing the result dict itself: the result and the
        runtime-state input have disjoint shapes, so passing the result
        silently zeroes every limit.
        """
        limits = result.get("limits", {}) or {}
        risk_info = result.get("risk", {}) or {}
        permission = result.get("permission", {}) or {}
        unavailable = "Daily loss history unavailable"
        state: dict[str, Any] = {
            "trades_taken_today": int(limits.get("trades_taken_today", 0) or 0),
            "consecutive_losses": int(limits.get("consecutive_losses", 0) or 0),
            "total_open_planned_risk_percent": float(
                risk_info.get("total_open_planned_risk_percent", 0.0) or 0.0
            ),
        }
        daily_loss_unverified = unavailable in (permission.get("warnings") or []) or unavailable in (
            permission.get("block_reasons") or []
        )
        if not daily_loss_unverified:
            state["daily_loss_percent"] = float(risk_info.get("daily_loss_percent", 0.0) or 0.0)
        return state

    @staticmethod
    def decision_context_from_result(result: dict[str, Any]) -> dict[str, Any]:
        """Return confidence-context risk flags derived from an evaluate() result."""
        permission = result.get("permission", {}) or {}
        reasons = permission.get("block_reasons", []) or []
        return {
            "daily_loss_limit_hit": "Daily loss limit hit" in reasons,
            "max_trades_per_day_hit": "Max trades per day hit" in reasons,
            "risk_blocked": not bool(permission.get("trade_allowed", True)),
        }

    def get_daily_loss_percent(self, state: dict[str, Any]) -> tuple[float, bool]:
        """Return daily loss percent and whether it was verified from state/history.

        TODO: Replace the state fallback with MT5 trade-history/journal integration.
        Until then, absence of verified daily loss data warns in development and blocks in production.
        """
        if "daily_loss_percent" in state:
            return max(float(state["daily_loss_percent"]), 0.0), True

        return 0.0, False

    def get_limits(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return daily operational limit status from runtime state."""
        now = self.parse_datetime(state.get("analysis_time")) or datetime.now(self.WAT_TIMEZONE)
        last_loss_time = self.parse_datetime(state.get("last_loss_time"))
        cooldown_active = self.is_cooldown_active(
            last_loss_time=last_loss_time,
            now=now,
            cooldown_minutes=self.cooldown_after_loss_minutes,
        )

        return {
            "max_trades_per_day": self.max_trades_per_day,
            "trades_taken_today": int(state.get("trades_taken_today", 0)),
            "max_consecutive_losses": self.max_consecutive_losses,
            "consecutive_losses": int(state.get("consecutive_losses", 0)),
            "cooldown_active": cooldown_active,
        }

    def select_risk_percent(
        self,
        balance: float,
        starting_balance: float,
        state: dict[str, Any],
    ) -> float:
        """Return active risk percent using Week 2 only when profitable and allowed."""
        risk_config = self.risk_profile.get("risk_per_trade", {})
        week_1_percent = float(risk_config.get("week_1_percent", 0.5))
        week_2_percent = float(risk_config.get("week_2_percent_if_profitable", 1.0))
        forex_percent = float(risk_config.get("forex_development_percent", 0.25))
        trading_week = int(state.get("trading_week", 1))
        week_2_allowed = bool(state.get("week_2_risk_allowed", False))
        account_is_profitable = balance > starting_balance
        symbol = str(state.get("symbol", "")).upper().strip()

        if self.is_forex_symbol(symbol):
            return forex_percent

        if trading_week >= 2 and week_2_allowed and account_is_profitable:
            return week_2_percent
        return week_1_percent

    def is_forex_symbol(self, symbol: str) -> bool:
        """Return whether a symbol is configured as forex watchlist."""
        forex_symbols = self.trading_rules.get("markets", {}).get("forex", {}).get("symbols", [])
        return symbol.upper().strip() in {str(item).upper().strip() for item in forex_symbols}

    @property
    def firm_drawdown_limit(self) -> float:
        """Return configured firm max drawdown percent."""
        return float(self.risk_profile.get("drawdown", {}).get("firm_max_drawdown_percent", 6.0))

    @property
    def internal_drawdown_limit(self) -> float:
        """Return configured internal max drawdown percent."""
        return float(self.risk_profile.get("drawdown", {}).get("internal_max_drawdown_percent", 4.0))

    @property
    def daily_loss_limit(self) -> float:
        """Return configured daily loss limit percent."""
        return float(self.risk_profile.get("daily_limits", {}).get("daily_loss_limit_percent", 1.0))

    @property
    def max_trades_per_day(self) -> int:
        """Return configured max trades per day."""
        return int(self.risk_profile.get("daily_limits", {}).get("max_trades_per_day", 2))

    @property
    def max_consecutive_losses(self) -> int:
        """Return configured max consecutive losses."""
        return int(self.risk_profile.get("daily_limits", {}).get("stop_after_consecutive_losses", 2))

    @property
    def cooldown_after_loss_minutes(self) -> int:
        """Return configured cooldown after stop loss in minutes."""
        return int(self.risk_profile.get("daily_limits", {}).get("cooldown_after_stop_loss_minutes", 15))

    @property
    def max_total_open_planned_risk_percent(self) -> float:
        """Return max total open planned risk across symbols."""
        return float(self.risk_profile.get("portfolio_limits", {}).get("max_total_open_planned_risk_percent", 1.0))

    @staticmethod
    def normalize_account_info(account_info: dict[str, Any]) -> dict[str, Any]:
        """Normalize MT5 account fields used by the governor."""
        return {
            "login": int(account_info.get("login", 0) or 0),
            "server": str(account_info.get("server", "") or ""),
            "currency": str(account_info.get("currency", "USD") or "USD"),
            "balance": round(float(account_info.get("balance", 0.0) or 0.0), 2),
            "equity": round(float(account_info.get("equity", 0.0) or 0.0), 2),
            "profit": round(float(account_info.get("profit", 0.0) or 0.0), 2),
        }

    @staticmethod
    def calculate_risk_amount(equity: float, risk_percent: float) -> float:
        """Return risk amount from equity and risk percent."""
        if equity < 0:
            raise ValueError("equity cannot be negative.")
        if risk_percent < 0:
            raise ValueError("risk_percent cannot be negative.")
        return round(equity * (risk_percent / 100), 2)

    @staticmethod
    def calculate_drawdown_percent(peak_equity: float, current_equity: float) -> float:
        """Return drawdown percent from runtime peak equity to current equity."""
        if peak_equity <= 0:
            return 0.0
        drawdown = ((peak_equity - current_equity) / peak_equity) * 100
        return round(max(drawdown, 0.0), 2)

    @staticmethod
    def is_internal_drawdown_blocked(current_drawdown_percent: float, limit: float) -> bool:
        """Return whether internal drawdown limit is breached."""
        return current_drawdown_percent >= limit

    @staticmethod
    def is_firm_drawdown_blocked(current_drawdown_percent: float, limit: float) -> bool:
        """Return whether firm drawdown limit is breached."""
        return current_drawdown_percent >= limit

    @staticmethod
    def is_max_trades_blocked(trades_taken_today: int, max_trades_per_day: int) -> bool:
        """Return whether the daily trade count limit is breached."""
        return trades_taken_today >= max_trades_per_day

    @staticmethod
    def is_consecutive_losses_blocked(consecutive_losses: int, max_consecutive_losses: int) -> bool:
        """Return whether consecutive loss limit is breached."""
        return consecutive_losses >= max_consecutive_losses

    @classmethod
    def is_cooldown_active(
        cls,
        last_loss_time: datetime | None,
        now: datetime,
        cooldown_minutes: int,
    ) -> bool:
        """Return whether the post-loss cooldown window is still active."""
        if last_loss_time is None:
            return False
        if last_loss_time.tzinfo is None:
            last_loss_time = last_loss_time.replace(tzinfo=cls.WAT_TIMEZONE)
        if now.tzinfo is None:
            now = now.replace(tzinfo=cls.WAT_TIMEZONE)
        return now.astimezone(cls.WAT_TIMEZONE) < (
            last_loss_time.astimezone(cls.WAT_TIMEZONE) + timedelta(minutes=cooldown_minutes)
        )

    def permission_status(self, block_reasons: list[str], current_drawdown_percent: float) -> str:
        """Return permission status from active blocks."""
        if self.is_firm_drawdown_blocked(current_drawdown_percent, self.firm_drawdown_limit):
            return "HARD_BLOCKED"
        if self.environment_mode == "production" and "Daily loss history unavailable" in block_reasons:
            return "HARD_BLOCKED"
        if block_reasons:
            return "BLOCKED"
        return "ALLOWED"

    @staticmethod
    def parse_datetime(value: Any) -> datetime | None:
        """Parse optional datetime values from runtime state."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise ValueError(f"Unsupported datetime value: {value!r}")

    @staticmethod
    def dedupe_reasons(reasons: list[str]) -> list[str]:
        """Preserve-order de-duplication for block reasons."""
        deduped: list[str] = []
        for reason in reasons:
            if reason and reason not in deduped:
                deduped.append(reason)
        return deduped

    @staticmethod
    def normalize_environment_mode(environment_mode: str) -> str:
        """Validate and normalize the risk governor environment mode."""
        normalized = environment_mode.lower().strip()
        if normalized not in {"development", "production"}:
            raise ValueError("environment_mode must be 'development' or 'production'.")
        return normalized

    @staticmethod
    def normalize_account_mode(account_mode: str) -> str:
        """Validate and normalize the account mode label."""
        normalized = account_mode.lower().strip()
        if normalized not in {"demo", "funded", "personal"}:
            raise ValueError("account_mode must be 'demo', 'funded', or 'personal'.")
        return normalized

    @staticmethod
    def build_explanation(
        account: dict[str, Any],
        environment_mode: str,
        risk_percent: float,
        risk_amount: float,
        daily_loss_verified: bool,
        current_drawdown: float,
        status: str,
        block_reasons: list[str],
    ) -> list[str]:
        """Build human-readable risk explanation lines."""
        explanation = [
            f"Live MT5 account balance is {account['balance']} {account['currency']} and equity is {account['equity']} {account['currency']}.",
            f"Risk Governor environment mode is {environment_mode}.",
            f"Risk per trade is {risk_percent}% of equity, equal to {risk_amount} {account['currency']}.",
            f"Current runtime drawdown is {current_drawdown}%.",
            f"Risk permission status is {status}.",
        ]
        if not daily_loss_verified:
            if environment_mode == "production":
                explanation.append("Daily loss history is not implemented yet, so trading is hard blocked in production.")
            else:
                explanation.append("Daily loss history is not implemented yet; development mode returns a warning only.")
        if block_reasons:
            explanation.append(f"Blocked because: {', '.join(block_reasons)}.")
        explanation.append("Advisor Mode only: no execution action was taken.")
        return explanation

    def _load_risk_profile(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "risk_profile.yaml")
        return self._deep_merge(self.DEFAULT_RISK_PROFILE, config)

    def _load_trading_rules(self) -> dict[str, Any]:
        config = self._load_yaml_file(self.config_dir / "trading_rules.yaml")
        return self._deep_merge(self.DEFAULT_TRADING_RULES, config)

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning("Config file {} does not exist.", path)
            return {}

        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise RiskGovernorError(f"Failed to load config {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
