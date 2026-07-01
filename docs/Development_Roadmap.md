# Development Roadmap

## Phase 1: Project Foundation

- Create professional repository structure.
- Define product and trading documentation.
- Add environment template and dependency list.
- Establish backend package boundaries.

## Phase 2: Market Data Layer

- Connect to MetaTrader 5 data.
- Normalize candle data for XAUUSD and US30.
- Add validation for symbols, timeframes, and missing data.
- Create tests for data transformation.

## Phase 3: Analysis Engines

- Build trend and market structure detection.
- Build liquidity mapping.
- Build ICT setup validation.
- Add unit tests for each engine.

## Phase 4: Confidence And Risk

- Implement explainable confidence scoring.
- Implement prop firm risk checks.
- Combine setup quality with account protection rules.
- Produce structured recommendation objects.

## Phase 5: Advisor Workflow

- Create end-to-end Advisor Mode pipeline.
- Generate journal-ready decision records.
- Add logging and audit trails.
- Run backtesting-style validation on historical samples.

## Phase 6: Dashboard

- Design a focused trader dashboard.
- Display market context, recommendations, and risk state.
- Add journal views and confidence breakdowns.

## Phase 7: Future Automation Readiness

- Harden execution boundaries.
- Add dry-run execution simulations.
- Define approval gates for any future live trading mode.
- Keep risk controls mandatory and transparent.
