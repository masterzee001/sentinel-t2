# O2 Planning: Shared Decision Adapter Tier Refactor

**Date:** 2026-07-01  
**Status:** Planning Phase (No Code Changes Yet)  
**Goal:** Design the SDA refactor to implement SENTINEL_CONSTITUTION.md tiers without changing trading behavior

---

## 1. Current System Inspection

### 1.1 Core SDA File

**Path:** `backend/shared/shared_decision_adapter.py`

**Current Structure:**
```python
class SharedDecisionAdapter:
    def __init__(self, confidence_analyzer: ConfidenceBrain | None = None)
    def evaluate(request: DecisionRequest | dict[str, Any]) -> dict[str, Any]
    def normalize_request(request) -> DecisionRequest
```

**Current Behavior:**
- High-level pass-through wrapper
- Normalizes mode (LIVE, DEMO, REPLAY, BACKTEST, PAPER)
- Routes to ConfidenceAnalyzer
- No decision logic, no tier hierarchy
- Returns simple decision payload

**Key Limitation:** SDA is currently a thin wrapper, not a control plane. All logic lives in ConfidenceAnalyzer.

---

### 1.2 Confidence Scoring Pipeline

**Path:** `backend/confidence_engine/confidence_analyzer.py`

**Current Entry:** `ConfidenceAnalyzer.analyze(symbol, context)` and `analyze_payloads(...)`

**Current Decision Flow:**
```
1. Collect engine payloads:
   - trend_analyzer.get_overall_bias()
   - liquidity_analyzer.analyze()
   - ict_analyzer.analyze()

2. Infer direction (MSS-first logic)

3. Calculate scores (weighted sum):
   - daily_bias: 15 pts
   - h4_narrative: 20 pts
   - liquidity_sweep: 20 pts
   - mss: 20 pts
   - fvg_quality: 15 pts (scaled by grade A/B/C)
   - session_quality: 5 pts
   - target_clarity: 5 pts
   - smt: ±confidence (bonus/penalty)
   Total: 0-100 (capped)

4. Hard rejection checks:
   - evaluate_hard_rejections() [MIXED TIER LOGIC]
     - Killzone check
     - MSS presence
     - FVG presence
     - Direction alignment
     - RR ratio
     - News lock
     - Daily loss limit
     - Max trades limit
     - Forex-specific rules

5. Guardrail evaluation:
   - evaluate_guardrails() [SEPARATE SYSTEM]
     - Symbol allowed/blocked
     - Observer vs. production
     - Confidence minimum by symbol
     - Narrative phase penalties
     - SMT requirements

6. Final decision:
   decision = "APPROVED" if (total_confidence >= minimum AND not rejection_reasons) else "REJECTED"

7. Output:
   - total_confidence (0-100)
   - decision (APPROVED/REJECTED)
   - rejection_reasons (list)
   - guardrail status
   - confidence_band
   - explanation
```

**Problem:** Decision logic is spread across `evaluate_hard_rejections()` and `evaluate_guardrails()`. No clear tier hierarchy.

---

### 1.3 Guardrail System

**Path:** `backend/guardrails/strategy_guardrails.py`

**Current Behavior:**
```python
def evaluate(
    symbol: str,
    total_confidence: int,
    killzone: dict | None = None,
    smt: dict | None = None,
    narrative_phase: str | None = None,
    ...
) -> dict[str, Any]
```

**Current Rules:**
- Symbol execution tiers (production, filtered_production, observer_only)
- Minimum confidence by symbol type (priority: 90, forex: 95)
- Adaptive penalties (london_open: -12, london_continuation: -100, range_phase: -8)
- Adaptive bonuses (new_york_continuation: +5, smt_confirmation: +3)
- Narrative phase blocks (range, distribution)
- Forex-specific requirements
- Robustness 365D rules

**Problem:** Penalties are applied *to confidence score*, but can also return `reasons` for rejection. Logic is not strictly tiered.

---

### 1.4 Rejection Reason Handling

**Current Status:**
```
In ConfidenceAnalyzer.evaluate_hard_rejections():
  reasons = [...]  # List of reason strings
  
In StrategyGuardrails.evaluate():
  guardrail.get("reasons", [])  # Additional reasons
  
In final decision:
  decision = "APPROVED" if (...not rejection_reasons) else "REJECTED"
```

**Limitations:**
- Rejection reasons are simple strings, no structure
- No reason code, tier, severity, or confidence impact tracking
- No distinction between replay and live outcomes
- No way to measure QAER or FRR

---

### 1.5 Ticket/Admission Output

**Path:** `backend/execution_engine/assisted_execution_bridge.py`

**Current Ticket Structure:**
```python
@dataclass(frozen=True)
class LockedTradeTicket:
    ticket_id: str
    created_at: str
    expires_at: str
    symbol: str
    side: str
    entry_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_percent: float
    lot_size: float
    grade: str
    confidence: float
    strategy: str
    killzone: str
    rationale: str
    status: str = "CREATED"
```

**Current Statuses:**
- CREATED, AWAITING_APPROVAL, APPROVED, EXPIRED, REJECTED, SUBMITTED_DEMO, BLOCKED

**Problem:** No tier information, no reason ledger, no admission-stage data.

---

### 1.6 Observer Symbol Handling

**Current Status:**
```
In StrategyGuardrails DEFAULT_CONFIG:
    "symbol_execution_tiers": {
        "production": ["US30"],
        "filtered_production": ["XAUUSD"],
        "observer_only": ["EURUSD", "GBPUSD", "BTCUSD", "NAS100"],
    }

In guardrail evaluation:
    if symbol in observer_only:
        block execution
        (but still run analysis for diagnostics)
```

**Problem:** Observer blocking is in guardrails, not in tier hierarchy. No clean separation between decision logic and execution logic.

---

### 1.7 Replay vs. Live Mode Handling

**Current Status:**
```
In ConfidenceAnalyzer.analyze():
    decision = ConfidenceAnalyzer.analyze_payloads(
        symbol, 
        trend=..., 
        liquidity=..., 
        ict=..., 
        context=...
    )

In backtesting/historical_decision_brain.py:
    Calls the same analyze_payloads() with closed-candle payloads
    
Result: Same decision logic for both replay and live
```

**Problem:** No explicit mode-aware tier logic. If a tier needs different behavior in replay vs. live, there's no hook for it.

---

## 2. Tier Mapping to Current Code

### Tier 1: Absolute Hard Safety

**Constitution Requirement:**
- Daily loss exceeded → veto
- Max DD exceeded → veto
- Broker locked → veto
- News locked → veto
- Symbol locked → veto

**Current Implementation:**
```
ConfidenceAnalyzer.evaluate_hard_rejections():
  if context.get("daily_loss_limit_hit", False):
    reasons.append("Daily loss limit hit")
  if context.get("high_impact_news_lock_active", False):
    reasons.append("High impact news lock active")

StrategyGuardrails.evaluate():
  if risk_blocked:  # Tier 1?
    reasons.append(...)
```

**Status:** ✅ Mostly exists, but spread across two places. No severity ranking.

**Risk:** Tier 1 logic is NOT centralized. Hard to audit.

---

### Tier 2A: Macro Truth (Veto)

**Constitution Requirement:**
- HTF narrative alignment
- Macro liquidity existence
- Regime validation
- Weighted severity veto (2+ failures)

**Current Implementation:**
```
MISSING. Narrative is evaluated for penalties/bonuses, not severity-ranked veto.

get_narrative_status() returns phase, but doesn't weigh multiple macro failures.
```

**Status:** ❌ Does NOT exist as defined. Narrative is penalty-only, not tier.

**Risk:** Major gap. Macro truth is implicit, not explicit tier.

---

### Tier 2B: Macro Confidence (Penalty Only)

**Constitution Requirement:**
- Macro alignment strength
- Returns multiplier, never vetoes

**Current Implementation:**
```
StrategyGuardrails applies adaptive penalties:
  "london_open": -12 (penalty to confidence)
  "range_phase": -8
```

**Status:** ✅ Exists as confidence penalty, but not as explicit tier.

**Risk:** Penalties are ad-hoc, not systematic multiplier model.

---

### Tier 3A: Structural Validity (Veto)

**Constitution Requirement:**
- Valid MSS present
- Executable FVG exists
- Valid stop structure

**Current Implementation:**
```
ConfidenceAnalyzer.evaluate_hard_rejections():
  if not ict.get("mss", {}).get("detected"):
    reasons.append("MSS not confirmed")
  if not ict.get("fvg", {}).get("detected"):
    reasons.append("FVG not detected")
  if ict.get("premium_discount", {}).get("current_zone") == "unavailable":
    reasons.append("Premium/discount unavailable")
```

**Status:** ✅ Exists, but mixed with Tier 2A checks. Not separated.

**Risk:** Hard to distinguish Tier 3A from Tier 2A in current code.

---

### Tier 3B: Setup Quality (Scaling Only)

**Constitution Requirement:**
- SMT confluence score → risk multiplier
- FVG quality grade → risk multiplier
- Order block clarity → risk multiplier

**Current Implementation:**
```
ConfidenceAnalyzer.score_smt():
  if smt.get("direction") == direction:
    return confidence  # Bonus
  return -confidence  # Penalty

ConfidenceAnalyzer.score_fvg_quality():
  if grade == "A": return max_score
  if grade == "B": return max_score * 0.75
  if grade == "C": return max_score * 0.5

StrategyGuardrails applies penalties:
  "no_smt_confirmation": -5
```

**Status:** 🟡 Partially exists. Scoring exists but risk scaling not explicit.

**Risk:** No separate risk multiplier. Just confidence adjustment.

---

### Tier 4A: Portfolio Admission (Pre-Entry)

**Constitution Requirement:**
- Correlation with existing positions
- Sector overweight check
- Liquidity capacity

**Current Implementation:**
```
MISSING. No portfolio-level checks in current code.
```

**Status:** ❌ Does NOT exist.

**Risk:** Major gap. Can admit two correlated trades if both pass individual checks.

---

### Tier 4B: Execution Optimization (Post-Approval)

**Constitution Requirement:**
- A+ Override eligibility
- Exit target selection
- Risk enhancement

**Current Implementation:**
```
In backend/a_plus_override/a_plus_override_engine.py:
  Separate engine for A+ Override logic.
  
Currently called after main confidence decision, not as explicit tier.
```

**Status:** 🟡 Exists but separate from SDA. Not integrated as Tier 4B.

**Risk:** A+ logic is independent, not part of tier hierarchy.

---

### Tier 5: Trade Lifecycle / EFDE

**Constitution Requirement:**
- Early Failure Detection Engine
- Trade journal
- Exit recommendations

**Current Implementation:**
```
In backend/early_failure_detection/:
  Separate engine, called post-entry, not pre-entry.
```

**Status:** ✅ Exists but separate. Not part of admission tier.

**Risk:** EFDE is post-entry only, not part of SDA admission.

---

## 3. Pre-Implementation Risk Analysis

### Risk 1: Tier 2A Does Not Exist as Defined

**Issue:** Current code has no concept of "macro truth with weighted severity veto."

**Impact:** O2 refactor must ADD new logic for Tier 2A.

**Mitigation:**
- Extract narrative phase evaluation
- Create severity weights (htf_opposite: 0.50, macro_liquidity_absent: 0.30, etc.)
- Implement 2+ weighted failures → veto rule

**Complexity:** MEDIUM

---

### Risk 2: Tier 3A Mixed with Tier 2A

**Issue:** `evaluate_hard_rejections()` checks both macro truth (HTF narrative) and structural validity (MSS, FVG) without distinction.

**Impact:** Can't separate "macro rejection" from "structure rejection" in current code.

**Mitigation:**
- Create separate `_tier_3a_structural_validity()` method
- Move MSS, FVG, stop structure checks there
- Keep HTF narrative logic in Tier 2A

**Complexity:** MEDIUM

---

### Risk 3: Guardrail System Has Veto Power But No Tier

**Issue:** Guardrails can return `reasons` (veto) but also penalties. No tier structure.

**Impact:** Guardrails are Tier 1, 2A, 3B all mixed together.

**Mitigation:**
- Analyze guardrail rules and classify by tier
- Example: "symbol not allowed" = Tier 1 safety
- Example: "narrative phase penalty" = Tier 2B

**Complexity:** HIGH (requires careful rule categorization)

---

### Risk 4: Confidence Score Collapse

**Issue:** Total confidence = sum of all scores (0-100). No component decomposition per Law 7.

**Impact:** Can't answer "which component caused the rejection?"

**Mitigation:**
- Add `confidence_components` dict to output
- Preserve component scores (daily_bias, h4_narrative, liquidity, MSS, FVG, session, target, smt, macro_pressure)
- Calculate total from components but track both

**Complexity:** MEDIUM

---

### Risk 5: Replay/Live Outcome Divergence Untracked

**Issue:** No mechanism to track "would this have won in replay but rejected in live?"

**Impact:** Can't measure FRR (False Rejection Rate).

**Mitigation:**
- Add `replay_status` field to reason ledger
- In replay: after trade simulates, mark `would_have_won: true/false`
- In live: mark `unknown_until_resolved`
- Post-trade journal: update outcome

**Complexity:** HIGH (requires replay integration)

---

### Risk 6: Observer Symbols Not Cleanly Separated

**Issue:** Observer symbols can still generate rejections/admissions, contaminating production logs.

**Impact:** Tier 4A (Portfolio Admission) can't distinguish observer from production symbols.

**Mitigation:**
- Add `observer_status_check` early in SDA
- If observer_only symbol: bypass Tier 4 (portfolio checks), mark decision as "observer_only"
- Still generate reason ledger for diagnostics

**Complexity:** MEDIUM

---

### Risk 7: Reason Ledger Does Not Exist

**Issue:** Current code stores rejection reasons as list of strings. No structured ledger.

**Impact:** Can't calculate QAER, FRR, or root-cause metrics.

**Mitigation:**
- Create ReasonLedger dataclass per Constitution spec
- Mandatory fields: symbol, tier, reason_code, severity, confidence_before/after, risk_before/after
- Store in backend/shared/reason_ledger.py

**Complexity:** MEDIUM

---

### Risk 8: Confidence Minimum Threshold Is Ad-Hoc

**Issue:** Minimum confidence is per-symbol but scattered in code:
```
minimum_execution_confidence: 95
minimum_execution_confidence_by_symbol_type:
  priority: 90
  forex: 95
```

**Impact:** Dual Admission Logic (Law 3 exception path) needs explicit threshold. Currently implicit.

**Mitigation:**
- Centralize minimum confidence in SDA
- Make it parameterized (can be read from asset_profiles.py in O3)
- Document as PROPOSED threshold until Constitution specifies

**Complexity:** LOW

---

## 4. Proposed File Changes for O2

### O2 File Structure

```
backend/shared/
├── shared_decision_adapter.py          (REFACTOR)
├── asset_profiles.py                   (ALREADY DONE O1)
├── reason_ledger.py                    (NEW - O2A)
├── sda_tier_authority.py               (NEW - O2B)
└── sda_confidence_integration.py       (NEW - O2C)

tests/
├── test_shared_decision_adapter_tiers.py  (NEW - O2F)
└── test_reason_ledger.py                  (NEW - O2F)
```

### Detailed File Proposals

#### File 1: `backend/shared/reason_ledger.py` (NEW - O2A)

**Purpose:** Structured tracking of every rejection with reason, tier, severity, outcomes.

**Contains:**
```python
class ReasonCode(Enum):
    # Tier 1
    DAILY_LOSS_EXCEEDED
    MAX_DD_EXCEEDED
    BROKER_LOCKED
    NEWS_LOCKED
    SYMBOL_LOCKED
    
    # Tier 2A
    HTF_NARRATIVE_OPPOSITE
    MACRO_LIQUIDITY_ABSENT
    REGIME_TOXIC
    
    # Tier 3A
    MSS_ABSENT
    NO_EXECUTABLE_FVG
    INVALID_STOP_STRUCTURE
    
    # Tier 3B (penalty only, but may block if too severe)
    WEAK_SMT
    WEAK_FVG_QUALITY
    
    # Tier 4A
    PORTFOLIO_OVERWEIGHT
    CORRELATION_TOO_HIGH
    
    # Other
    UNKNOWN

@dataclass
class ReasonEntry:
    symbol: str
    timestamp: str
    tier: str  # "TIER_1", "TIER_2A", etc.
    reason_code: ReasonCode
    severity: float  # 0-1
    confidence_before: int
    confidence_after: int
    risk_before: float
    risk_after: float
    replay_status: str  # "would_have_won", "would_have_lost", "unknown_until_resolved"
    full_reasoning: dict
```

#### File 2: `backend/shared/sda_tier_authority.py` (NEW - O2B)

**Purpose:** Tier hierarchy wrapper without changing behavior yet.

**Contains:**
```python
class TierAuthority:
    def __init__(self, confidence_analyzer, guardrails, asset_profiles):
        self.ca = confidence_analyzer
        self.guardrails = guardrails
        self.profiles = asset_profiles
        self.reason_ledger = []
    
    def evaluate_tier_1_safety(self, context) -> bool:
        """Hard safety checks. Never soften."""
        
    def evaluate_tier_2a_macro_truth(self, context) -> tuple[bool, float]:
        """Macro validation. Returns (veto, severity)."""
        
    def evaluate_tier_2b_macro_confidence(self, context) -> float:
        """Macro confidence penalty. Returns multiplier."""
        
    def evaluate_tier_3a_structural_validity(self, context) -> bool:
        """MSS, FVG, stops. Never soften without evidence."""
        
    def evaluate_tier_3b_setup_quality(self, context) -> float:
        """SMT, FVG grade, OB clarity. Returns risk multiplier."""
        
    def evaluate_tier_4a_portfolio(self, context) -> bool:
        """Portfolio admission. Check correlation, overweight."""
        
    # Tiers 4B, 5 not in SDA (post-admission)
```

**Key Property:** BEHAVIOR-PRESERVING. Each tier calls existing engine logic, just reorganizes it.

#### File 3: `backend/shared/sda_confidence_integration.py` (NEW - O2C)

**Purpose:** Dual admission logic and confidence decomposition per Law 7.

**Contains:**
```python
class ConfidenceDecision:
    """Tracks confidence components and dual admission paths."""
    
    confidence_components: dict[str, int]  # {daily_bias, h4_narrative, liquidity, ...}
    total_confidence: int
    admission_path: str  # "standard" or "exceptional_macro"
    
    def calculate_components(self, scores: dict) -> dict:
        """Extract component scores from confidence engine output."""
        
    def evaluate_dual_admission(self, tier_2a_macro_grade, tier_3_risk_multiplier) -> bool:
        """
        Path 1: confidence >= threshold
        Path 2: macro_grade == STRONG and risk <= reduced_cap and confidence >= 0.65
        """
```

#### File 4: `backend/shared/shared_decision_adapter.py` (REFACTOR)

**Current:** ~94 lines, high-level pass-through

**New:** ~300-400 lines, tier-aware orchestration

**Changes:**
```python
class SharedDecisionAdapter:
    def __init__(
        self,
        confidence_analyzer,
        guardrails,
        asset_profiles,
        tier_authority,  # NEW
        reason_ledger,   # NEW
    ):
        self.confidence_analyzer = confidence_analyzer
        self.guardrails = guardrails
        self.asset_profiles = asset_profiles
        self.tier_authority = tier_authority
        self.reason_ledger = reason_ledger
    
    def evaluate(self, request: DecisionRequest) -> dict[str, Any]:
        """NEW: Orchestrate tier-based decision pipeline."""
        
        # [O2C] Tier 1: Hard safety
        tier_1_pass = self.tier_authority.evaluate_tier_1_safety(context)
        if not tier_1_pass:
            return self._record_rejection("TIER_1", ...)
        
        # [O2C] Tier 2A: Macro truth (weighted severity)
        tier_2a_pass, tier_2a_severity = self.tier_authority.evaluate_tier_2a_macro_truth(context)
        if not tier_2a_pass:
            return self._record_rejection("TIER_2A", ...)
        
        # [O2C] Tier 2B: Macro confidence (multiplier, never veto)
        tier_2b_multiplier = self.tier_authority.evaluate_tier_2b_macro_confidence(context)
        
        # ... continue through all tiers ...
        
        # [O2F] Dual admission logic
        admission = self._dual_admission_check(confidence, tier_2a_grade, tier_3_risk)
        
        # [O2F] Record reason ledger
        self._record_admission(setup_id, tiers_passed, ...)
        
        return decision_output
```

#### File 5: `tests/test_shared_decision_adapter_tiers.py` (NEW - O2F)

**Purpose:** Prove behavior is preserved while tier structure is added.

**Test Classes:**
```python
class TestTierStructure:
    def test_tier_1_hard_safety_still_blocks_daily_loss()
    def test_tier_2a_macro_still_checks_narrative()
    def test_tier_3a_structural_still_blocks_no_mss()
    ...

class TestBehaviorPreservation:
    def test_approval_rate_unchanged_after_refactor()
    def test_rejection_reasons_still_populated()
    def test_observer_symbols_still_blocked()
    ...

class TestReasonLedger:
    def test_reason_ledger_records_every_rejection()
    def test_reason_entry_has_required_fields()
    def test_replay_status_defaults_to_unknown()
    ...
```

---

## 5. Implementation Sequence (O2A → O2F)

### O2A: ReasonLedger Skeleton (1 file)

**File:** `backend/shared/reason_ledger.py`

**Scope:**
- Define `ReasonCode` enum
- Define `ReasonEntry` dataclass
- Create `ReasonLedger` collection class
- Add basic append/query methods
- NO integration yet (no calls from SDA)

**Tests:** Can create and populate ledger entries

**Duration Estimate:** 2-3 hours

---

### O2B: SDA Tier Wrapper Without Behavior Change (2 files + 1 test)

**Files:**
- `backend/shared/sda_tier_authority.py` (new)
- `backend/shared/shared_decision_adapter.py` (refactor)
- `tests/test_shared_decision_adapter_tiers.py` (new)

**Scope:**
- Extract tier logic from ConfidenceAnalyzer into TierAuthority
- Call existing engine methods (no new logic)
- Wrap SDA to call TierAuthority
- Verify 100% backward compatibility (same decisions output)

**Key Constraint:** BEHAVIOR MUST BE IDENTICAL. Every decision that was APPROVED should still be APPROVED. Every rejection should still reject for same reason.

**Tests:**
- Compare old SDA output vs. new SDA output on 500 replay trades
- Verify 100% decision match
- Verify rejection reasons are equivalent

**Duration Estimate:** 4-6 hours

---

### O2C: Hard Safety Integration (Tier 1 Mapping)

**Scope:**
- Implement `tier_authority.evaluate_tier_1_safety()`
- Move all Tier 1 logic (daily loss, DD, news lock, etc.) to one place
- Add severity ranking (all Tier 1 = severity 1.0, fatal)
- Call from new SDA pipeline

**Tests:**
- Tier 1 rejections still block trades
- Tier 1 reasons are recorded with severity 1.0

**Duration Estimate:** 2-3 hours

---

### O2D: Tier 2/3 Classification Scaffolding

**Scope:**
- Implement stubs for Tier 2A, 2B, 3A, 3B methods
- Mark which checks go where
- NO new veto logic (preserve current behavior)
- Document mapping of each engine check to tier

**Tests:**
- Decision logic unchanged
- Tier labels are correct in reason ledger

**Duration Estimate:** 3-4 hours

---

### O2E: Dual Admission Logic Placeholder

**Scope:**
- Implement `ConfidenceDecision.calculate_components()`
- Extract confidence_components from payloads
- Implement dual admission check (but don't change thresholds yet)
- Record which admission path was used

**Tests:**
- Components decompose correctly
- Dual admission logic evaluates but behavior unchanged

**Duration Estimate:** 2-3 hours

---

### O2F: Tests Proving Behavior Preserved

**Scope:**
- 41 tests verifying tier structure
- 15 tests verifying behavior preservation
- 8 tests verifying reason ledger
- All 514 baseline tests still pass

**Duration Estimate:** 4-5 hours

---

## 6. Risk Mitigation Strategy

### Strategy 1: Behavior-Preserving Refactoring

**How:** During O2B-O2E, every change compares old vs. new output on 500+ replay trades.

**Tool:** Create `tests/test_sda_regression.py`:
```python
def test_sda_refactor_preserves_all_decisions_on_500_trades():
    old_sda = SharedDecisionAdapter(ca)  # Current
    new_sda = NewSharedDecisionAdapter(ca, tiers, profiles)  # O2
    
    for setup in replay_500_trades:
        old_decision = old_sda.evaluate(setup)
        new_decision = new_sda.evaluate(setup)
        
        assert old_decision["decision"] == new_decision["decision"]
        assert old_decision["total_confidence"] == new_decision["total_confidence"]
        # (within tolerance)
```

**Pass Criteria:** 100% decision match, within 1 point confidence tolerance.

---

### Strategy 2: Layer-by-Layer Integration

**How:** Integrate tiers one at a time, validate before moving to next.

**Order:**
1. Tier 1 (safety) - simplest, most critical
2. Tier 2A/2B (macro) - well-defined, can parallelize
3. Tier 3A/3B (structure) - distinct from macro
4. Tier 4A (portfolio) - new logic, but additive
5. Dual admission (enhancement, not breaking change)

---

### Strategy 3: Explicit Separation of Concerns

**How:** Keep SDA ("orchestration"), ConfidenceAnalyzer ("intelligence"), Guardrails ("business rules") clearly separated.

**Constraint:** O2 refactor ONLY changes orchestration, not engines.

---

## 7. Proposed Testing Plan

### Phase 1: Unit Tests (O2A, O2B, O2D, O2E)

- ReasonLedger can store and retrieve entries
- Each tier method returns expected type (bool, float, etc.)
- Tier authority can be instantiated
- New SDA can be instantiated

### Phase 2: Integration Tests (O2C, O2F)

- New SDA produces identical decisions to old SDA on 500 trades
- Rejection reasons are populated correctly
- Reason ledger entries have all required fields
- Observer symbols still blocked

### Phase 3: Regression Tests

- All 514 baseline tests still pass
- No new test failures
- Confidence bands unchanged
- Guardrail reasons unchanged

---

## 8. Confirmation: No Code Modified

✅ **Status:** O2 is planning phase only.

**Current State:**
- ❌ No SDA refactored
- ❌ No tier logic implemented
- ❌ No reason ledger created
- ❌ No behavior changed

**Verification:**
- All 555 tests (514 baseline + 41 O1) still pass
- No files modified
- No execution logic changed
- Git status clean (except for this planning document)

---

## 9. Summary

**O2 Goal:** Refactor SDA into explicit tier hierarchy while preserving behavior.

**Key Challenges:**
1. Tier 2A (macro truth severity veto) doesn't exist—must add it
2. Tier 3A (structure validity) mixed with Tier 2A—must separate
3. Guardrails system has veto power but no tier classification
4. Confidence score collapses components—must decompose per Law 7
5. Reason ledger structured tracking missing—must create

**Timeline Estimate:** 18-24 hours spread over O2A-O2F phases

**Risk Profile:** MEDIUM (behavior-preserving refactoring is well-established pattern)

**Next Step:** Approval to proceed with O2A (ReasonLedger skeleton)

