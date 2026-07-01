from __future__ import annotations

from pathlib import Path

from backend.symbols.symbol_discovery import SymbolDiscovery
from backend.symbols.symbol_registry import SymbolRegistry


def write_registry(config_dir: Path) -> None:
    config_dir.mkdir()
    (config_dir / "symbol_registry.yaml").write_text(
        """
tier_1_production:
  - US30
tier_2_filtered_production:
  - XAUUSD
tier_3_demo_sandbox:
  - BTCUSD
  - NAS100
tier_4_observer_only:
  - EURUSD
  - GBPUSD
aliases:
  BTCUSD:
    - BTCUSD
    - BTCUSDm
  NAS100:
    - NAS100
    - USTEC
promotion_rules:
  production:
    min_pf: 1.5
    min_wr: 55
    min_trades: 30
    max_dd: 4
  observer:
    min_pf: 1.0
    max_pf: 1.5
  disabled:
    max_pf: 1.0
observer_reasons:
  NAS100: "NAS100 demo sandbox: production execution disabled"
""",
        encoding="utf-8",
    )


def test_registry_tiers_and_classification(tmp_path: Path):
    config_dir = tmp_path / "config"
    write_registry(config_dir)
    registry = SymbolRegistry(config_dir=config_dir)

    assert registry.tier_for("US30") == "PRODUCTION"
    assert registry.tier_for("XAUUSD") == "PRODUCTION"
    assert registry.tier_for("NAS100") == "DEMO_SANDBOX"
    assert registry.execution_allowed("NAS100") is False
    assert registry.sandbox_execution_allowed("NAS100") is True
    assert registry.tier_for("EURUSD") == "OBSERVER_ONLY"
    assert registry.observer_reason("NAS100") == "NAS100 demo sandbox: production execution disabled"
    assert registry.classify({"profit_factor": 1.6, "win_rate": 56, "trades": 31, "max_drawdown": 3.5}) == "PRODUCTION_CANDIDATE"
    assert registry.classify({"profit_factor": 1.2, "win_rate": 50, "trades": 20, "max_drawdown": 2}) == "OBSERVER"
    assert registry.classify({"profit_factor": 0.8}) == "DISABLE"


def test_symbol_discovery_matches_patterns_and_preferred_alias():
    symbols = [
        {"name": "EURUSD", "path": "Forex"},
        {"name": "BTCUSDm", "path": "Crypto"},
        {"name": "USTEC", "description": "Nasdaq 100 index"},
    ]

    crypto = SymbolDiscovery.discover(type("MT5", (), {"symbols_get": lambda self=None: symbols})(), ["BTC", "CRYPTO"])
    index = SymbolDiscovery.discover(type("MT5", (), {"symbols_get": lambda self=None: symbols})(), ["NAS", "USTEC"])

    assert [item["symbol"] for item in crypto] == ["BTCUSDm"]
    assert SymbolDiscovery.choose_preferred(crypto, ["BTCUSD", "BTCUSDm"]) == "BTCUSDm"
    assert [item["symbol"] for item in index] == ["USTEC"]
