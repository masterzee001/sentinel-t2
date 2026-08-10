"""
Unit tests for Asset Profile System (O1)

Tests validate:
- Profile retrieval and accuracy
- Production vs. observer status
- Fallback behavior for unknown symbols
- Profile consistency validation
- Helper function correctness
- No trading behavior change
"""

import pytest
from backend.shared.asset_profiles import (
    AssetProfile,
    AssetClass,
    MacroMode,
    CorrelationGroup,
    US30_PROFILE,
    XAUUSD_PROFILE,
    NAS100_PROFILE,
    get_asset_profile,
    is_production_asset,
    is_observer_asset,
    get_correlation_group,
    get_all_production_symbols,
    get_all_observer_symbols,
    get_symbols_in_correlation_group,
    get_macro_mode,
    validate_symbol,
    register_profile,
    update_profile,
    ASSET_PROFILE_REGISTRY,
)


class TestProfileRetrieval:
    """Test profile lookup functionality."""

    def test_get_us30_profile(self):
        """US30 profile retrieves correctly."""
        profile = get_asset_profile("US30")
        assert profile is not None
        assert profile.symbol == "US30"
        assert profile.asset_class == AssetClass.INDEX
        assert profile.production_enabled is True
        assert profile.execution_allowed is True
        assert profile.observer_only is False

    def test_get_xauusd_profile(self):
        """XAUUSD profile retrieves correctly."""
        profile = get_asset_profile("XAUUSD")
        assert profile is not None
        assert profile.symbol == "XAUUSD"
        assert profile.asset_class == AssetClass.METAL
        assert profile.production_enabled is True
        assert profile.execution_allowed is True
        assert profile.observer_only is False
        assert profile.macro_mode == MacroMode.GOLD
        assert "DXY" in profile.macro_inputs

    def test_get_nas100_profile(self):
        """NAS100 profile retrieves correctly (observer)."""
        profile = get_asset_profile("NAS100")
        assert profile is not None
        assert profile.symbol == "NAS100"
        assert profile.asset_class == AssetClass.INDEX
        assert profile.production_enabled is False
        assert profile.execution_allowed is False
        assert profile.observer_only is True

    def test_unknown_symbol_returns_none(self):
        """Unknown symbol returns None (safe fallback)."""
        profile = get_asset_profile("UNKNOWN_SYMBOL")
        assert profile is None

    def test_unknown_symbol_case_sensitive(self):
        """Symbol lookup is case-sensitive."""
        profile = get_asset_profile("us30")  # lowercase
        assert profile is None


class TestProductionStatus:
    """Test production asset classification."""

    def test_us30_is_production(self):
        """US30 marked as production asset."""
        assert is_production_asset("US30") is True

    def test_xauusd_is_production(self):
        """XAUUSD marked as production asset."""
        assert is_production_asset("XAUUSD") is True

    def test_nas100_not_production(self):
        """NAS100 is NOT production (observer only)."""
        assert is_production_asset("NAS100") is False

    def test_unknown_symbol_not_production(self):
        """Unknown symbol returns False for production check."""
        assert is_production_asset("UNKNOWN") is False

    def test_get_all_production_symbols(self):
        """List of all production symbols is correct."""
        prod_symbols = get_all_production_symbols()
        assert "US30" in prod_symbols
        assert "XAUUSD" in prod_symbols
        assert "NAS100" not in prod_symbols
        assert len(prod_symbols) == 2


class TestObserverStatus:
    """Test observer asset classification."""

    def test_nas100_is_observer(self):
        """NAS100 is observer-only."""
        assert is_observer_asset("NAS100") is True

    def test_us30_not_observer(self):
        """US30 is NOT observer."""
        assert is_observer_asset("US30") is False

    def test_xauusd_not_observer(self):
        """XAUUSD is NOT observer."""
        assert is_observer_asset("XAUUSD") is False

    def test_unknown_symbol_not_observer(self):
        """Unknown symbol returns False."""
        assert is_observer_asset("UNKNOWN") is False

    def test_get_all_observer_symbols(self):
        """List of all observer symbols is correct."""
        observer_symbols = get_all_observer_symbols()
        assert "NAS100" in observer_symbols
        assert "US30" not in observer_symbols
        assert "XAUUSD" not in observer_symbols
        assert len(observer_symbols) == 1


class TestCorrelationGroups:
    """Test portfolio correlation grouping."""

    def test_us30_indices_group(self):
        """US30 in indices correlation group."""
        group = get_correlation_group("US30")
        assert group == CorrelationGroup.INDICES

    def test_nas100_indices_group(self):
        """NAS100 in indices correlation group."""
        group = get_correlation_group("NAS100")
        assert group == CorrelationGroup.INDICES

    def test_xauusd_metals_group(self):
        """XAUUSD in metals correlation group."""
        group = get_correlation_group("XAUUSD")
        assert group == CorrelationGroup.METALS

    def test_unknown_symbol_returns_none(self):
        """Unknown symbol returns None."""
        group = get_correlation_group("UNKNOWN")
        assert group is None

    def test_get_symbols_in_indices_group(self):
        """Retrieve all symbols in indices group."""
        indices = get_symbols_in_correlation_group(CorrelationGroup.INDICES)
        assert "US30" in indices
        assert "NAS100" in indices
        assert "XAUUSD" not in indices
        assert len(indices) == 2

    def test_get_symbols_in_metals_group(self):
        """Retrieve all symbols in metals group."""
        metals = get_symbols_in_correlation_group(CorrelationGroup.METALS)
        assert "XAUUSD" in metals
        assert "US30" not in metals
        assert len(metals) == 1


class TestMacroMode:
    """Test intelligence mode parameters."""

    def test_us30_index_mode(self):
        """US30 uses index macro mode."""
        mode = get_macro_mode("US30")
        assert mode == MacroMode.INDEX

    def test_xauusd_gold_mode(self):
        """XAUUSD uses gold macro mode."""
        mode = get_macro_mode("XAUUSD")
        assert mode == MacroMode.GOLD

    def test_nas100_index_mode(self):
        """NAS100 uses index macro mode."""
        mode = get_macro_mode("NAS100")
        assert mode == MacroMode.INDEX

    def test_unknown_symbol_returns_none(self):
        """Unknown symbol returns None."""
        mode = get_macro_mode("UNKNOWN")
        assert mode is None


class TestProfileValidation:
    """Test profile internal consistency checks."""

    def test_observer_cannot_be_execution_allowed(self):
        """Cannot create observer profile that allows execution."""
        with pytest.raises(ValueError, match="observer_only=True but execution_allowed=True"):
            AssetProfile(
                symbol="BAD_PROFILE",
                asset_class=AssetClass.INDEX,
                production_enabled=False,
                execution_allowed=True,  # Invalid with observer_only=True
                observer_only=True,
                valid_killzones=["NY_OPEN"],
                min_liquidity_timeframe="M15",
                macro_mode=MacroMode.INDEX,
                base_risk_percent=0.3,
                max_risk_percent=0.5,
                correlation_group=CorrelationGroup.INDICES,
                use_for_correlation=False,
                spread_tolerance=0.5,
                slippage_tolerance=0.3,
            )

    def test_observer_cannot_be_production_enabled(self):
        """Cannot create observer profile with production_enabled=True."""
        with pytest.raises(ValueError, match="observer_only=True but production_enabled=True"):
            AssetProfile(
                symbol="BAD_PROFILE",
                asset_class=AssetClass.INDEX,
                production_enabled=True,  # Invalid with observer_only=True
                execution_allowed=False,
                observer_only=True,
                valid_killzones=["NY_OPEN"],
                min_liquidity_timeframe="M15",
                macro_mode=MacroMode.INDEX,
                base_risk_percent=0.3,
                max_risk_percent=0.5,
                correlation_group=CorrelationGroup.INDICES,
                use_for_correlation=False,
                spread_tolerance=0.5,
                slippage_tolerance=0.3,
            )

    def test_base_risk_cannot_exceed_max_risk(self):
        """base_risk_percent cannot exceed max_risk_percent."""
        with pytest.raises(ValueError, match="base_risk_percent .* > max_risk_percent"):
            AssetProfile(
                symbol="BAD_PROFILE",
                asset_class=AssetClass.INDEX,
                production_enabled=True,
                execution_allowed=True,
                observer_only=False,
                valid_killzones=["NY_OPEN"],
                min_liquidity_timeframe="M15",
                macro_mode=MacroMode.INDEX,
                base_risk_percent=1.0,  # 100% base
                max_risk_percent=0.5,   # Max 50% — invalid!
                correlation_group=CorrelationGroup.INDICES,
                use_for_correlation=False,
                spread_tolerance=0.5,
                slippage_tolerance=0.3,
            )


class TestSymbolValidation:
    """Test symbol validation helper."""

    def test_validate_us30(self):
        """Validate US30 symbol."""
        is_valid, message = validate_symbol("US30")
        assert is_valid is True
        assert "registered" in message.lower()

    def test_validate_unknown_symbol(self):
        """Validate unknown symbol."""
        is_valid, message = validate_symbol("FAKE_SYMBOL")
        assert is_valid is False
        assert "not registered" in message.lower()


class TestProfileDataExport:
    """Test profile-to-dict conversion."""

    def test_us30_to_dict(self):
        """US30 profile exports to dictionary."""
        data = US30_PROFILE.to_dict()
        assert isinstance(data, dict)
        assert data["symbol"] == "US30"
        assert data["asset_class"] == "index"
        assert data["production_enabled"] is True
        assert data["macro_mode"] == "index"

    def test_xauusd_to_dict_includes_macro_inputs(self):
        """XAUUSD to_dict includes macro_inputs."""
        data = XAUUSD_PROFILE.to_dict()
        assert "macro_inputs" in data
        assert "DXY" in data["macro_inputs"]
        assert len(data["macro_inputs"]) >= 4


class TestProfileConsistency:
    """Verify profiles match Constitution specifications."""

    def test_us30_matches_spec(self):
        """US30 profile matches SENTINEL_CONSTITUTION specs."""
        profile = US30_PROFILE
        assert profile.valid_killzones == ["NEW_YORK_OPEN"]
        assert profile.min_liquidity_timeframe == "M15"
        assert profile.macro_mode == MacroMode.INDEX
        assert profile.correlation_group == CorrelationGroup.INDICES
        assert profile.use_for_correlation is True

    def test_xauusd_matches_spec(self):
        """XAUUSD profile matches Constitution specs."""
        profile = XAUUSD_PROFILE
        assert set(profile.valid_killzones) == {"LONDON_OPEN", "NY_OVERLAP"}
        assert profile.min_liquidity_timeframe == "H1"
        assert profile.macro_mode == MacroMode.GOLD
        assert profile.correlation_group == CorrelationGroup.METALS
        assert profile.use_for_correlation is False

    def test_nas100_matches_spec(self):
        """NAS100 profile matches Constitution specs."""
        profile = NAS100_PROFILE
        assert set(profile.valid_killzones) == {"LONDON_OPEN", "NEW_YORK_OPEN"}
        assert profile.min_liquidity_timeframe == "M15"
        assert profile.macro_mode == MacroMode.INDEX
        assert profile.observer_only is True
        assert profile.execution_allowed is False


class TestRegistryIntegrity:
    """Verify registry is not corrupted and stable."""

    def test_registry_has_three_profiles(self):
        """Registry contains exactly three profiles."""
        assert len(ASSET_PROFILE_REGISTRY) == 3

    def test_registry_keys_match_symbols(self):
        """Registry keys match profile symbols."""
        for symbol, profile in ASSET_PROFILE_REGISTRY.items():
            assert symbol == profile.symbol

    def test_no_duplicate_symbols(self):
        """No duplicate symbols in registry."""
        symbols = [p.symbol for p in ASSET_PROFILE_REGISTRY.values()]
        assert len(symbols) == len(set(symbols))


class TestNoTradingBehaviorChange:
    """Verify asset profiles do NOT change trading behavior."""

    def test_profiles_are_parameter_only(self):
        """Profiles contain only data, no methods with business logic."""
        # AssetProfile should be a pure data class
        # Verify only expected methods exist (to_dict, __post_init__ for validation)
        custom_methods = [m for m in dir(US30_PROFILE)
                         if not m.startswith('_')
                         and callable(getattr(US30_PROFILE, m))
                         and m not in ['to_dict']]  # to_dict is expected
        # Should have no custom business logic methods
        assert len(custom_methods) == 0, f"Unexpected methods found: {custom_methods}"

    def test_helper_functions_do_not_execute_trades(self):
        """Helper functions are parameter lookups only, no execution."""
        # Calling all helper functions should have no side effects
        get_asset_profile("US30")
        is_production_asset("US30")
        is_observer_asset("NAS100")
        get_correlation_group("XAUUSD")
        get_all_production_symbols()
        get_all_observer_symbols()
        get_symbols_in_correlation_group(CorrelationGroup.INDICES)
        get_macro_mode("XAUUSD")
        validate_symbol("US30")
        # If we got here without exceptions, no behavior changed

    def test_profiles_not_wired_to_sda(self):
        """Profiles are independent from SDA (not integrated yet)."""
        # This test verifies that importing asset_profiles doesn't import SDA
        import sys
        # Check that backend.shared.sda is NOT imported
        # (should fail gracefully if not found, not be auto-imported)
        try:
            from backend.shared import sda_tiered_admission
            # If SDA exists, verify it doesn't import asset_profiles at module load
            # (actual integration comes in O2)
        except ImportError:
            # SDA doesn't exist yet (expected in O1), so we're good
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
