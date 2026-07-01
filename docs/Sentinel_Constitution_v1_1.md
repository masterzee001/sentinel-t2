# Sentinel Constitution v1.1

Project Sentinel is a rule-based ICT trading intelligence platform for prop firm trading. This constitution defines the non-negotiable trading rules that future engines must respect before producing any actionable recommendation.

## Core Philosophy

- Never take a bad trade.
- Missing a good trade is acceptable.
- Taking a bad trade is unacceptable.

Sentinel must prefer no trade over a weak trade. Advisor Mode should only surface opportunities that align with session, narrative, liquidity, execution, and risk constraints.

## Approved Markets

- XAUUSD
- US30

No other market should be considered valid until it is explicitly added to the constitution and supporting configuration.

## Approved Sessions

All session times are defined in WAT.

### XAUUSD

- London: 08:00-11:00 WAT
- New York: 13:30-16:00 WAT

### US30

- New York only: 13:30-16:00 WAT

Trades outside approved sessions are invalid, even if the technical setup appears strong.

## Risk Rules

- Account size: 5000
- Firm max drawdown: 6%
- Internal max drawdown: 4%
- Week 1 risk per trade: 0.5%
- Week 2 risk per trade: 1% only if profitable
- Max trades per day: 2
- Daily loss limit: 1%
- Lock account after 4% drawdown
- Stop trading after 2 consecutive losses

Risk limits override all setup signals. If any risk rule is breached or unclear, Sentinel must return a no-trade decision.

## Execution Checklist

Every actionable trade idea requires all of the following:

- Valid session
- Daily bias
- 4H narrative
- Liquidity sweep
- MSS
- FVG
- Return to FVG
- Premium/discount alignment
- Clear target
- RR >= 3

If any checklist item is missing, Sentinel should wait, monitor, or avoid instead of recommending a trade.

## Trade Management

- Move stop loss to break even at 1R.
- Take 30% profit at 2R.
- Trail the remaining 70%.

Trade management rules must be included in journal output whenever Sentinel produces a trade recommendation.

## Extra Rules

- Wait 15 minutes after a stop loss before considering another setup.
- Allow only one trade per narrative.
- Apply a high-impact news lock 30 minutes before and 30 minutes after relevant events.

## Future Mode Switching

Sentinel should be prepared to support three operating modes:

- Conservative
- Balanced
- Aggressive

Mode switching must never bypass hard constitution rules. Future modes may adjust confidence thresholds, scoring interpretation, or risk sizing only within approved risk boundaries.
