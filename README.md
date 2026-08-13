# Project Sentinel

Project Sentinel is an AI-powered ICT trading intelligence platform for prop firm traders. Its purpose is to help traders evaluate high-probability opportunities with strict risk controls, clear reasoning, and journal-ready explanations.

## Sentinel Philosophy - SPEX Doctrine

Project Sentinel evolves under SPEX doctrine:

- S = Sentinel
- P = Perfectionism
- E = Expansionism
- X = Exceptionism

Perfectionism:
Sentinel continuously reduces imperfections in logic, reporting, execution, and architecture. No hidden bugs, logic drift, or unexplained behavior are tolerated.

Expansionism:
Sentinel grows in capability through controlled evolution without sacrificing architecture or safety. Growth must strengthen, not destabilize.

Exceptionism:
Sentinel rejects mediocrity and targets rare superiority. Sentinel is designed to achieve exceptional rather than merely good performance.

Official doctrine statement:

Project Sentinel accepts:

- no growth that compromises truth
- no profit that compromises safety
- no optimization that compromises robustness

## Sentinel Excellence Doctrine (SED)

Project Sentinel exists to achieve:

Exceptional Alpha
+
Exceptional Safety
+
Exceptional Robustness

Exceptional Alpha:
Superior market edge with elite profitability.

Exceptional Safety:
Capital preservation and strict risk control.

Exceptional Robustness:
Consistent performance across regimes with minimal fragility.

Rule:
No optimization is accepted if it materially damages any pillar.

## Sentinel Audit Doctrine (SAD)

No sprint is complete until audit passes.

Audit Levels:

FAST:
UI, reports, and commands.

LOGIC:
Decision engine changes.

FORENSIC:
Execution, portfolio, and live policy changes.

Rule:
Commit blocked if audit fails.

## Sentinel Triad Evaluation (STE)

Each major sprint is scored on:

- Alpha Score (0-10)
- Safety Score (0-10)
- Robustness Score (0-10)

Sprint approval requires balanced improvement.

## Vision

Sentinel is designed to become a disciplined trading advisor that combines market data, ICT concepts, confidence scoring, and prop firm risk rules into one decision workflow. The system should never behave like a black box. Every recommendation must explain the market context, the rules that passed or failed, the risk constraints applied, and the reason a trade is or is not acceptable.

## Initial Market Focus

The first version focuses on:

- XAUUSD, commonly traded as Gold
- US30, commonly traded as the Dow Jones index CFD

These instruments were selected because they are popular among prop firm traders, move with meaningful volatility, and require strong session awareness and risk discipline.

EURUSD and GBPUSD are supported as forex watchlist symbols. They are lower-priority radar markets, require A-grade setups only, use a stricter 95 minimum confidence threshold, and remain inside Advisor Mode with no auto execution.

BTCUSD is available as an experimental observer-mode symbol. Sentinel can collect live BTC diagnostics for confidence, killzone, narrative, SMT, and state tracking, but BTCUSD is never execution-enabled and always remains blocked from trade planning.

NAS100 is available as an observer-mode index candidate. It is included in command center, dashboard, Telegram, live monitor, live-data collection, and backtest discovery paths, but execution remains disabled unless future metrics qualify it for promotion.

## Advisor Mode First

Project Sentinel will initially run in Advisor Mode. In this mode, the platform analyzes market conditions and produces structured trade guidance, but it does not place live trades automatically.

Advisor Mode should support:

- Market structure analysis
- Liquidity mapping
- ICT setup validation
- Confidence scoring
- Risk checks
- Explainable trade recommendations
- Trade journal output

## Explainable Decision Engine

The decision engine will be built around transparent rule evaluation. A valid recommendation should include:

- Current trend and market structure context
- Liquidity targets and invalidation areas
- ICT concepts involved in the setup
- Confidence score and score breakdown
- Risk sizing and prop firm limits
- Final recommendation: wait, monitor, avoid, or consider trade

## Prop Firm Risk Management

Risk management is a first-class part of the architecture. Sentinel must respect account protection rules before any setup is considered actionable.

Core constraints include:

- Default risk per trade
- Maximum daily loss
- Maximum trades per day
- Minimum confidence threshold
- Clear invalidation before entry
- No recommendation when rules conflict

No real broker credentials, passwords, API keys, or account details should be committed to this repository.

## Project Structure

```text
backend/                 Core Python application modules
config/                  Environment templates and configuration examples
dashboard/               Future user interface
data/                    Local data storage, excluded where sensitive or generated
docs/                    Product, trading, ICT, and roadmap documentation
scripts/                 Utility and operational scripts
tests/                   Automated test suite
```

## Local Setup

Create a local environment file from the example:

```powershell
Copy-Item config/settings.example.env .env
```

Then edit `.env` locally with your own MT5 credentials. Do not commit real credentials, API keys, broker details, passwords, or account information.

Create and activate the virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run the MT5 connection smoke test:

```powershell
python scripts/test_mt5_connection.py
```

The smoke test connects to MetaTrader 5, prints account information, prints the latest tick for XAUUSD and US30, fetches 100 XAUUSD M15 candles, and shuts the MT5 connection down cleanly.

Run symbol discovery to find broker-specific Gold and US30 symbols:

```powershell
python scripts/list_symbols.py
```

Run crypto and index discovery to find broker-specific BTC/NAS100 aliases:

```powershell
python scripts/discover_crypto_symbols.py
python scripts/discover_index_symbols.py
```

The crypto discovery script scans broker symbols for BTC, XBT, and CRYPTO names such as `BTCUSD`, `BTCUSDm`, `BTCUSD.cash`, `BTCUSD.pro`, and `XBTUSD`. The index discovery script scans NAS, USTEC, NASDAQ, US100, and NDX names such as `NAS100`, `USTEC`, `NAS100.cash`, and `NASUSD`.

Run the Trend Engine smoke test:

```powershell
python scripts/test_trend_engine.py
```

The trend engine connects to MetaTrader 5, analyzes XAUUSD and US30 across Daily, 4H, and 1H candles, prints Advisor Mode bias results, and shuts down cleanly. It does not place trades.

Run the Liquidity Engine smoke test:

```powershell
python scripts/test_liquidity_engine.py
```

The liquidity engine connects to MetaTrader 5, analyzes external, internal, and engineered liquidity, including PDH, PDL, weekly levels, Asian range, equal highs/lows, and recent liquidity sweeps for XAUUSD and US30, then shuts down cleanly. It does not place trades.

Run the ICT Execution Engine smoke test:

```powershell
python scripts/test_ict_engine.py
```

The ICT engine connects to MetaTrader 5, combines execution candles with liquidity context, analyzes MSS, BOS, FVGs, order blocks, premium/discount, return into FVG, and basic Advisor Mode execution readiness for XAUUSD and US30. It does not place trades.

Run the Confidence Engine smoke test:

```powershell
python scripts/test_confidence_engine.py
```

The confidence engine connects to MetaTrader 5, combines Trend, Liquidity, ICT, and constitution config checks, then returns a final Advisor Mode confidence score, decision, rejection reasons, and explanation for XAUUSD and US30. It does not place trades.

Run the Narrative Engine smoke test:

```powershell
python scripts/test_narrative_engine.py
```

The narrative engine connects to MetaTrader 5, combines Trend, Liquidity, ICT, and configured session context, then returns the market story for XAUUSD and US30: daily bias, swept and unswept liquidity, premium/discount zone, active session, market phase, likely draw on liquidity, summary, and explanation. It does not place trades.

Run the Killzone Engine smoke test:

```powershell
python scripts/test_killzone_engine.py
```

The killzone engine reads `config/killzones.yaml`, uses WAT time, and reports the active ICT killzone, validity, quality score, commentary, and minutes to the next killzone for XAUUSD, US30, EURUSD, and GBPUSD. Confidence scoring uses killzone quality for session quality and rejects setups outside valid killzones. Command Center, Live Monitor, and Journal records include killzone context. It does not place trades.

Run the SMT Engine smoke test:

```powershell
python scripts/test_smt_engine.py
```

The SMT engine reads `config/smt_pairs.yaml`, compares correlated symbols on M15 by default, and reports bullish or bearish SMT divergence using recent swing highs/lows. SMT is confirmation only: Confidence can add an alignment bonus or subtract a conflict penalty, Command Center displays SMT status, and Journal records SMT context. It does not place trades.

Run the Backtesting Engine smoke test:

```powershell
python scripts/test_backtesting_engine.py
```

The backtesting engine reads `config/backtesting.yaml`, fetches historical M15 candles, scans historical killzone windows, builds Advisor Mode historical trade plans, simulates SL/TP outcomes candle by candle, and reports win rate, average RR, profit factor, drawdown, and performance by symbol, confidence band, and killzone. Diagnostic reporting also breaks results down by narrative phase, losing trade clusters, and engine score contribution. Trade records keep the raw score keys (`daily_bias`, `h4_narrative`, `liquidity_sweep`, `mss`, `fvg_quality`, `session_quality`, `target_clarity`, `smt`) while printed diagnostics use readable labels (`daily_bias`, `narrative`, `liquidity`, `mss`, `fvg`, `killzone`, `smt`). Strategy guardrails live in `backend/guardrails/strategy_guardrails.py` and read `config/strategy_guardrails.yaml`; they veto weak execution approvals without changing the underlying confidence score. The backtest smoke test prints before-vs-after guardrail impact, including PF, win rate, trades removed, and the most common filtered condition. It does not place live trades.

Run a single long-horizon backtest for priority symbols only:

```powershell
python scripts/test_backtesting_engine.py --days 30 --guardrails off
python scripts/test_backtesting_engine.py --days 30 --guardrails hard
python scripts/test_backtesting_engine.py --days 30 --guardrails adaptive
python scripts/test_backtesting_engine.py --days 90 --guardrails off
python scripts/test_backtesting_engine.py --days 90 --guardrails hard
python scripts/test_backtesting_engine.py --days 90 --guardrails adaptive
```

Run the full long-horizon matrix:

```powershell
python scripts/test_backtesting_engine.py --long
```

Long-horizon mode temporarily focuses on XAUUSD and US30, excluding EURUSD and GBPUSD so Sentinel can evaluate its strongest instruments first. It reports setups scanned, trades approved, wins, losses, breakevens, win rate, profit factor, average RR, max drawdown, net RR, and breakdowns by symbol, killzone, and narrative phase. If the 90-day guardrails-on result has PF above 1.5, win rate above 50%, and max drawdown below 6%, Sentinel qualifies for Phase 3: Execution Automation Research. Otherwise, optimization continues.

Adaptive guardrails preserve hard vetoes for risk blocks, high-impact news locks, invalid killzones, missing MSS, RR below 3, and observer-only symbols. Conditions such as range phase, missing SMT, forex without SMT, and distribution without SMT are applied as confidence penalties instead of changing the original confidence score. The 365D robustness layer uses symbol execution tiers: US30 is production, XAUUSD is filtered production, and EURUSD, GBPUSD, and BTCUSD are observer-only. EURUSD is disabled for execution with the reason `EURUSD observer mode: 365D PF below threshold`. XAUUSD requires SMT alignment or confidence of at least 95, cannot execute London Continuation, and cannot execute London Open without SMT. US30 is restricted to New York Open and New York Continuation, with New York Continuation preferred. London Continuation is hard-blocked, London Open requires SMT, No SMT expansion setups receive a heavy penalty, and known No SMT loss clusters produce warnings.

Run the 365-day robustness backtest:

```powershell
python scripts/run_backtest_365d.py
```

The 365D runner writes `data/reports/backtest_365d_summary.json` and prints before-vs-after robustness metrics for PF, win rate, trade count, and max drawdown. It remains Advisor Mode only and does not place trades.

Run XAU deep diagnostics from the cached 365D report:

```powershell
python scripts/run_xau_diagnostics.py
```

The diagnostics report killzone, narrative, SMT availability, and loss-cluster evidence for XAUUSD. If the cached report does not include trade-level SMT splits, the script reports SMT dependency as unknown instead of inventing a rule.

Run the Monte Carlo stress test smoke script:

```powershell
python scripts/test_monte_carlo.py
```

The Monte Carlo engine reads `config/monte_carlo.yaml`, uses 365D adaptive guardrail trade outcomes when available, and randomizes trade sequencing across configured risk models. It reports return distribution, drawdowns, losing and winning streaks, 4% and 6% drawdown breach probabilities, account-collapse probability, safe risk, risk notes, and recommendations. It is Advisor Mode only, recommends no autonomous execution, and does not modify orders.

The long-horizon runner saves the latest adaptive performance summary to `data/reports/latest_backtest_summary.json` after a successful `--long` run. Telegram `/backtest` and the Streamlit Analytics page read this cache so they can display 30D/90D profit factor, win rate, trades, drawdown, and Phase 3 decision without rerunning historical tests.

Run the backtest cache smoke test:

```powershell
python scripts/test_backtest_cache.py
```

Run the Risk Governor smoke test:

```powershell
python scripts/test_risk_governor.py
```

The risk governor connects to MetaTrader 5, reads the live account balance, equity, profit, currency, login, and server, then applies prop firm protection rules for risk amount, drawdown, daily loss, trade count, consecutive losses, and cooldown status. It has veto power and does not place trades.

Risk Governor environment settings:

```powershell
$env:SENTINEL_ENV="development"   # development or production
$env:SENTINEL_ACCOUNT_MODE="demo"  # demo, funded, or personal
```

In development mode, unavailable daily loss history is returned as a warning. In production mode, unavailable daily loss history hard-blocks trading. Account sizing is always sourced from live MT5 account data.

Run the News Filter smoke test:

```powershell
python scripts/test_news_filter.py
```

The news filter reads manual high-impact USD events from `config/news_filter.yaml` and reports whether the current WAT time is inside the configured protection window. Version 0.1 does not use an external news API. When a lock is active for a symbol, Confidence can still score the setup normally, but the final decision is rejected with `High impact news lock active`.

Run the Journal Engine smoke test:

```powershell
python scripts/test_journal_engine.py
```

The journal engine appends local JSONL decision records to `data/journal/sentinel_decisions.jsonl`. It records scan context, account/risk status, news status, symbol state, confidence decisions, rejection reasons, trend/ICT snapshots, and trade-plan diagnostics. Journal files are local-only and ignored by git; credentials, passwords, API keys, and `.env` values must never be stored.

Run the Live Data Collector smoke test:

```powershell
python scripts/test_live_data_collector.py
```

The live data collector reads `config/live_data.yaml` and appends compact production scan records to `data/live_data/live_signals.jsonl` for XAUUSD, US30, EURUSD, GBPUSD, and BTCUSD. It records state, raw and guardrail-adjusted confidence, decision, bias, narrative phase, killzone, SMT, risk/news status, execution permission, rejection reasons, symbol mode, and a `setup_id` used to correlate setup progression across scans. BTCUSD is always stored with `symbol_mode: experimental`. The collector is Advisor Mode only, uses JSONL retention, and is called by Live Monitor and Command Center after their normal scan data is already built.

Run the Alert Engine smoke test:

```powershell
python scripts/test_alert_engine.py
```

The alert engine detects meaningful state changes such as `WARM_TO_HOT`, `HOT_TO_EXECUTION_READY`, risk blocks, and news locks. Version 0.1 keeps terminal alerts enabled by default and suppresses repeated alerts inside the configured cooldown window.

Telegram alerts are available but disabled by default in `config/alerts.yaml`. To test them, set `telegram: true` locally and provide credentials through environment variables or your local `.env`:

```powershell
$env:TELEGRAM_BOT_TOKEN="your-token"
$env:TELEGRAM_CHAT_ID="your-chat-id"
python scripts/test_telegram_alert.py
```

To retrieve a chat ID, send a message to your Telegram bot, then run:

```powershell
python scripts/get_telegram_chat_id.py
```

The helper prints available chat IDs without printing the bot token. Telegram messages use HTML formatting and are sent only when alerts are enabled, Telegram is enabled, credentials are present, and an alert is actually triggered.

Do not commit Telegram tokens, chat IDs, or other alert credentials.

Run the Telegram Command Bot offline smoke test:

```powershell
python scripts/test_telegram_command_bot.py
```

The Telegram Command Bot reads `config/telegram_bot.yaml` and upgrades Telegram from alerts-only to a lightweight mobile Advisor Mode command center. It supports `/start`, `/help`, `/ping`, `/status`, `/summary`, symbol snapshots (`/xauusd`, `/us30`, `/eurusd`, `/gbpusd`, `/btcusd`, `/nas100`), `/symbols`, `/risk`, `/news`, `/coach`, `/positions`, `/plans`, `/journal`, `/backtest`, `/live_stats`, `/stress`, `/readiness`, and `/settings`. It uses Telegram `getUpdates` polling, responds only to the configured `TELEGRAM_CHAT_ID`, returns `Unauthorized.` to unknown chats, uses HTML message formatting, and never prints or stores the bot token. `/symbols` displays the symbol registry tier, PF, WR, trades, drawdown, and status. `/backtest` reads the cached latest summary when available and falls back cleanly when no cache exists. `/live_stats` reads the live-data JSONL cache and reports warm, hot, and execution-ready frequencies per symbol, including BTCUSD experimental mode and NAS100 observer mode. `/stress` reports Monte Carlo safe risk, 95% drawdown, worst losing streak, 4% breach probability, and keeps autonomous mode not recommended. `/readiness` reports the assisted-execution preflight status, failed checks, and blocking reasons without exposing credentials. BTCUSD is displayed as `BTCUSD (EXPERIMENTAL)` and NAS100 as `NAS100 (OBSERVER)`; both remain observer-only with execution blocked. The offline smoke test uses mocked Sentinel snapshots and sends no Telegram messages.

Run the live Telegram Command Bot:

```powershell
python scripts/run_telegram_bot.py
```

The live bot loads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from `.env`, connects to MT5, and answers mobile commands using the existing Risk Governor, News Filter, Confidence Engine, Trade Planner, AI Coach, and Journal Engine. Advisor Mode is enforced: no autonomous execution, no order approval, and no position modification.

Optionally send a live readiness message when Telegram credentials are present:

```powershell
python scripts/test_telegram_command_live.py
```

Run the Streamlit web dashboard:

```powershell
streamlit run dashboard/app.py
```

The dashboard reads `dashboard/config.yaml` and provides Advisor Mode pages for Overview, Live Monitor, Trade Plans, Analytics, Journal, and AI Coach. It uses MT5 data when available, local journal JSONL records, cached backtest summaries when present, live-data collection summaries, Monte Carlo stress analytics, assisted-readiness status, and AI Coach output. The Overview page includes a readiness card and Symbol Registry panel, and the Analytics page shows cached 30D/90D/365D metric cards, profit factor, win rate, drawdown, trade-count charts, the cache timestamp, Monte Carlo drawdown histogram, risk model comparison, safe risk recommendation, live warm/hot/execution-ready frequencies, setup counts by killzone and narrative, and rejection reason buckets. BTCUSD is displayed with an EXPERIMENTAL badge and NAS100 with an OBSERVER badge. The dashboard has no execution controls.

Run the Trade Planner smoke test:

```powershell
python scripts/test_trade_planner.py
```

The trade planner connects to MetaTrader 5, combines Confidence, Risk, ICT, Liquidity, and symbol metadata, then drafts an Advisor Mode trade plan with entry, stop loss, take profit targets, lot sizing, reward-to-risk, management rules, and execution gating. It does not place trades.

Planner test mode can be enabled in `config/trade_planner.yaml` with `planner_test_mode: true`. This allows synthetic entry, stop-loss, and take-profit values to be generated for calculation testing when live market conditions do not pass the execution gates. The real `execution_allowed` flag and rejection reasons are still preserved.

Run the Execution Engine smoke test:

```powershell
python scripts/test_execution_engine.py
```

The execution engine reads `config/execution.yaml` and supports `advisor` and `assisted` modes in V0.1. Advisor mode validates and builds an order request but never submits. Assisted mode can submit only after safety checks pass, manual confirmation returns `Y`, and the final readiness checklist passes. Autonomous execution is not implemented. The smoke test uses a mock MT5 sender and does not place live orders.

Run the assisted paper trade drill:

```powershell
python scripts/run_assisted_paper_drill.py --scenario A --auto-approve
python scripts/run_assisted_paper_drill.py --scenario B --auto-approve
python scripts/run_assisted_paper_drill.py --scenario C --auto-approve
python scripts/run_assisted_paper_drill.py --scenario READINESS_BLOCKED
python scripts/run_assisted_paper_drill.py --scenario APPROVAL_REJECTED
```

The paper drill simulates a full assisted lifecycle without submitting broker orders. It performs a live-scan rehearsal, detects a qualifying setup, builds a trade plan, runs confidence context, guardrails, readiness, manual approval, mock order submission, open-position registration, position-manager recommendations, closeout, journaling, and formatted Telegram lifecycle messages. Scenario A walks `0R -> 1R -> 2R -> TP3`, Scenario B walks `0R -> 1R -> BE stopout`, and Scenario C walks `0R -> SL`. Use `--show-telegram-messages` to print the formatted lifecycle messages and `--send-telegram` only when local Telegram credentials are configured.

Run the assisted readiness checker smoke test:

```powershell
python scripts/test_readiness_checker.py
```

The readiness checker reads `config/readiness.yaml` and is the last gate before any assisted order submission. It validates MT5 connection, account alias, Risk Governor status, news lock, killzone, guardrails, spread, lot constraints, RR minimum, assisted execution mode, and manual confirmation. Anything less than 11/11 blocks assisted execution, journals the failure through the execution engine, and keeps autonomous execution disabled.

Run the Position Manager smoke test:

```powershell
python scripts/test_position_manager.py
```

The position manager reads `config/position_manager.yaml`, identifies Sentinel MT5 positions/orders by magic number or comment, calculates current R, recommends breakeven moves at 1R, partial close at 2R, structure trailing, and pending-order cancellation when the setup is invalidated. Advisor mode only prints recommendations. Assisted mode requires manual confirmation before any mock or live modification request. The smoke test uses mock MT5 actions by default and does not modify live positions.

Run the AI Coach smoke test:

```powershell
python scripts/test_ai_coach.py
```

The AI Coach reads `config/ai_coach.yaml`, local journal records from `data/journal/sentinel_decisions.jsonl`, and a backtest diagnostics summary, then prints rule-based coaching guidance for symbol preference, session quality, confidence bands, narrative phases, guardrail impact, repeated rejection reasons, risk warnings, news locks, and execution readiness. Version 0.1 is Advisor Mode only, uses no LLM or external API, stores no credentials, and never places or modifies trades. If the local journal is empty, the smoke test uses safe synthetic sample records.

Run the Sentinel Command Center:

```powershell
python scripts/sentinel_command_center.py
```

The command center connects to MetaTrader 5 once, suppresses noisy logs, and prints a clean Advisor Mode dashboard covering account risk status, AI Coach summary, trend, liquidity, ICT, confidence, trade plan, final decision, and analyst commentary for XAUUSD, US30, EURUSD, and GBPUSD. It also appends live-data collector records when `config/live_data.yaml` is present. It does not place trades.

Run the Sentinel Live Monitor:

```powershell
python scripts/run_sentinel_live.py
```

The live monitor repeatedly scans the configured symbols in `config/monitoring.yaml`, prints heartbeat summaries, raises terminal alerts when a symbol changes confidence state, such as `WARM -> HOT` or `HOT -> EXECUTION_READY`, and appends live-data collector records when `config/live_data.yaml` is present. It handles unavailable broker symbols without stopping the monitor and remains Advisor Mode only. BTCUSD is included as an experimental observer-mode symbol and is journaled only as diagnostics with execution blocked.

Start, stop and check Sentinel with the four buttons in the project root:

| button | what it does |
| --- | --- |
| `CHECK_SENTINEL.bat` | Read-only health report: supervisor, engine, MT5, feed age, open positions, refusals, kill switch. Changes nothing. |
| `START_SENTINEL.bat` | Starts the supervisor, which then starts MT5 and the engine. Rarely needed - the supervisor starts automatically at Windows logon. |
| `STOP_SENTINEL.bat` | Stops the supervisor, then the engine. Refuses while a position is open unless given `-Force`; add `-IncludeMT5` to close the terminal too. |
| `RESTART_SENTINEL.bat` | Stops and restarts so the running processes pick up current code. |

The supervisor is the single owner of the engines. Do not start an engine by
hand while it is running - two books on one account is the failure that rule
exists to prevent. It writes a timestamped `data/live_paper/supervisor.log`
itself, so it logs the same way however it was launched.

To halt new entries without stopping anything, create an empty
`data/live_paper/KILL_SWITCH` file. Exits keep running; only new orders are
blocked. Delete the file to resume.

Startup troubleshooting:

- If `CHECK_SENTINEL` says the supervisor is not running, double-click `START_SENTINEL.bat`.
- If MetaTrader 5 is down, the supervisor relaunches it within a couple of minutes; if it cannot, it alerts on Telegram.
- If orders are refused with `retcode=10027`, AutoTrading is off in MT5 - click the `Algo Trading` button so it is green (Ctrl+E).
- If PowerShell blocks script execution, the `.bat` wrappers already pass `-ExecutionPolicy Bypass`.

Live monitor scan intervals are configured by environment:

```yaml
scan_interval_seconds:
  development: 180
  production: 60
```

Run unit tests:

```powershell
pytest
```
