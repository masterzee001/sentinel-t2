"""Central symbol tier registry for Project Sentinel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class SymbolRegistryError(RuntimeError):
    """Raised when symbol registry configuration cannot be loaded."""


class SymbolRegistry:
    """Load symbol tiers and classify symbols from backtest metrics."""

    DEFAULT_CONFIG = {
        "tier_1_production": ["US30", "NAS100"],
        "tier_2_filtered_production": [],
        "tier_3_demo_sandbox": ["BTCUSD"],
        "tier_4_observer_only": ["XAUUSD", "EURUSD", "GBPUSD"],
        "tier_3_observer": [],
        "tier_4_disabled": [],
        "aliases": {
            "BTCUSD": ["BTC", "BTCUSD", "BTCUSDm", "BTCUSD.cash", "BTCUSD.pro", "XBTUSD"],
            "NAS100": ["NAS100", "USTEC", "USTECH100M", "NAS100.cash", "NASUSD"],
        },
        "promotion_rules": {
            "production": {"min_pf": 1.5, "min_wr": 55, "min_trades": 30, "max_dd": 4},
            "observer": {"min_pf": 1.0, "max_pf": 1.5},
            "disabled": {"max_pf": 1.0},
        },
        "execution": {
            "observer_execution_allowed": False,
            "demo_sandbox_execution_allowed": False,
            "autonomous_execution_allowed": False,
        },
        "observer_reasons": {
            "XAUUSD": "XAUUSD observer mode: 3y PF 0.89 under index logic; pending MPV redesign",
            "BTCUSD": "BTCUSD demo sandbox: production execution disabled",
            "EURUSD": "EURUSD observer-only mode: production execution disabled",
            "GBPUSD": "GBPUSD observer-only mode: production execution disabled",
        },
    }
    TIER_KEYS = {
        "tier_1_production": ("PRODUCTION", "Production"),
        "tier_2_filtered_production": ("PRODUCTION", "Production"),
        "tier_3_demo_sandbox": ("DEMO_SANDBOX", "Demo Sandbox"),
        "tier_3_observer": ("DEMO_SANDBOX", "Demo Sandbox"),
        "tier_4_observer_only": ("OBSERVER_ONLY", "Observer Only"),
        "tier_4_disabled": ("OBSERVER_ONLY", "Observer Only"),
    }

    def __init__(self, config_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.config_dir = Path(config_dir) if config_dir else project_root / "config"
        self.config = self._deep_merge(
            self.DEFAULT_CONFIG,
            self._load_yaml_file(self.config_dir / "symbol_registry.yaml"),
        )

    def symbols(self) -> list[str]:
        """Return all configured symbols in tier order."""
        values: list[str] = []
        for key in self.TIER_KEYS:
            for symbol in self.config.get(key, []):
                normalized = self.normalize_symbol(symbol)
                if normalized not in values:
                    values.append(normalized)
        return values

    def observer_symbols(self) -> list[str]:
        """Return all non-production diagnostic symbols."""
        values = [*self.demo_sandbox_symbols(), *self.observer_only_symbols()]
        return list(dict.fromkeys(values))

    def demo_sandbox_symbols(self) -> list[str]:
        """Return demo-sandbox symbols."""
        values = [
            *[self.normalize_symbol(symbol) for symbol in self.config.get("tier_3_demo_sandbox", [])],
            *[self.normalize_symbol(symbol) for symbol in self.config.get("tier_3_observer", [])],
        ]
        return list(dict.fromkeys(values))

    def observer_only_symbols(self) -> list[str]:
        """Return observer-only symbols."""
        values = [
            *[self.normalize_symbol(symbol) for symbol in self.config.get("tier_4_observer_only", [])],
            *[self.normalize_symbol(symbol) for symbol in self.config.get("tier_4_disabled", [])],
        ]
        return list(dict.fromkeys(values))

    def execution_symbols(self) -> list[str]:
        """Return symbols allowed to reach execution gates."""
        return [
            *[self.normalize_symbol(symbol) for symbol in self.config.get("tier_1_production", [])],
            *[self.normalize_symbol(symbol) for symbol in self.config.get("tier_2_filtered_production", [])],
        ]

    def disabled_symbols(self) -> list[str]:
        """Return legacy disabled/observer-only symbols."""
        return self.observer_only_symbols()

    def tier_for(self, symbol: str) -> str:
        """Return machine tier label for a symbol."""
        normalized = self.normalize_symbol(symbol)
        for key, (tier, _display) in self.TIER_KEYS.items():
            if normalized in {self.normalize_symbol(value) for value in self.config.get(key, [])}:
                return tier
        return "UNREGISTERED"

    def display_tier_for(self, symbol: str) -> str:
        """Return display tier label for a symbol."""
        tier = self.tier_for(symbol)
        for _key, (machine, display) in self.TIER_KEYS.items():
            if machine == tier:
                return display
        return "Unregistered"

    def is_observer(self, symbol: str) -> bool:
        """Return whether symbol is observer-only."""
        return self.tier_for(symbol) == "OBSERVER_ONLY"

    def is_observer_only(self, symbol: str) -> bool:
        """Return whether symbol is observer-only."""
        return self.tier_for(symbol) == "OBSERVER_ONLY"

    def is_demo_sandbox(self, symbol: str) -> bool:
        """Return whether symbol is demo-sandbox eligible."""
        return self.tier_for(symbol) == "DEMO_SANDBOX"

    def execution_allowed(self, symbol: str) -> bool:
        """Return whether the registry permits execution for a symbol tier."""
        return self.tier_for(symbol) == "PRODUCTION"

    def sandbox_execution_allowed(self, symbol: str) -> bool:
        """Return whether a symbol may be evaluated by the demo sandbox layer."""
        return self.is_demo_sandbox(symbol)

    def observer_reason(self, symbol: str) -> str:
        """Return configured observer block reason."""
        normalized = self.normalize_symbol(symbol)
        return str(
            self.config.get("observer_reasons", {}).get(
                normalized,
                f"{normalized} observer mode: execution disabled",
            )
        )

    def aliases_for(self, symbol: str) -> list[str]:
        """Return broker alias candidates for a canonical symbol."""
        normalized = self.normalize_symbol(symbol)
        aliases = self.config.get("aliases", {}).get(normalized, [normalized])
        values = [str(alias).strip() for alias in aliases if str(alias).strip()]
        return values or [normalized]

    def rows(self, metrics_by_symbol: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Return dashboard-friendly registry rows."""
        metrics_by_symbol = metrics_by_symbol or {}
        rows = []
        for symbol in self.symbols():
            metrics = metrics_by_symbol.get(symbol, {})
            status = self.classify(metrics) if metrics else self.default_status(symbol)
            if self.is_demo_sandbox(symbol) and status != "PRODUCTION_CANDIDATE":
                status = "DEMO_SANDBOX"
            if self.is_observer_only(symbol):
                status = "OBSERVER_ONLY"
            rows.append(
                {
                    "symbol": symbol,
                    "tier": self.display_tier_for(symbol),
                    "pf": self.metric(metrics, "pf", "profit_factor"),
                    "wr": self.metric(metrics, "wr", "win_rate", "winrate"),
                    "trades": int(self.metric(metrics, "trades", "trades_approved")),
                    "dd": self.metric(metrics, "dd", "max_drawdown"),
                    "status": status,
                }
            )
        return rows

    def classify(self, metrics: dict[str, Any]) -> str:
        """Classify performance using the registry promotion rules."""
        pf = self.metric(metrics, "pf", "profit_factor")
        wr = self.metric(metrics, "wr", "win_rate", "winrate")
        trades = self.metric(metrics, "trades", "trades_approved")
        dd = self.metric(metrics, "dd", "max_drawdown")
        production = self.config.get("promotion_rules", {}).get("production", {})
        observer = self.config.get("promotion_rules", {}).get("observer", {})
        disabled = self.config.get("promotion_rules", {}).get("disabled", {})

        if pf < float(disabled.get("max_pf", 1.0)):
            return "DISABLE"
        if (
            pf > float(production.get("min_pf", 1.5))
            and wr > float(production.get("min_wr", 55))
            and trades > float(production.get("min_trades", 30))
            and dd < float(production.get("max_dd", 4))
        ):
            return "PRODUCTION_CANDIDATE"
        if float(observer.get("min_pf", 1.0)) < pf < float(observer.get("max_pf", 1.5)):
            return "OBSERVER"
        return "WATCH"

    def default_status(self, symbol: str) -> str:
        """Return status when no backtest metrics exist."""
        tier = self.tier_for(symbol)
        if tier == "DEMO_SANDBOX":
            return "DEMO_SANDBOX"
        if tier == "OBSERVER_ONLY":
            return "OBSERVER_ONLY"
        return "ACTIVE"

    @staticmethod
    def metric(metrics: dict[str, Any], *keys: str) -> float:
        """Return first available numeric metric."""
        for key in keys:
            if key in metrics:
                try:
                    return round(float(metrics.get(key) or 0.0), 2)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    @staticmethod
    def normalize_symbol(symbol: Any) -> str:
        return str(symbol).upper().strip()

    @staticmethod
    def _load_yaml_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return yaml.safe_load(file) or {}
        except Exception as exc:
            raise SymbolRegistryError(f"Failed to load symbol registry {path}: {exc}") from exc

    @classmethod
    def _deep_merge(cls, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
