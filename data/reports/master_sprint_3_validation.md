# Master Sprint 3 - 365D Validation Checkpoint

Date/time: 2026-07-01T17:44:24.805495+00:00

## Approved Baseline
- PF: 1.58
- WR: 58.7%
- Trades: 56
- DD: 2.97%

## Raw Baseline
- PF: 1.16
- WR: 52.22%
- Trades: 123
- DD: 3.94%

## Observer-Only Comparison
- PF: 1.58
- WR: 58.7%
- Trades: 56
- DD: 2.97%
- Matches approved baseline: True

## Observer Diagnostics
- BTC: CANDLES_AVAILABLE_NO_SETUPS, trades 0, PF 0.0, WR 0.0%
- NAS100/USTEC: GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES, trades 24, PF 2.0, WR 65.22%
- EURUSD: GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES, trades 101, PF 1.06, WR 51.39%
- GBPUSD: GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES, trades 102, PF 1.03, WR 50.0%

## XAU SMT Status
- With SMT: trades 0, PF 0.0, WR 0.0%
- Without SMT: trades 9, PF 1.0, WR 50.0%
- SMT dependency: NO_SMT_SAMPLE
- Hard SMT block: False

## Regression Gates
- production_pf_within_0_01: PASS (delta=0.00)
- production_trade_count_locked: PASS (trades=56)
- production_wr_locked: PASS (delta=0.00)
- production_dd_below_3: PASS (dd=2.97)
- observer_symbols_non_invasive: PASS (trade_delta=0)
- observer_symbols_execution_disabled: PASS (BTC/NAS100/EURUSD/GBPUSD execution disabled)
- xau_smt_hard_block_disabled_below_sample: PASS (SMT hard block disabled while sample < 10)
- autonomous_execution_disabled: PASS (execution_mode=advisor)

## Decision: PASS

## Next Roadmap Step
Master Sprint 4 - Market Watch Strategy Intelligence
