# Master Post Sprint 16 Regression Backtest

Generated: 2026-06-30T13:12:00.657767+00:00

Mode: validation only. No production changes, no config changes, no live execution.

## Production Scope
- Included: US30, XAUUSD
- Excluded from production metrics: BTCUSD, NAS100, EURUSD, GBPUSD

## Window Metrics
### 30D
- PF: 2.0
- WR: 63.64%
- Trades: 13
- DD: 0.99%
- Avg RR: 0.31
- Avg Win: 1.25
- Avg Loss: -1.0
- Expectancy: 0.31
- Source: data/reports/latest_backtest_summary.json

### 365D
- PF: 1.58
- WR: 58.7%
- Trades: 56
- DD: 2.97%
- Avg RR: 0.2
- Avg Win: 1.11
- Avg Loss: -1.0
- Expectancy: 0.2
- Source: data/reports/backtest_365d_summary.json:production_portfolio.metrics

### 90D
- PF: 1.75
- WR: 61.29%
- Trades: 45
- DD: 1.0%
- Avg RR: 0.2
- Avg Win: 1.06
- Avg Loss: -1.0
- Expectancy: 0.2
- Source: data/reports/latest_backtest_summary.json

### Extended Window 2025-01-01 to 2026-06-30
- PF: 1.58
- WR: 58.7%
- Trades: 56
- DD: 2.97%
- Avg RR: 0.2
- Avg Win: 1.11
- Avg Loss: -1.0
- Expectancy: 0.2
- Source: validated production-only 365D cache used because full Jan-Jun 2025 cache is unavailable

## Extended Coverage Note
- Requested: 2025-01-01 to 2026-06-30
- Cached coverage: 2025-07 to 2026-06
- Status: PARTIAL_CACHE_USED
- Note: Cached production backtest artifacts cover the validated 365D portfolio only; Jan-Jun 2025 candles are not present in cached report artifacts.

## Baseline Comparison
- approved_robustness_baseline: PF 1.58, WR 58.7%, Trades 56, DD 2.97% - Production preservation for US30/XAUUSD after observer, sandbox, and advisory feature additions.
- efde_enhanced_baseline: PF 3.02, WR 72.45%, Trades 151, DD 3.37% - EFDE advisory replay and challenge-mode research; not production portfolio accounting.
- original_elite_baseline: PF 2.84, WR 72.6%, Trades 151, DD 3.72% - Advisory A+ Override and EFDE-enhanced simulations only; does not replace production metrics.
- raw_baseline: PF 1.16, WR 52.22%, Trades 123, DD 3.94% - Historical pre-robustness reference only; not the current approval gate.

## Diagnostics
- Confidence Distribution: {'COLD': 0, 'EXECUTION_READY': 52, 'HOT': 20, 'WARM': 0}
- Rejection Distribution: {'MSS not confirmed': 17, 'grade_lock': 20, 'killzone': 19, 'no_trade': 15, 'risk_lock': 0, 'symbol_lock': 0}
- Signal Funnel: {'approved_trades': 56, 'execution_ready_setups': 52, 'hot_setups': 20, 'losses': 19, 'qualifying_setups': 118, 'total_scans': 4802, 'wins': 27}
- EFDE: {'average_loss_after': -0.58, 'average_loss_before': -1.0, 'dd_delta': -0.35, 'false_exit_rate': 0.0, 'losses_reduced': 18, 'reported_separately': True, 'saved_loss_value': 10.08}
- A+ Override: {'false_override_rate': 0.0, 'override_count': 12, 'override_win_rate': 100.0, 'recovered_trades': 7, 'reported_separately': True}
- Memory Engine: {'m1_contribution': 10.0, 'm5_contribution': 15.0, 'memory_alignment_contribution': 15.0, 'production_score_impact': 0.0, 'score_stickiness_warnings': 1, 'stale_symbols': ['XAUUSD'], 'trigger_contribution': 25.0}
- Best Regime: healthy_continuation_trend
- Worst Regime: noisy_chop

## Safety
- assisted_execution_dry_run_or_disabled: True
- autonomous_execution_disabled: True
- broker_submission_disabled: True
- config_drift_detected: False
- excluded_symbols: ['BTCUSD', 'EURUSD', 'GBPUSD', 'NAS100']
- observer_excluded: True
- production_baseline_preserved: True
- production_symbols: ['US30', 'XAUUSD']
- sandbox_excluded: True

Decision: PASS
