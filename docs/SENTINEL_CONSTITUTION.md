# Sentinel Constitution

**Version:** 1.0  
**Date:** 2026-07-01  
**Branch:** sentinel-t2 (Tiered Decision Architecture & Governance Evolution)  
**Status:** Governance Framework (Pre-Implementation)

---

## 1. Purpose

This Sentinel Constitution defines the operating laws, decision authority model, admission pipeline, metrics, and replay-governed promotion rules for the Sentinel T2 evolution branch.

**Scope:**
- Establishes governance principles for admission control
- Defines tier hierarchy for decision authority
- Specifies measurability and explainability requirements
- Governs asset-specific policy through unified architecture
- Reserves replay-validated evidence as the sole path to production

**Non-Scope (Implementation Phase O1-O4):**
- Does not modify execution logic
- Does not modify guardrails
- Does not modify the Shared Decision Adapter
- Does not implement code

**Goal:**
Improve Sentinel's alpha yield by fixing admission-control bottlenecks—not by adding intelligence, but by better orchestrating the intelligence already built.

---

## 2. Core Diagnosis: The Three Admission-Control Failures

Sentinel does not primarily have an intelligence problem.

**It has an admission-control problem.**

### 2.1 Problem A: Overblocking

**Definition:** High-quality setups are rejected because a single weak or overly broad gate blocks them.

**Example:**
```
Good setup:
  - HTF narrative: bullish
  - Liquidity sweep: valid
  - Macro: mildly conflicted
  - SMT: weak

Result: Rejected because SMT weak.
Cost: Alpha loss. Trade would have won.
```

**Root Cause:** Guardrails are crude on/off switches. Any failure triggers veto.

### 2.2 Problem B: Flat Penalties

**Definition:** All weaknesses are treated identically, causing information loss and preventing nuanced decision-making.

**Example:**
```
Distribution phase = reject
(But not all distribution is equal.)

New York Continuation = one flat bucket
(But afternoon reversals differ from trend continuations.)

Weak SMT + weak FVG = same penalty as weak SMT alone
(But one is semantic, one is structural.)
```

**Root Cause:** Binary classification (good/bad) replaces probabilistic reasoning (degrees of confidence).

### 2.3 Problem C: Authority Ambiguity

**Definition:** Multiple engines vote without a clear hierarchy. Decision authority is implicit and fragile.

**Example:**
```
Market Watch says:  YES (bullish signal)
Narrative Engine says: NO (distribution phase)
A+ Override says: YES (strong confluence)
Guardrail says: NO (weak SMT)

Who wins?
→ Undefined. Result: inconsistent execution or accidental overrides.
```

**Root Cause:** No explicit tier hierarchy. All engines compete with equal veto power.

---

## 3. Seven Sentinel Laws

These laws govern all design, implementation, and operational decisions within Sentinel T2.

### Law 1: Same Brain, Different Profiles

**Statement:**  
All assets must use the same unified decision architecture, but may use asset-specific parameters.

**Interpretation:**
- One SDA (Shared Decision Adapter) serves all symbols.
- Each symbol has its own `ASSET_PROFILE` containing parameters.
- Intelligence engines are shared. Behavior is parameterized.
- Prevents code forking; enables behavioral specialization.

**Example:**
```
US30 profile:
  - valid_killzones: ["NEW_YORK_OPEN"]
  - min_liquidity_tf: "M15"
  - correlative_smt_symbol: "NAS100"

XAUUSD profile:
  - valid_killzones: ["LONDON_OPEN", "NY_OVERLAP"]
  - min_liquidity_tf: "H1"
  - correlative_smt_symbol: "DXY"

Same SDA. Different parameters.
```

---

### Law 2: Config Defines Parameters, Never Logic

**Statement:**  
Configuration may define thresholds, sessions, risk limits, asset behavior, and profile parameters. It must not become a second codebase or decide engine topology.

**Interpretation:**
- Good: `valid_killzones: ["NY_OPEN"]`, `base_risk: 0.003`, `min_liquidity_tf: "H1"`
- Bad: `"intelligence_engine": "MPV"` (that's code, not config)
- Bad: `"use_engine": "ICT_SMT"` (that's topology, not parameter)

**Why:**
If config chooses engines, maintenance becomes nightmare: you have two codebases and no single source of truth.

**Rule:**
- Config answers: "What are the bounds?"
- Code answers: "What is the logic?"

---

### Law 3: Only Hard Safety, Macro Truth, and Structural Invalidity May Veto

**Statement:**  
Weak confluence, weak SMT, mediocre FVG quality, imperfect killzone timing, or average order-block clarity may reduce confidence or risk, but must not independently veto a trade.

**Interpretation:**

**CAN VETO (Tier 1-2A):**
- Daily loss exceeded → veto
- Max DD exceeded → veto
- Broker locked → veto
- News locked → veto
- Symbol locked → veto
- HTF narrative opposite + macro absent (multiple Tier 2A failures) → veto
- No valid MSS → veto
- No executable FVG → veto
- Invalid stop structure → veto

**CAN ONLY PENALIZE (Tier 2B, 3B):**
- Weak SMT → reduce confidence
- Mediocre FVG grade → reduce risk
- Imperfect killzone timing → penalize
- Average order block → scale position
- Weak narrative alignment → apply multiplier

**Why:**
Single weak signals often lie. Requiring coordination prevents false rejections. Weak signals inform sizing, not admission.

---

### Law 4: Every Rejection Must Be Measurable

**Statement:**  
Every rejection must produce a reason code, tier, severity, confidence-before, confidence-after, risk-before, risk-after, and replay/live outcome status.

**Interpretation:**
Each rejection generates a ledger entry:
```python
{
  "symbol": "XAUUSD",
  "timestamp": "2026-07-01T14:30:00Z",
  "decision": "REJECT",
  "tier": "TIER_3A",
  "reason_code": "MSS_ABSENT",
  "severity": 0.6,  # 0-1 scale
  "confidence_before": 85,
  "confidence_after": 42,
  "risk_before": 0.003,
  "risk_after": 0.0,
  "replay_status": "would_have_won",  # in backtest only
  "full_reasoning": {...}
}
```

**Purpose:**
- Enables root-cause analysis of alpha leakage
- Identifies systemic overblocking patterns
- Drives optimization priorities
- Prevents blind tuning

**Metrics Derived:**
- QAER (Qualified Admission Efficiency Ratio)
- FRR (False Rejection Rate)
- Rejection reason distribution

---

### Law 5: Replay Governs Promotion

**Statement:**  
No advisory engine, asset profile, override, guardrail change, or execution policy may reach production without unified causal replay proof.

**Interpretation:**
- Advisory experiments (Market Watch, A+ Override, Portfolio Intelligence) run in replay only.
- When metrics are stable and strong (over 100+ causal trades), replay evidence is collected.
- Promotion requires:
  1. PF ≥ 1.75 (or target threshold)
  2. WR ≥ 58%
  3. DD < 4%
  4. No worse-than-baseline monthly loss clusters
  5. QAER ≥ 35% (not rejecting too many winners)
- Only then may the experiment move to live/assisted execution.

**Why:**
Live markets are unforgiving. Backtest-only improvements often evaporate. Causal replay forces discipline.

---

### Law 6: Every Veto Must Be Severity-Ranked

**Statement:**  
Veto logic must not rely only on raw failure counts. Each veto-capable failure must include severity, tier, and reason code.

**Interpretation:**
Do NOT do this:
```python
if len(failures) >= 2:
    veto()
```

DO THIS:
```python
severity_total = sum(failure_weights)
if severity_total >= 0.8:
    veto()
```

**Example Severity Weights:**
```
htf_narrative_opposite: 0.50
macro_liquidity_absent: 0.30
regime_toxic: 0.60
```

**Why:**
Not all failures are equal. `[HTF_opposite, Regime_toxic]` is more dangerous than `[macro_liquidity_absent, regime_toxic]`. Severity weighting reflects domain knowledge.

---

### Law 7: Every Score Must Be Decomposable

**Statement:**  
Aggregate confidence scores must preserve component-level evidence.

**Interpretation:**
Do NOT collapse into a single opaque number:
```python
confidence = 83
→ Where did 83 come from? Unknown.
```

DO preserve components:
```python
confidence_components = {
  "daily_bias": 15,
  "h4_narrative": 20,
  "liquidity": 12,
  "mss": 20,
  "fvg": 11,
  "session": 8,
  "target_clarity": 10,
  "macro_pressure": 5
}
confidence = sum(confidence_components.values())  # 101 → normalized to 85
```

**Purpose:**
- Later, ask: "Which components correlate with false rejections?"
- Optimize based on evidence, not opinion
- Maintains explainability for human traders

---

## 4. Tiered Admission Authority

The SDA (Shared Decision Adapter) enforces seven tiers of decision authority.

### Tier Structure

```
┌─────────────────────────────────────────────────────┐
│ Tier 1: ABSOLUTE HARD SAFETY (Veto Power)         │
│ • Daily loss exceeded                              │
│ • Max DD exceeded                                  │
│ • Broker locked                                    │
│ • News locked                                      │
│ • Symbol locked                                    │
│ • Spread/slippage violation                        │
│ Action: Veto. Never override. (Except human)       │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ Tier 2A: MACRO TRUTH (Veto if Multiple Failures)  │
│ • HTF narrative alignment                          │
│ • Macro liquidity existence                        │
│ • Regime validation                                │
│ Veto Rule: 2+ failures → veto (by weighted severity)│
│ Action: Veto or pass. Severity-ranked.             │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ Tier 2B: MACRO CONFIDENCE (Penalty Only)           │
│ • Macro alignment strength                         │
│ • Liquidity pool clarity                           │
│ • Regime transition confidence                     │
│ Action: Return multiplier (never veto)             │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ Tier 3A: STRUCTURAL VALIDITY (Veto if Missing)    │
│ • Valid MSS present                                │
│ • Executable FVG or OB exists                      │
│ • Valid stop structure                             │
│ Action: Veto if any fails. No leeway.              │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ Tier 3B: SETUP QUALITY (Scaling Only)              │
│ • SMT confluence score                             │
│ • FVG quality grade                                │
│ • Order block clarity                              │
│ Action: Return risk multiplier (never veto)        │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ Tier 4A: PORTFOLIO ADMISSION (Pre-Entry Check)     │
│ • Correlation with existing positions              │
│ • Sector overweight check                          │
│ • Liquidity capacity                               │
│ Action: Veto or pass. Portfolio-level decision.    │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ Tier 4B: EXECUTION OPTIMIZATION (Post-Approval)    │
│ • A+ Override eligibility                          │
│ • Exit target selection                            │
│ • Risk enhancement                                 │
│ Action: Enhance approved trade. Cannot reject.     │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ Tier 5: TRADE LIFECYCLE (Post-Entry Monitoring)    │
│ • EFDE (Early Failure Detection)                   │
│ • Trade journal                                    │
│ • Exit recommendations                             │
│ Action: Inform. Monitor. Recommend. Cannot block.  │
└─────────────────────────────────────────────────────┘
```

### Tier Authority Rules

1. **Tier 1 rejects → Trade blocked. Period.** No lower tier can override.
2. **Tier 2A rejects → Trade blocked** unless multiple Tier 2A failures fail to reach severity threshold.
3. **Tier 2B rejects → Cannot happen.** Always returns multiplier.
4. **Tier 3A rejects → Trade blocked.** Structural invalidity is fatal.
5. **Tier 3B rejects → Cannot happen.** Always returns risk multiplier.
6. **Tier 4A rejects → Trade blocked.** Portfolio safety overrides individual setup quality.
7. **Tier 4B cannot reject.** Only enhances.
8. **Tier 5 cannot reject.** Only monitors and informs.

### Dual Admission Logic

A setup may be admitted via two paths:

**Path 1: Standard**
```
Tier 1 passes
AND Tier 2A passes
AND Tier 3A passes
AND confidence_final >= threshold
→ ADMIT
```

**Path 2: Exceptional (Strong Macro)**
```
Tier 1 passes
AND Tier 2A passes
AND Tier 3A passes
AND macro_grade == "STRONG"
AND risk_allocation <= reduced_risk_cap
AND confidence_final >= 0.65  # minimum baseline
→ ADMIT (reduced risk)
```

**Purpose:** Exceptional macro strength can admit lower-confidence reduced-risk setups without requiring full confidence threshold.

---

## 5. SDA Control Plane Principle

The Shared Decision Adapter (SDA) is Sentinel's control plane. It is not a collection of loosely coupled engines.

### SDA as Orchestration Layer

The SDA:
- Does NOT contain intelligence logic (engines do that)
- DOES enforce tier hierarchy
- DOES coordinate engine outputs
- DOES track reason ledger
- DOES measure QAER, FRR, AER

The SDA pipeline:

```
setup_candidate
  ↓
[Tier 1: Safety Gate]    → veto? → REJECT
  ↓ pass
[Tier 2A: Macro Truth]   → veto? → REJECT
  ↓ pass
[Tier 2B: Macro Conf]    → penalty multiplier
  ↓
[Tier 3A: Struct Valid]  → veto? → REJECT
  ↓ pass
[Tier 3B: Setup Quality] → risk multiplier
  ↓
[Tier 4A: Portfolio]     → veto? → REJECT
  ↓ pass
[Dual Admission Check]   → (standard or exceptional path?)
  ↓
[Tier 4B: Optimization]  → enhance if approved
  ↓
ADMIT or REJECT
```

### SDA Properties

- **Deterministic:** Same input → same output (no randomness)
- **Explainable:** Every decision tracked with reason ledger
- **Measurable:** Every step produces metrics
- **Auditable:** Full history preserved for replay
- **Testable:** Each tier can be validated independently

---

## 6. Reason Ledger

Every rejection produces a structured entry in the reason ledger.

### Ledger Schema

```python
RejectionEntry = {
  # Identity
  "symbol": str,
  "timestamp": ISO8601,
  "setup_id": str,  # unique per setup
  
  # Decision
  "decision": "REJECT",
  "tier": str,  # "TIER_1", "TIER_2A", etc.
  "reason_code": str,  # "MSS_ABSENT", "MACRO_OPPOSITE", etc.
  "severity": float,  # 0-1 scale
  
  # Confidence Lifecycle
  "confidence_before": float,
  "confidence_after": float,
  "confidence_components_before": dict,  # {bias, narrative, liquidity, ...}
  "confidence_components_after": dict,
  
  # Risk Lifecycle
  "risk_before": float,  # as % of account
  "risk_after": float,   # as % of account
  "risk_modifiers": dict,  # {tier_2b: 0.9, tier_3b: 0.8, ...}
  
  # Outcome (Replay Only)
  "replay_status": str,  # "would_have_won", "would_have_lost", "inconclusive"
  "replay_rr": float,  # R:R if trade had executed
  
  # Full Reasoning
  "full_reasoning": dict,  # {vetoes, checks, penalties, decisions}
}
```

### Aggregated Metrics

After 500+ trades:

```
Rejection Reason Breakdown:
  MSS_ABSENT:               189 (38%)   [False: 71]
  WEAK_SMT:                 124 (25%)   [False: 32]
  LATE_KILLZONE:             98 (20%)   [False: 15]
  MACRO_LIQUIDITY_ABSENT:    48 (10%)   [False: 8]
  PORTFOLIO_OVERWEIGHT:      28 (6%)    [False: 2]

Tier Veto Breakdown:
  TIER_1:      89 (legitimate safety blocks)
  TIER_2A:    102 (macro truth failures)
  TIER_3A:    189 (structural invalidity)
  TIER_4A:     28 (portfolio conflicts)

False Rejection Count: 128 / 476 = 26.9%
```

---

## 7. Metrics: QAER, FRR, AER

### QAER: Qualified Admission Efficiency Ratio

```
QAER = winning_trades_admitted / high_quality_candidates

where:
  high_quality_candidates = candidates with confidence_score >= 70
  winning_trades_admitted = those that passed admission and won
```

**Interpretation:**
- Measures alpha strangulation on *good* setups only
- High QAER (>50%): Admitting most winners
- Low QAER (<20%): Rejecting too many winners

---

### FRR: False Rejection Rate

```
FRR = profitable_rejected_candidates / high_quality_candidates

where:
  profitable_rejected_candidates = in replay, would have won
  high_quality_candidates = confidence_score >= 70
```

**Interpretation:**
- Direct measure of alpha leakage
- High FRR (>30%): Overblocking problem
- Low FRR (<10%): Admission gates calibrated well

---

### AER: Admission Efficiency Ratio (Raw)

```
AER = winning_trades_admitted / total_candidates_detected

where:
  total_candidates_detected = all setups that triggered a signal
```

**Note:** AER is broader than QAER. QAER is the refined version for "was this a genuinely good setup?"

---

## 8. Asset Profile Principle

### Asset Profile Structure

Each symbol has an `ASSET_PROFILE` that parameterizes (but does not change) the unified architecture:

```python
ASSET_PROFILES = {
  "US30": {
    "execution_allowed": True,
    "intelligence_mode": "ICT_SMT",
    "correlative_smt_symbol": "NAS100",
    "valid_killzones": ["NEW_YORK_OPEN"],
    "min_liquidity_tf": "M15",
    "base_risk_allocation": 0.003,
    "macro_enabled": False,
  },
  "XAUUSD": {
    "execution_allowed": True,
    "intelligence_mode": "MACRO_PRESSURE_VECTOR",
    "macro_inputs": ["DXY", "US10Y", "RealYields", "SPX_Sentiment"],
    "valid_killzones": ["LONDON_OPEN", "NY_OVERLAP"],
    "min_liquidity_tf": "H1",
    "base_risk_allocation": 0.0015,
    "macro_enabled": True,
  },
  "NAS100": {
    "execution_allowed": False,  # Observer only
    "intelligence_mode": "ICT_SMT",
    "correlative_smt_symbol": "US30",
    "valid_killzones": ["NEW_YORK_OPEN", "LONDON_OPEN"],
    "min_liquidity_tf": "M15",
    "use_for_correlation": True,  # Teaches SMT
  },
}
```

### Profile Parameters (Allowed)

- ✅ `execution_allowed`
- ✅ `valid_killzones`
- ✅ `min_liquidity_tf`
- ✅ `base_risk_allocation`
- ✅ `correlative_smt_symbol`
- ✅ `macro_inputs`
- ✅ `macro_enabled`
- ✅ Risk thresholds, timeframe ranges, session windows

### Profile Logic (NOT Allowed)

- ❌ `"intelligence_engine": "MPV"` (that's code, not config)
- ❌ Conditional business logic inside profile
- ❌ Engine topology decisions

---

## 9. Initial Asset Scope

### Production Symbols (Execution Allowed)

1. **US30** (S&P 500 Index)
   - ICT + SMT intelligence
   - NY Open sessions
   - Validated baseline: PF 1.69, WR 60%, DD 2.0%

2. **XAUUSD** (Gold)
   - Macro Pressure Vector intelligence
   - London/NY Overlap sessions
   - Current baseline: PF 1.0, WR 50%, DD 0.99%
   - **Goal:** Improve from break-even to 1.5+ PF via MPV specialization

### Observer Symbols (Diagnostic Only, No Execution)

1. **NAS100** (NASDAQ-100)
   - ICT + SMT intelligence
   - Observer baseline: PF 2.0, WR 65.22%, DD ~2.0%
   - **Promotion Gate:** 100+ causal trades at PF ≥ 1.75 before live execution

2. **EURUSD** (Euro/US Dollar)
   - FX-specific macro intelligence
   - Observer baseline: PF 1.06, WR 51.39%
   - **Status:** Keep observer. Do not promote.

3. **GBPUSD** (British Pound/US Dollar)
   - FX-specific macro intelligence
   - Observer baseline: PF 1.03, WR 50%
   - **Status:** Keep observer. Do not promote.

4. **BTCUSD** (Bitcoin)
   - 24/7 volatility-driven intelligence
   - Observer baseline: No setups currently
   - **Status:** Observer only until crypto-specific sessions proven

---

## 10. Gold Macro Pressure Vector Principle

Gold (XAUUSD) currently fails because it is forced through index-family logic.

### Gold Does Not Trade Like an Index

**Indices (US30, NAS100):**
- Driven by corporate earnings, sector rotation
- Correlated with other indices
- SMT = inter-index divergence (US30 vs NAS100)

**Gold:**
- Driven by macro: DXY, real yields, rate expectations, risk sentiment
- Inverse to strong dollar, inverse to high rates
- SMT = macro-to-commodity divergence (DXY divergence from gold movement)

### Gold Macro Pressure Vector (MPV) Approach

Instead of SMT, Gold uses Macro Pressure Vector:

**Inputs:**
- DXY (US Dollar Index)
- US10Y (10-Year Treasury Yield)
- Real Yields (derived from inflation expectations)
- SPX Sentiment (risk-on/off indicator)
- VIX (volatility spillover)

**Calculation (Phase 1: Rule-Based):**
```
bullish_gold_pressure = (strong_dollar_weakness + high_real_yields + risk_on)
bearish_gold_pressure = (strong_dollar + rising_real_yields + risk_off)

result = BULLISH | BEARISH | CONFLICTED | NEUTRAL
```

**Weights (Phase 2: After Empirical Tuning):**
```
pressure_score = (
  -0.40 * dxy_trend +        # DXY weakness = bullish gold
  -0.25 * us10y_trend +      # Yield rises = bearish gold (eventually)
  +0.20 * real_yield_trend + # Real yields matter for gold demand
  +0.15 * spx_sentiment +    # Risk-on (positive SPX sentiment) = bear gold
  +0.10 * vix_level          # Volatility spike = flight to gold
)
```

**Initial Implementation:**
- Start with rule-based classification (BULLISH/BEARISH/CONFLICTED)
- Collect 50+ trades in replay
- If stable and profitable, optimize to weighted scoring
- Validate with QAER/FRR

---

## 11. Promotion Rules

### Rule: NAS100 Automatic Promotion Gate

**Condition:**
- Observer mode currently
- 24-trade sample in replay (too small)

**Promotion Trigger:**
1. Run continuous 100+ trade causal replay
2. Measure: PF ≥ 1.75, WR ≥ 60%, DD < 4%
3. If all three met: Automatically promote to assisted execution tickets
4. Track QAER ≥ 35%

**Why:**
NAS100 shows strong metrics (PF 2.0 in current small sample). With more replay evidence, it can join production safely.

### Rule: Gold MPV Iterative Improvement

**Condition:**
- Currently: PF 1.0 (break-even)
- Current approach: forced through index logic

**Iteration 1 (Replay):**
1. Implement MPV rule-based classification
2. Run 50+ trades replay
3. Measure QAER, FRR
4. If QAER < 20% or FRR > 40%, iterate signal

**Iteration 2 (Weight Tuning):**
1. If Iteration 1 shows promise (QAER > 30%, FRR < 30%)
2. Collect regression weights from replay data
3. Replace rule-based with weighted scoring
4. Validate on holdout replay period

**Iteration 3 (Live Paper):**
1. Run on live data (demo account)
2. Track vs replay: Do live metrics match?
3. If yes: Candidate for assisted execution
4. If no: Debug divergence, iterate

---

## 12. Out of Scope

This Constitution does NOT govern:

- **Specific Engine Implementations:** Individual engine algorithms are implementation details (Liquidity Engine, Narrative Engine, etc.)
- **Entry Signal Logic:** What price action triggers a setup candidate
- **Exit Logic:** TP/SL calculation
- **Position Sizing:** How to calculate position size (that's execution logic)
- **Telegram Alerts:** Alert format, timing, frequency
- **Dashboard Display:** UI/UX choices
- **Live Broker Integration:** Specific broker API calls

This Constitution ONLY governs:
- Decision authority hierarchy
- Admission control flow
- Reason tracking and metrics
- Replay validation rules
- Asset profile structure

---

## 13. Final Directive

Sentinel T2 is governed by this Constitution.

All implementation decisions during phases O1-O4 must:

1. **Preserve Law 1:** Same unified architecture, asset-specific profiles only
2. **Respect Law 3:** Only hard safety, macro truth, and structural invalidity may veto
3. **Enable Law 4:** Every rejection produces a measurable reason ledger entry
4. **Obey Law 5:** No experiment reaches production without replay proof
5. **Implement Law 6:** Severity-rank all vetoes
6. **Preserve Law 7:** Keep confidence scores decomposable

The goal is **not perfection**. The goal is:

- **Clarity:** Know why each trade was admitted or rejected
- **Measurability:** Prove overblocking via QAER, FRR
- **Governance:** Enforce tier hierarchy, not chaos
- **Evolution:** Let replay evidence, not opinion, drive optimization

---

**Constitution Approved For Implementation Phase O1-O4**

Next step: Code O1 (Asset Profiles) in isolated sentinel-t2 branch.

---

**Document Status:**
- ✅ Governance Framework Complete
- ✅ Seven Laws Established
- ✅ Tier Hierarchy Defined
- ✅ Metrics Specified
- ✅ Asset Strategy Outlined
- ⏳ Ready for O1 Implementation
