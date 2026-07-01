# Product Requirements Document

## Product Name

Project Sentinel

## Purpose

Project Sentinel is an AI-powered ICT trading intelligence platform for prop firm traders. The first release will provide Advisor Mode analysis for XAUUSD and US30, helping traders make disciplined decisions without automated order placement.

## Primary Users

- Prop firm challenge traders
- Funded account traders
- ICT-style discretionary traders
- Traders who need structured risk control and decision journaling

## Initial Scope

The initial platform will analyze market conditions, evaluate ICT-style trade setups, calculate confidence, apply risk constraints, and produce explainable recommendations.

## Supported Instruments

- XAUUSD
- US30

## Initial Mode

Advisor Mode only. The system will not place live trades in the initial version.

## Core Capabilities

- Connect to market data sources
- Normalize price and candle data
- Detect market structure and trend context
- Identify liquidity areas
- Validate ICT concepts and setup rules
- Score confidence with a transparent breakdown
- Enforce prop firm risk settings
- Produce trade journal records
- Explain why a trade is accepted, rejected, or deferred

## Non-Goals For Initial Release

- Fully automated live trading
- Multi-broker execution
- Social trading signals
- Guaranteed profitability claims
- Hidden or unexplained AI decisions

## Success Criteria

- The system can analyze XAUUSD and US30 consistently.
- Every recommendation includes a clear explanation.
- Risk limits are checked before trade recommendations.
- Advisor Mode can be tested safely without broker credentials in source control.
- The codebase remains modular and ready for expansion.
