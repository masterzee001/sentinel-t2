# Fixture Report Quarantine

**Status:** QUARANTINED (Phase 0 truth reset, 2026-08-10)

Every report in this directory is **synthetic fixture output, not market
evidence**. The Phase 0 audit (three independent code-level passes) established
that these files were generated from hand-authored templates, hardcoded
baselines, or engines whose inputs are fixed literals. None of them may be
cited as a performance record, used as a promotion gate, or fed into parameter
tuning.

## Fabrication evidence (summary)

- Shadow learning: exactly 6 setups per block reason x 9 reasons; all 6 symbols
  numerically identical; outcome is a pure function of the block-reason label.
- Challenge mode: 2026 monthly windows bit-identical to 2025 (12-element cycle);
  1000/1000 challenge passes with zero failures.
- A+ override: 100% win rate, 0% false overrides; PF formula floor (2.91) sits
  above its own pass threshold (2.9) so it cannot fail.
- EFDE replay: cuts 18 losses and zero winners (look-ahead artifact).
- Expectancy database: by_symbol bit-identical to by_session (template join).
- Market Watch IQ series: routing_accuracy / learning_success returned as
  literals regardless of input.
- All of these measure themselves against hardcoded "elite" baselines
  (PF 2.84 / 151 trades) that no verified backtest produced.

## Quarantined files

Shadow family:
- shadow_setup_database.json
- shadow_trade_outcomes.json
- shadow_backtest_365d.json
- shadow_enhanced_comparison.json
- shadow_learning_memory.json
- shadow_tier_replay_report.json (hand-typed candidate scenarios; contains timestamps after its own generation time)

Challenge family:
- challenge_mode_simulation.json
- challenge_monthly_windows.json
- challenge_rolling_2month_windows.json
- challenge_profile_comparison.json
- challenge_mode_profile.json
- challenge_command_center.json

Override / EFDE family:
- a_plus_override_backtest.json
- a_plus_override_simulation.json
- block_severity_database.json
- early_failure_detection_report.json
- efde_calibration_report.json
- efde_learning_memory.json
- efde_trade_replay.json

Leak / expectancy family:
- edge_leak_analysis.json
- opportunity_leak_analysis.json
- guardrail_leak_analysis.json
- guardrail_attribution.json
- guardrail_iq_report.json
- setup_expectancy_database.json
- regime_strategy_expectancy.json
- conditional_relaxation_simulation.json
- no_trade_optimization.json
- loss_memory_database.json

Memory / portfolio / candidate family:
- hierarchical_market_memory.json
- portfolio_intelligence_results.json
- candidate_stress_report.json
- candidate_correlation_report.json
- candidate_validation_report.json
- symbol_lock_optimization.json
- sandbox_learning_memory.json
- ai_policy_memory.json
- ai_policy_recommendations.json

Market Watch family:
- market_watch_iq_report.json
- market_watch_iq_v2.json through market_watch_iq_v9.json
- market_watch_strategy_diagnostics.json
- strategy_routing_forensics.json
- micro_regime_diagnostics.json
- timeframe_confluence_report.json
- score_stickiness_report.json
- market_watch_365d_summary.json (the PF 1.21 -> 2.89 in-sample escalation ladder)

## Suspect but retained as working artifacts (not fixtures)

- backtest_365d_summary.json / backtest_365d_v2_summary.json /
  latest_backtest_summary.json — produced by the real backtest engine, but the
  pre-Phase-0 engine had no costs, assumed MSS true, and the headline block is
  a stored constant contradicted by its own breakdowns. Superseded by any run
  of the Phase 0 engine (live-parity brain + costs). Regenerate before citing.
- governance_audit_replay_report.json — real engine, but its input journal is
  absent from the repo and its false-rejection section is vacuous (0 samples).
- production_promotion_results.json, post_sprint_16_regression_backtest.json —
  historical reference only.

## Rule going forward

No report is evidence unless it was produced by the current engine version,
reconciles against its own trade export, and the export contains per-trade
outcomes. The regenerated gates in `scripts/run_backtest_365d.py` (Phase 0.6)
enforce recomputation instead of constant-equality.

The fixture *generator scripts* (`scripts/run_shadow_learning_engine.py`,
`run_ai_policy_engine.py`, `run_a_plus_override_engine.py`,
`run_early_failure_detection_engine.py`, `run_challenge_mode_simulator.py`,
etc.) still write these filenames. Until those engines are rebuilt on real
outcome data (Phase 3), anything they emit is a fixture by construction.
