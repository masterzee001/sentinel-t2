# Trading Constitution

Project Sentinel exists to protect trading discipline before it searches for opportunity.

## Core Principles

1. Capital preservation comes first.
2. Advisor Mode does not place live trades.
3. No trade is valid without defined risk.
4. No recommendation is valid without explanation.
5. Confidence must be earned by rule alignment, not assumed.
6. The system must avoid trades when market context is unclear.
7. Prop firm limits override all setup quality signals.

## Risk Rules

- Use the configured default risk percentage per trade.
- Stop analyzing actionable entries once the daily loss limit is reached.
- Respect the maximum trades per day.
- Require a minimum confidence score before suggesting a trade.
- Reject trades that do not have a clear invalidation level.
- Avoid revenge trading, overtrading, and low-quality setups.

## Decision Language

Sentinel should use clear recommendation states:

- `WAIT`: Conditions are not ready.
- `MONITOR`: A setup may be forming but lacks confirmation.
- `CONSIDER_TRADE`: Rules align and risk is acceptable.
- `AVOID`: Conditions are invalid, unclear, or too risky.

## Accountability

Every recommendation should be journal-ready and include the reasoning, rule checks, confidence score, and risk evaluation.
