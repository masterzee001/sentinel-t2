# Master Logic Consistency Audit - Project Sentinel

Generated: 2026-06-30T13:31:01.504166+00:00
Mode: Deep forensic audit, report-only. No code, config, live execution, or production rule changes were made.

## Executive Decision
- Final decision: CONFLICT_FOUND
- Production baseline preserved: True
- Metric isolation: PASS
- Execution safety: PASS
- Conflict severity counts: {'HIGH': 1, 'LOW': 1, 'MEDIUM': 3}
- Safe to continue live validation: True
- Requires diagnostic/reporting hotfix: True

## Baseline Context
- Original Elite: PF 2.84, WR 72.6%, Trades 151, DD 3.72%
- Current Production: PF 1.58, WR 58.7%, Trades 56, DD 2.97%
- Trade count gap: 95 trades

## Signal Funnel Consistency
- Source: data/reports/post_sprint_16_regression_diagnostics.json and production-filtered routing records
- Total scans: 4802
- Qualifying setups: 118
- HOT setups reported: 20
- EXECUTION_READY setups reported: 52
- Approved trades: 56
- Exclusive band counts: {'EXECUTION_READY': 52, 'HOT': 20}
- Correct cumulative HOT_OR_BETTER: 72
- Diagnosis: BUG_FOUND_IN_REPORTING_SEMANTICS
- Explanation: HOT and EXECUTION_READY were counted as mutually exclusive buckets, not cumulative funnel stages. EXECUTION_READY > HOT is valid for bucket distribution but invalid if the labels are interpreted as a funnel.
- Required fix: Report both exclusive_band_counts and cumulative_funnel_counts; label HOT as HOT_ONLY or use HOT_OR_BETTER for funnel stage.

## Confidence Band Consistency
- Status: CONFLICT_FOUND
- Central authority: PARTIAL_ONLY
- Can same symbol show different bands: True
- Raw/adjusted separation: IMPROVED_FOR_DISPLAY_BUT_NOT_FULLY_CENTRALIZED

### Band Sources
- backend/confidence_engine/confidence_analyzer.py :: ConfidenceAnalyzer.get_confidence_band: {'COLD': '<=39', 'EXECUTION_READY': '>=90', 'HOT': '70-89', 'WARM': '40-69'} (raw production confidence band)
- backend/guardrails/strategy_guardrails.py :: StrategyGuardrails.adjust_confidence_band: {'COLD': '<40', 'EXECUTION_READY': '>=95', 'HOT': '>=70', 'WARM': '>=40'} (guardrail-adjusted execution band from config/strategy_guardrails.yaml)
- backend/display/confidence_display.py :: confidence_state_for_score: {'COLD': '<=39', 'EXECUTION_READY': '>=90', 'HOT': '70-89', 'WARM': '40-69'} (display-only duplicate of raw confidence band)
- backend/backtesting/backtest_engine.py :: BacktestEngine.build_historical_plan: {'EXECUTION_READY': 'confidence >= minimum_confidence', 'HOT': 'confidence < minimum_confidence'} (historical scan emits only HOT/EXECUTION_READY candidates)
- backend/shadow_learning/shadow_learning_engine.py :: confidence_band: {'COLD': '<40', 'EXECUTION_READY': '>=90', 'HOT': '70-89', 'WARM': '40-69'} (shadow learning duplicate band mapper)
- backend/observer/btc_observer.py :: classify_observer_state/build_confidence: {'COLD': 'else', 'HOT': 'M15 magnitude >= 2.0%', 'WARM': 'M15 magnitude >= 0.75%'} (observer movement state; not production confidence band)
- backend/observer/nas100_observer.py :: classify_observer_state/build_confidence: {'COLD': 'else', 'HOT': 'M15 magnitude >= 1.5%', 'WARM': 'M15 magnitude >= 0.5%'} (observer movement state; not production confidence band)

### Band Conflicts
- Raw production EXECUTION_READY starts at 90, guardrail-adjusted EXECUTION_READY starts at 95.
- Display helper duplicates raw thresholds instead of importing ConfidenceAnalyzer threshold authority.
- Shadow learning duplicates threshold logic.
- Backtest emits only HOT/EXECUTION_READY, so COLD/WARM distributions are not comparable to live scans.
- Observer HOT/WARM/COLD are movement states, not raw confidence bands; they can show HOT at scores such as 53 by design but need clear labeling.

## Grade Lock Audit
- Status: VALID_CURRENT_BLOCKS_BUT_LABEL_AMBIGUITY_FOUND
- Duplication risk: Prior regression called confidence<90 grade_lock; this duplicates confidence threshold semantics rather than proving an actual grade-classification lock.
- Routing proxy: total 20, valid/good 20, false/bad 0, leak 0.0%, leaked RR/profit 0, prevented loss 0
- Actual guardrail attribution: total 2, valid/good 2, false/bad 0, leak 0.0%, leaked RR/profit 0.0, prevented loss 1.2

Logic sources:
- config/rule_weights.yaml: minimum_confidence 90 across conservative/balanced/aggressive
- backend/confidence_engine/confidence_analyzer.py :: evaluate_hard_rejections/get_minimum_confidence: confidence below minimum threshold; forex minimum 95
- backend/a_plus_override/a_plus_override_engine.py :: override_eligible: grade A+, confidence >=90, execution_ready=True
- backend/execution_engine/assisted_execution_bridge.py :: final_safety_gate: assisted execution allowed_grades currently A+ only

## MSS Bottleneck Audit
- Status: VALID_BLOCKS_IN_CURRENT_ROUTING_DATA
- Bottleneck: M15/closed-candle displacement requirement can be slow, while M1/M5 memory is advisory-only and cannot satisfy MSS yet.
- Opportunity leak estimate: 0
- Routing proxy: total 17, valid/good 17, false/bad 0, leak 0.0%, leaked RR/profit 0, prevented loss 0

Logic sources:
- backend/ict_engine/ict_analyzer.py :: ICTAnalyzer.detect_mss: after liquidity sweep, candle close must break prior 12-candle structure and displacement_score >= 60 Timeframe: execution candles supplied by caller; current live/backtest flow is M15-centric
- backend/confidence_engine/confidence_analyzer.py :: evaluate_hard_rejections: adds MSS not confirmed when ict.mss.detected is false Timeframe: n/a
- backend/backtesting/backtest_engine.py :: scan_symbol: passes mss_confirmed=True into StrategyGuardrails during historical scan, so backtest guardrail attribution does not test MSS false paths Timeframe: n/a

## Killzone Restriction Audit
- Status: REPORTING_PROXY_BUG_FOUND
- Explanation: The prior regression count treated non-NY sessions as killzone blocks. That is not the same as actual guardrail killzone blocks; London open is configured as valid for XAUUSD.
- Routing proxy non-NY: total 19, valid/good 7, false/bad 12, leak 63.16%, leaked RR/profit 19.2, prevented loss 0
- Actual guardrail attribution: total 2, valid/good 2, false/bad 0, leak 0.0%, leaked RR/profit 0.0, prevented loss 2.0

Logic sources:
- config/killzones.yaml: London open 08:00-09:30; London continuation 09:30-11:00; NY open 13:30-15:00; NY continuation 15:00-16:00 WAT.
- backend/confidence_engine/confidence_analyzer.py :: is_valid_killzone/evaluate_hard_rejections: invalid killzone is hard rejection
- backend/guardrails/strategy_guardrails.py :: hard_block_reasons/evaluate_adaptive: london_continuation is blocked; london_open can be penalized when SMT conditions fail

## Production Routing Rejection Quality
- MSS not confirmed: total 17, valid/good 17, false/bad 0, leak 0.0%, leaked RR/profit 0, prevented loss 0
- grade_lock: total 20, valid/good 20, false/bad 0, leak 0.0%, leaked RR/profit 0, prevented loss 0
- killzone_proxy_non_ny: total 19, valid/good 7, false/bad 12, leak 63.16%, leaked RR/profit 19.2, prevented loss 0
- no_trade: total 15, valid/good 15, false/bad 0, leak 0.0%, leaked RR/profit 0, prevented loss 0
- risk_lock: total 0, valid/good 0, false/bad 0, leak 0.0%, leaked RR/profit 0, prevented loss 0
- symbol_lock: total 0, valid/good 0, false/bad 0, leak 0.0%, leaked RR/profit 0, prevented loss 0

## Actual Guardrail Attribution
- grade_lock: total 2, valid/good 2, false/bad 0, leak 0.0%, leaked RR/profit 0.0, prevented loss 1.2
- killzone: total 2, valid/good 2, false/bad 0, leak 0.0%, leaked RR/profit 0.0, prevented loss 2.0
- no_trade: total 2, valid/good 0, false/bad 2, leak 100.0%, leaked RR/profit 2.6, prevented loss 0.0
- risk_lock: total 2, valid/good 0, false/bad 2, leak 100.0%, leaked RR/profit 2.4, prevented loss 0.0
- symbol_lock: total 2, valid/good 0, false/bad 2, leak 100.0%, leaked RR/profit 3.0, prevented loss 0.0

## Metric Isolation Audit
- Status: PASS
- Production symbols: ['US30', 'XAUUSD']
- Excluded symbols: ['BTCUSD', 'EURUSD', 'GBPUSD', 'NAS100']
- Validation matches approved baseline: True
- Production metrics: PF 1.58, WR 58.7%, Trades 56, DD 2.97%

- Evidence: scripts/run_backtest_365d.py splits production_trades and observer_trades before metrics.
- Evidence: build_production_payload returns approved_robustness_baseline when symbol_expansion.observer_only=true and affect_production=false.
- Evidence: observer_diagnostics explicitly set execution_allowed=false and production_excluded=true.
- Evidence: SymbolRegistry.execution_allowed returns true only for PRODUCTION tier.

## Execution Safety Audit
- Status: PASS
- Assisted mode: DEMO_ONLY
- Assisted submit_orders: False
- Assisted broker orders: False
- Assisted autonomous execution: False
- Sandbox enabled: False
- Sandbox mode: DEMO_ONLY
- Sandbox submit_orders: False

- Evidence: AssistedExecutionBridge.dry_run always returns order_send_called=false.
- Evidence: submit_demo_order blocks before order_send when final_safety_gate fails or submit_orders=false.
- Evidence: final_safety_gate checks enabled, DEMO_ONLY mode, demo account, human approval, freshness, symbol lock, grade lock, risk lock, kill switch, spread/slippage, duplicate lock, SL/TP, lot-size risk, and broker_submission_global_override=false.
- Evidence: Telegram /exec_approve and /execute_approve route through AssistedExecutionBridge and return dry-run when submit_orders=false.
- Evidence: Sandbox approval uses DemoSandboxEngine and sandbox config with enabled=false and submit_orders=false by default.

## Subsystem Conflict Register
- BAND_THRESHOLD_DRIFT [MEDIUM]: Raw confidence EXECUTION_READY begins at 90, guardrail-adjusted EXECUTION_READY begins at 95, A+ override eligibility uses 90 plus execution_ready.
- FUNNEL_EXCLUSIVE_BUCKETS_LABELED_AS_STAGES [MEDIUM]: HOT=20 and EXECUTION_READY=52 are exclusive band counts, but report labels them as funnel stages.
- REJECTION_BUCKET_PROXY_MISLABEL [HIGH]: Prior killzone count used non-NY proxy, not actual guardrail killzone block. Grade lock proxy used confidence<90, not actual grade classification.
- BACKTEST_MSS_BYPASS_IN_GUARDRAIL_EVALUATION [MEDIUM]: BacktestEngine.scan_symbol passes mss_confirmed=True into StrategyGuardrails, while live confidence can hard-reject MSS not confirmed.
- OBSERVER_STATE_OVERLOAD [LOW]: Observer HOT/WARM/COLD are movement states and not raw confidence thresholds; display label now says observer/demo sandbox but source semantics remain separate.

## Final Authority Map
- advisory_recommendations: AI Policy/A+ Override/EFDE/Memory are report-only; no final execution authority
- execution: AssistedExecutionBridge.final_safety_gate plus SymbolRegistry.execution_allowed and submit_orders flag
- production_metrics: scripts/run_backtest_365d.py production portfolio split plus approved robustness baseline when affect_production=false

## Alpha Suppression Analysis
1. Production baseline/portfolio gate: 151 elite advisory trades vs 56 approved production trades; 95-trade gap is mostly policy isolation, not a scoring failure.
2. Advisory candidates not promoted to production: Shadow enhanced identified +24 hypothetical trades with DD near ceiling; intentionally not production.
3. Symbol/sandbox isolation: BTCUSD/NAS100/EURUSD/GBPUSD can carry diagnostic opportunity but are excluded from production metrics and execution.
4. No-trade/risk/symbol-lock guardrails: GOE attribution shows production bad blocks for no_trade/risk_lock/symbol_lock, but these remain advisory review candidates.
5. MSS and grade filters: Current production-filtered routing data shows 0 false blocks for MSS and confidence<90 grade proxy, so current evidence does not support relaxing them.

- Primary root cause: Production and elite/advisory metrics are different lanes; elite improvements have not been promoted into production policy.
- Secondary root cause: Reporting ambiguity makes suppression look like grade/MSS/killzone failure even when actual guardrail attribution is cleaner.

## Recommendations
- Centralize confidence band thresholds into one shared module/config contract and have display/shadow/backtest import it.
- Fix regression diagnostics to separate exclusive_band_distribution from cumulative_signal_funnel.
- Replace rejection proxy labels with actual rejection reasons from confidence/guardrail payloads; never label non-NY as killzone block.
- Align historical backtest MSS handling with live confidence, or label backtest MSS as synthetic/not evaluated.
- Keep grade/MSS strictness unchanged until false-block evidence appears; current production-filtered data shows 0 false blocks for both.

## Final Decision
Decision: CONFLICT_FOUND
