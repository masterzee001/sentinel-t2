# Master Sprint 17 - Reporting & Classification Truth Layer

Generated: 2026-07-01T17:44:06.803271+00:00
Decision: PASS

## Band Registry
- Status: PASS
- Source: backend/shared/confidence_band_registry.py
- Production raw thresholds: {'warm_minimum': 40, 'hot_minimum': 70, 'execution_ready_minimum': 90}
- Guardrail-adjusted thresholds: {'warm_minimum': 40, 'hot_minimum': 70, 'execution_ready_minimum': 95}

## Funnel Truth
- Exclusive Band Distribution: {'COLD_ONLY': 0, 'WARM_ONLY': 0, 'HOT_ONLY': 20, 'EXECUTION_READY': 52}
- Cumulative Funnel: {'total_scans': 4802, 'qualifying_setups': 118, 'HOT_OR_BETTER': 72, 'EXECUTION_READY': 52, 'approved_trades': 56, 'wins': 27, 'losses': 19}
- Rule: HOT_ONLY is a bucket; HOT_OR_BETTER is the cumulative stage.

## Rejection Attribution
- Status: PASS
- Distribution: {'MSS_NOT_CONFIRMED': 17, 'BELOW_MIN_CONFIDENCE': 20, 'INVALID_KILLZONE': 19, 'SYMBOL_LOCK': 0, 'RISK_LOCK': 0, 'NO_TRADE_WINDOW': 15, 'NEWS_LOCK': 0, 'SPREAD_LOCK': 0, 'DUPLICATE_LOCK': 0}
- Rule: display reports use allowed payload reason codes only.

## Backtest MSS Label
- Backtest MSS Mode: SYNTHETIC_ASSUMED_TRUE
- Live MSS Mode: LIVE_EVALUATED

## Observer Label
- Status: PASS
- States: OBSERVER_COLD, OBSERVER_WARM, OBSERVER_HOT, OBSERVER_UNAVAILABLE

## Production Baseline
- Preserved: True
- PF: 1.58
- WR: 58.7
- Trades: 56
- DD: 2.97
