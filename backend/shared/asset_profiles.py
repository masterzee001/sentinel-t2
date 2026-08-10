"""
Asset Profile System for Sentinel T2

This module implements the Asset Profile Unified Policy (O1).
It provides a parameter-only configuration layer for asset-specific behavior.

CONSTRAINT: This module does NOT contain logic, routing decisions, or strategy.
It is purely a data structure and parameter store.

Per SENTINEL_CONSTITUTION.md Law 1:
  "All assets must use the same unified decision architecture,
   but may use asset-specific parameters."

Per Law 2:
  "Configuration may define thresholds, sessions, risk limits, asset behavior.
   It must not become a second codebase or decide engine topology."
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class AssetClass(Enum):
    """Asset classification for profile organization."""
    INDEX = "index"
    METAL = "metal"
    CURRENCY = "currency"
    CRYPTO = "crypto"


class MacroMode(Enum):
    """Intelligence mode selection (parameter only, no routing logic)."""
    INDEX = "index"           # US30, NAS100 — inter-index SMT
    GOLD = "gold"            # XAUUSD — macro pressure vector
    CURRENCY = "currency"    # EURUSD, GBPUSD — macro intermarket
    CRYPTO = "crypto"        # BTCUSD — volatility-driven


class CorrelationGroup(Enum):
    """Portfolio correlation grouping for Tier 4A (Portfolio Admission)."""
    INDICES = "indices"      # US30, NAS100 — usually correlated
    METALS = "metals"        # XAUUSD — uncorrelated with equities
    CURRENCIES = "currencies"  # EURUSD, GBPUSD — mixed correlation
    CRYPTO = "crypto"        # BTCUSD — emerging correlation


@dataclass
class AssetProfile:
    """
    Immutable asset profile: parameter container only.

    SPEC COMPLIANT:
    - Holds asset-specific parameters per SENTINEL_CONSTITUTION.md
    - No logic, no routing, no decision-making
    - Per Law 2: Config defines parameters, never logic
    """

    # Identity
    symbol: str
    asset_class: AssetClass

    # Production Status
    production_enabled: bool
    execution_allowed: bool
    observer_only: bool

    # Market Structure Parameters
    valid_killzones: List[str]
    min_liquidity_timeframe: str  # e.g., "M15", "H1", "H4"

    # Intelligence Mode
    macro_mode: MacroMode

    # Risk Parameters
    base_risk_percent: float       # Base position size as % of account
    max_risk_percent: float        # Max allowed risk

    # Portfolio Parameters
    correlation_group: CorrelationGroup
    use_for_correlation: bool      # Used in SMT/correlation analysis?

    # Execution Quality
    spread_tolerance: float        # Max spread in pips
    slippage_tolerance: float      # Max acceptable slippage in pips

    # Metadata
    profile_version: str = "1.0"
    notes: Optional[str] = None
    macro_inputs: List[str] = field(default_factory=list)  # For gold MPV, FX modes

    def __post_init__(self):
        """Validation: ensure profile is self-consistent."""
        if self.observer_only and self.execution_allowed:
            raise ValueError(
                f"{self.symbol}: observer_only=True but execution_allowed=True. "
                "Observer symbols cannot execute."
            )

        if self.observer_only and self.production_enabled:
            raise ValueError(
                f"{self.symbol}: observer_only=True but production_enabled=True. "
                "Observer symbols do not count toward production metrics."
            )

        if self.base_risk_percent > self.max_risk_percent:
            raise ValueError(
                f"{self.symbol}: base_risk_percent ({self.base_risk_percent}) "
                f"> max_risk_percent ({self.max_risk_percent})"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Export profile as dictionary (for display, logging, serialization)."""
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class.value,
            "production_enabled": self.production_enabled,
            "execution_allowed": self.execution_allowed,
            "observer_only": self.observer_only,
            "valid_killzones": self.valid_killzones,
            "min_liquidity_timeframe": self.min_liquidity_timeframe,
            "macro_mode": self.macro_mode.value,
            "base_risk_percent": self.base_risk_percent,
            "max_risk_percent": self.max_risk_percent,
            "correlation_group": self.correlation_group.value,
            "use_for_correlation": self.use_for_correlation,
            "spread_tolerance": self.spread_tolerance,
            "slippage_tolerance": self.slippage_tolerance,
            "profile_version": self.profile_version,
            "notes": self.notes,
            "macro_inputs": self.macro_inputs,
        }


# ============================================================================
# SPEC COMPLIANT: Initial Asset Profiles
# ============================================================================

US30_PROFILE = AssetProfile(
    symbol="US30",
    asset_class=AssetClass.INDEX,
    production_enabled=True,
    execution_allowed=True,
    observer_only=False,
    valid_killzones=["NEW_YORK_OPEN"],
    min_liquidity_timeframe="M15",
    macro_mode=MacroMode.INDEX,
    base_risk_percent=0.3,
    max_risk_percent=0.5,
    correlation_group=CorrelationGroup.INDICES,
    use_for_correlation=True,
    spread_tolerance=0.5,
    slippage_tolerance=0.3,
    notes="S&P 500 Index. Production baseline: PF 1.69, WR 60%, DD 2.0%",
)

XAUUSD_PROFILE = AssetProfile(
    symbol="XAUUSD",
    asset_class=AssetClass.METAL,
    production_enabled=True,
    execution_allowed=True,
    observer_only=False,
    valid_killzones=["LONDON_OPEN", "NY_OVERLAP"],
    min_liquidity_timeframe="H1",
    macro_mode=MacroMode.GOLD,
    base_risk_percent=0.15,
    max_risk_percent=0.3,
    correlation_group=CorrelationGroup.METALS,
    use_for_correlation=False,
    spread_tolerance=0.5,
    slippage_tolerance=0.3,
    notes="Gold (macro-driven commodity). Current: PF 1.0, WR 50%. Target: MPV specialization.",
    macro_inputs=["DXY", "US10Y", "RealYields", "SPX_Sentiment", "VIX"],
)

NAS100_PROFILE = AssetProfile(
    symbol="NAS100",
    asset_class=AssetClass.INDEX,
    production_enabled=False,
    execution_allowed=False,
    observer_only=True,
    valid_killzones=["LONDON_OPEN", "NEW_YORK_OPEN"],
    min_liquidity_timeframe="M15",
    macro_mode=MacroMode.INDEX,
    base_risk_percent=0.3,
    max_risk_percent=0.5,
    correlation_group=CorrelationGroup.INDICES,
    use_for_correlation=True,
    spread_tolerance=0.5,
    slippage_tolerance=0.3,
    notes="NASDAQ-100. Observer only. PF 2.0, WR 65.22%. Promotion gate: 100 trades @ PF >= 1.75",
)

# ============================================================================
# Profile Registry (Lookup Table)
# ============================================================================

ASSET_PROFILE_REGISTRY: Dict[str, AssetProfile] = {
    "US30": US30_PROFILE,
    "XAUUSD": XAUUSD_PROFILE,
    "NAS100": NAS100_PROFILE,
}


# ============================================================================
# Helper Functions (Parameter-Only, No Logic)
# ============================================================================

def get_asset_profile(symbol: str) -> Optional[AssetProfile]:
    """
    Retrieve asset profile by symbol.

    Returns:
        AssetProfile if symbol registered, None otherwise.

    DEV PROPOSALS — REQUIRES APPROVAL

    PROPOSED — NOT CANONICAL: Fallback behavior
    Current: Returns None for unknown symbols (allows caller to decide error handling)
    Alternative: Raise explicit exception
    Decision Required: Which approach matches current architecture?
    """
    return ASSET_PROFILE_REGISTRY.get(symbol)


def is_production_asset(symbol: str) -> bool:
    """
    Check if asset is marked for production execution.

    Returns:
        True if production_enabled=True AND execution_allowed=True
        False if unknown symbol or observer-only
    """
    profile = get_asset_profile(symbol)
    if profile is None:
        return False
    return profile.production_enabled and profile.execution_allowed


def is_observer_asset(symbol: str) -> bool:
    """
    Check if asset is observer-only (diagnostic, no execution).

    Returns:
        True if observer_only=True
        False if unknown symbol or production asset
    """
    profile = get_asset_profile(symbol)
    if profile is None:
        return False
    return profile.observer_only


def get_correlation_group(symbol: str) -> Optional[CorrelationGroup]:
    """
    Get portfolio correlation group for symbol (used in Tier 4A).

    Returns:
        CorrelationGroup enum value
        None if unknown symbol
    """
    profile = get_asset_profile(symbol)
    if profile is None:
        return None
    return profile.correlation_group


def get_all_production_symbols() -> List[str]:
    """Get list of all production-enabled symbols."""
    return [
        sym for sym, profile in ASSET_PROFILE_REGISTRY.items()
        if is_production_asset(sym)
    ]


def get_all_observer_symbols() -> List[str]:
    """Get list of all observer-only symbols."""
    return [
        sym for sym, profile in ASSET_PROFILE_REGISTRY.items()
        if is_observer_asset(sym)
    ]


def get_symbols_in_correlation_group(group: CorrelationGroup) -> List[str]:
    """
    Get all symbols in a correlation group (used for portfolio checks).

    Example:
        Prevent two highly-correlated indices from executing same setup.
    """
    return [
        sym for sym, profile in ASSET_PROFILE_REGISTRY.items()
        if profile.correlation_group == group
    ]


def get_macro_mode(symbol: str) -> Optional[MacroMode]:
    """Get intelligence mode for symbol (parameter only, no routing logic)."""
    profile = get_asset_profile(symbol)
    if profile is None:
        return None
    return profile.macro_mode


def validate_symbol(symbol: str) -> tuple[bool, str]:
    """
    Validate symbol is registered and ready for use.

    Returns:
        (is_valid, message)
    """
    profile = get_asset_profile(symbol)
    if profile is None:
        return (False, f"Symbol {symbol} not registered in ASSET_PROFILE_REGISTRY")
    return (True, f"Symbol {symbol} registered. Producer: {profile.production_enabled}, Observer: {profile.observer_only}")


# ============================================================================
# Registration/Extension Point
# ============================================================================

def register_profile(profile: AssetProfile) -> None:
    """
    Register a new asset profile (for future expansion).

    CONSTRAINT: Does not integrate with SDA or guardrails.
    This is purely a registry operation.

    DEV PROPOSALS — REQUIRES APPROVAL

    PROPOSED — NOT CANONICAL: Allow dynamic profile registration
    Current: Function provided for future extensibility
    Used by: Future EURUSD, GBPUSD, BTCUSD profiles
    Decision Required: Approve dynamic registration or keep registry static?
    """
    if profile.symbol in ASSET_PROFILE_REGISTRY:
        raise ValueError(f"Profile for {profile.symbol} already registered. Use update_profile().")
    ASSET_PROFILE_REGISTRY[profile.symbol] = profile


def update_profile(symbol: str, updated_profile: AssetProfile) -> None:
    """Update existing profile (for runtime parameter adjustment)."""
    if symbol not in ASSET_PROFILE_REGISTRY:
        raise ValueError(f"Profile for {symbol} not found. Use register_profile() to add new.")
    ASSET_PROFILE_REGISTRY[symbol] = updated_profile


# ============================================================================
# Debug/Display Functions
# ============================================================================

def print_all_profiles() -> None:
    """Print all registered profiles (for debugging, logging)."""
    print("\n" + "="*80)
    print("ASSET PROFILE REGISTRY")
    print("="*80)
    for symbol, profile in ASSET_PROFILE_REGISTRY.items():
        print(f"\n{symbol}:")
        print(f"  Asset Class:       {profile.asset_class.value}")
        print(f"  Production:        {profile.production_enabled}")
        print(f"  Execution:         {profile.execution_allowed}")
        print(f"  Observer:          {profile.observer_only}")
        print(f"  Killzones:         {', '.join(profile.valid_killzones)}")
        print(f"  Min Liquidity TF:  {profile.min_liquidity_timeframe}")
        print(f"  Macro Mode:        {profile.macro_mode.value}")
        print(f"  Risk (base/max):   {profile.base_risk_percent}% / {profile.max_risk_percent}%")
        print(f"  Correlation Group: {profile.correlation_group.value}")
        print(f"  Use for SMT:       {profile.use_for_correlation}")
        if profile.macro_inputs:
            print(f"  Macro Inputs:      {', '.join(profile.macro_inputs)}")
        if profile.notes:
            print(f"  Notes:             {profile.notes}")
    print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    # Quick validation on module load
    print_all_profiles()
    print("\nProduction Assets:", get_all_production_symbols())
    print("Observer Assets:", get_all_observer_symbols())
    print("Indices Correlation Group:", get_symbols_in_correlation_group(CorrelationGroup.INDICES))
