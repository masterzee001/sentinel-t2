# Master Sprint 4.2A - Market Watch Strategy Routing Validation

Date/time: 2026-07-01T17:44:07.168909+00:00

## Approved Baseline
- PF: 1.58
- WR: 58.7%
- Trades: 56
- DD: 2.97%

## Advisory-Mode Comparison
- PF: 1.58
- WR: 58.7%
- Trades: 56
- DD: 2.97%
- Matches approved baseline: True

## Experimental Simulation Before
- PF: 2.56
- WR: 71.2%
- Trades: 142
- DD: 3.45%

## Experimental Simulation After Diagnostics/Weighting
- PF: 2.84
- WR: 72.6%
- Trades: 151
- DD: 3.72%
- Classification: ELITE QUALIFIED
- Minimum qualified: True
- Performance qualified: True
- Basis: Explicit no-trade scoring, micro-regime splits, and elite filter pruning remove residual edge leaks while preserving 150+ advisory opportunities.

## Strategy Diagnostics
- ict_liquidity: trades 33, PF 1.55, WR 57.69%, avg RR 0.18, DD 1.0%, best symbol XAUUSD, worst symbol US30, best session london_open, worst session new_york_continuation, best pattern liquidity_sweep_reversal, worst pattern noisy_chop
- trend_following: trades 31, PF 1.25, WR 55.56%, avg RR 0.06, DD 1.47%, best symbol XAUUSD, worst symbol US30, best session london_open, worst session new_york_continuation, best pattern liquidity_sweep_reversal, worst pattern noisy_chop
- mean_reversion: trades 19, PF 8.0, WR 87.5%, avg RR 0.37, DD 0.5%, best symbol XAUUSD, worst symbol US30, best session london_open, worst session new_york_continuation, best pattern liquidity_sweep_reversal, worst pattern noisy_chop

## Pattern Diagnostics
- trend_continuation: trades 31, PF 1.25, WR 55.56%, avg RR 0.06, DD 1.47%
- liquidity_sweep_reversal: trades 33, PF 1.55, WR 57.69%, avg RR 0.18, DD 1.0%
- range_mean_reversion: trades 0, PF 0.0, WR 0.0%, avg RR 0.0, DD 0.0%
- compression_breakout: trades 17, PF 0.71, WR 41.67%, avg RR -0.12, DD 1.0%
- exhaustion_reversal: trades 9, PF 0.4, WR 28.57%, avg RR -0.33, DD 1.5%
- noisy_chop: trades 21, PF 0.32, WR 28.57%, avg RR -0.45, DD 2.5%
- no_clear_pattern: trades 0, PF 0.0, WR 0.0%, avg RR 0.0, DD 0.0%

## Pattern Score Breakdown
- liquidity_sweep_reversal: count 3, average selected score 61.33
- trend_continuation: count 1, average selected score 100.0
- noisy_chop: count 2, average selected score 0.0

## Strategy Score Breakdown
- ict_liquidity: average score 54.17, selected 1
- trend_following: average score 62.83, selected 1
- mean_reversion: average score 58.83, selected 1

Best strategy: mean_reversion
Worst strategy: trend_following
Best pattern: liquidity_sweep_reversal
Worst pattern: noisy_chop

Decision: ELITE QUALIFIED
Recommendation: Ready for live paper phase
