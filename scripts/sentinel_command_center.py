"""Unified Advisor Mode command center for Project Sentinel."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.ai_coach.coach_analyzer import AICoachAnalyzer
from backend.confidence_engine.confidence_analyzer import ConfidenceAnalyzer
from backend.ict_engine.ict_analyzer import ICTAnalyzer
from backend.journal.journal_engine import JournalEngine
from backend.killzone_engine.killzone_analyzer import KillzoneAnalyzer
from backend.live_data.live_data_collector import LiveDataCollector
from backend.liquidity_engine.liquidity_analyzer import LiquidityAnalyzer
from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.market_watch_engine.market_watch_engine import MarketWatchEngine
from backend.news_filter.news_filter import NewsFilter
from backend.observer.btc_observer import BTCObserver
from backend.observer.nas100_observer import NAS100Observer
from backend.risk_manager.risk_governor import RiskGovernor
from backend.smt_engine.smt_analyzer import SMTAnalyzer
from backend.symbols.symbol_registry import SymbolRegistry
from backend.trade_planner.trade_planner import TradePlanner
from backend.trend_engine.trend_analyzer import TrendAnalyzer


SUPPORTED_SYMBOLS = ("XAUUSD", "US30", "EURUSD", "GBPUSD", "BTCUSD", "NAS100")
COMMENTARY = {
    "COLD": "No narrative yet. Ignore noise.",
    "WARM": "Narrative forming. Watch liquidity.",
    "HOT": "Setup close. Wait for confirmation.",
    "EXECUTION_READY": "A-grade setup detected. Prepare execution.",
}


def main() -> int:
    """Run the Sentinel terminal dashboard."""
    configure_terminal_logging()
    connector = MT5Connector()

    try:
        connector.connect()

        liquidity_analyzer = LiquidityAnalyzer(connector=connector)
        ict_analyzer = ICTAnalyzer(connector=connector, liquidity_analyzer=liquidity_analyzer)
        trend_analyzer = TrendAnalyzer(connector=connector)
        news_filter = NewsFilter()
        killzone_analyzer = KillzoneAnalyzer()
        smt_analyzer = SMTAnalyzer(connector=connector)
        confidence_analyzer = ConfidenceAnalyzer(
            connector=connector,
            trend_analyzer=trend_analyzer,
            liquidity_analyzer=liquidity_analyzer,
            ict_analyzer=ict_analyzer,
            news_filter=news_filter,
            killzone_analyzer=killzone_analyzer,
            smt_analyzer=smt_analyzer,
        )
        risk_governor = RiskGovernor(connector=connector)
        journal_engine = JournalEngine()
        ai_coach = AICoachAnalyzer()
        live_data_collector = LiveDataCollector() if (PROJECT_ROOT / "config" / "live_data.yaml").exists() else None
        symbol_registry = SymbolRegistry()
        market_watch_engine = MarketWatchEngine()
        btc_observer = BTCObserver(connector=connector, killzone_analyzer=killzone_analyzer, smt_analyzer=smt_analyzer)
        nas100_observer = NAS100Observer(connector=connector, killzone_analyzer=killzone_analyzer, registry=symbol_registry)
        trade_planner = TradePlanner(
            connector=connector,
            confidence_analyzer=confidence_analyzer,
            risk_governor=risk_governor,
            ict_analyzer=ict_analyzer,
            liquidity_analyzer=liquidity_analyzer,
        )

        risk = risk_governor.evaluate()
        news_status = news_filter.check()
        print_header()
        print_account_status(risk, news_status)
        print_coach_summary(ai_coach.analyze(use_synthetic_if_empty=False))

        print_section("MARKET INTELLIGENCE")
        live_symbol_results: list[dict[str, Any]] = []
        for symbol in SUPPORTED_SYMBOLS:
            if symbol == BTCObserver.SYMBOL:
                btc = btc_observer.observe()
                print_btc_observer_dashboard(btc, symbol_registry)
                live_symbol_results.append(btc)
                journal_engine.append_scan_records(
                    environment=risk.get("environment", "development"),
                    risk=risk,
                    news=news_status,
                    symbol_payloads=[
                        {
                            "symbol": BTCObserver.SYMBOL,
                            "trend": {},
                            "ict": {},
                            "killzone": btc.get("killzone", {}),
                            "confidence": btc.get("confidence", {}),
                            "trade_plan": btc.get("trade_plan", {}),
                            "commentary": btc.get("narrative", {}).get("summary", "BTCUSD observer mode."),
                        }
                    ],
                )
                continue
            if symbol == NAS100Observer.SYMBOL:
                nas100 = nas100_observer.observe()
                print_nas100_observer_dashboard(nas100, symbol_registry)
                live_symbol_results.append(nas100)
                journal_engine.append_scan_records(
                    environment=risk.get("environment", "development"),
                    risk=risk,
                    news=news_status,
                    symbol_payloads=[
                        {
                            "symbol": NAS100Observer.SYMBOL,
                            "trend": {},
                            "ict": {},
                            "killzone": nas100.get("killzone", {}),
                            "confidence": nas100.get("confidence", {}),
                            "trade_plan": nas100.get("trade_plan", {}),
                            "commentary": nas100.get("narrative", {}).get("summary", "NAS100 observer mode."),
                        }
                    ],
                )
                continue
            trend = trend_analyzer.get_overall_bias(symbol)
            liquidity = liquidity_analyzer.analyze(symbol)
            ict = ict_analyzer.analyze(symbol)
            killzone = killzone_analyzer.analyze(symbol)
            confidence = confidence_analyzer.analyze(symbol, context={"risk_reward": 3.0})
            trade_plan = trade_planner.analyze(symbol)
            print_symbol_dashboard(
                symbol=symbol,
                trend=trend,
                liquidity=liquidity,
                ict=ict,
                killzone=killzone,
                confidence=confidence,
                trade_plan=trade_plan,
                risk=risk,
                symbol_registry=symbol_registry,
            )
            live_symbol_results.append(
                {
                    "symbol": symbol,
                    "available": True,
                    "state": confidence.get("confidence_band", "COLD"),
                    "score": confidence.get("total_confidence", 0),
                    "trend": trend,
                    "ict": ict,
                    "liquidity": liquidity,
                    "killzone": killzone,
                    "confidence": confidence,
                    "trade_plan": trade_plan,
                }
            )
            journal_engine.append_scan_records(
                environment=risk.get("environment", "development"),
                risk=risk,
                news=news_status,
                symbol_payloads=[
                    {
                        "symbol": symbol,
                        "trend": trend,
                        "ict": ict,
                        "killzone": killzone,
                        "confidence": confidence,
                        "trade_plan": trade_plan,
                        "commentary": COMMENTARY.get(confidence.get("confidence_band", "COLD"), COMMENTARY["COLD"]),
                    }
                ],
            )

        if live_data_collector is not None:
            try:
                live_data_collector.append_scan(
                    {
                        "risk": risk,
                        "risk_status": risk.get("permission", {}).get("status", "UNKNOWN"),
                        "news": news_status,
                        "news_status": NewsFilter.format_status(news_status),
                        "symbols": live_symbol_results,
                    }
                )
            except Exception as exc:
                logger.warning("Live data collection skipped: {}", exc)

        print_section("MARKET WATCH")
        for result in market_watch_engine.analyze_many(SUPPORTED_SYMBOLS, contexts=market_watch_contexts(live_symbol_results)):
            print_market_watch_dashboard(result)

        return 0
    except (MT5ConnectorError, RuntimeError, ValueError) as exc:
        print(f"Sentinel command center failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def configure_terminal_logging() -> None:
    """Keep the command center readable by hiding INFO logs."""
    logger.remove()
    logger.add(sys.stderr, level="ERROR")


def print_header() -> None:
    print("\nPROJECT SENTINEL COMMAND CENTER")
    print("Advisor Mode: analysis only, no trade execution")
    print("=" * 72)


def print_section(title: str) -> None:
    print(f"\n{title}")
    print("-" * 72)


def print_account_status(risk: dict[str, Any], news_status: dict[str, Any] | None = None) -> None:
    account = risk.get("account", {})
    risk_data = risk.get("risk", {})
    permission = risk.get("permission", {})
    news_status = news_status or {}

    print_section("ACCOUNT STATUS")
    print(f"Login:             {account.get('login', 0)}")
    print(f"Server:            {account.get('server', '')}")
    print(f"Account Mode:      {account.get('account_mode', risk.get('account_mode', 'demo'))}")
    print(f"Balance:           {account.get('balance', 0.0)} {account.get('currency', 'USD')}")
    print(f"Equity:            {account.get('equity', 0.0)} {account.get('currency', 'USD')}")
    print(f"Daily Risk Amount: {risk_data.get('risk_amount', 0.0)} {account.get('currency', 'USD')}")
    print(f"Risk Status:       {permission.get('status', 'UNKNOWN')}")
    print(f"News Status:       {NewsFilter.format_status(news_status)}")
    print(f"Warnings:          {format_list(permission.get('warnings', []))}")
    print(f"Block Reasons:     {format_list(permission.get('block_reasons', []))}")


def print_coach_summary(report: dict[str, Any]) -> None:
    """Print the short Advisor Mode coach summary."""
    print_section("AI COACH")
    print(report.get("summary", "Coach: collect more journal and backtest data."))


def print_symbol_dashboard(
    symbol: str,
    trend: dict[str, Any],
    liquidity: dict[str, Any],
    ict: dict[str, Any],
    killzone: dict[str, Any],
    confidence: dict[str, Any],
    trade_plan: dict[str, Any],
    risk: dict[str, Any],
    symbol_registry: SymbolRegistry,
) -> None:
    final_state = confidence.get("confidence_band", "COLD")
    decision = final_decision(final_state, risk, trade_plan, confidence)

    print(f"\n{symbol}")
    print("=" * 72)
    print(f"Tier: {symbol_registry.display_tier_for(symbol).upper()}")
    print("TREND")
    print(f"  Daily Bias:        {trend.get('daily_bias', 'unknown')}")
    print(f"  H4 Bias:           {trend.get('h4_bias', 'unknown')}")
    print(f"  H1 Context:        {trend.get('h1_context', 'unknown')}")

    print("LIQUIDITY")
    latest_sweep = liquidity.get("latest_sweep")
    print(f"  Sweep Detected:    {bool(latest_sweep)}")
    print(f"  Primary Target:    {primary_target(liquidity)}")

    print("ICT")
    print(f"  MSS:               {format_detection(ict.get('mss', {}))}")
    print(f"  FVG:               {format_detection(ict.get('fvg', {}))}")
    print(f"  Order Block:       {format_detection(ict.get('order_block', {}))}")

    print("KILLZONE")
    print(f"  Killzone:          {KillzoneAnalyzer.display_name(str(killzone.get('active_killzone', 'none')))}")
    print(f"  Killzone Quality:  {killzone.get('quality_score', 0)}")
    print(f"  Commentary:        {killzone.get('commentary', 'No active killzone.')}")

    print("SMT")
    print(f"  SMT:               {SMTAnalyzer.format_summary(confidence.get('smt', {}))}")

    print("CONFIDENCE")
    print(f"  Score:             {confidence.get('total_confidence', 0)}")
    print(f"  Band:              {final_state}")
    print(f"  Action:            {confidence.get('recommended_action', 'Ignore')}")
    print(f"  Confidence Decision: {confidence.get('decision', 'UNAVAILABLE')}")
    print(f"  Rejection Reasons: {format_list(confidence.get('rejection_reasons', []))}")
    print(f"  Warnings:          {format_list(confidence.get('warnings', []))}")

    print("GUARDRAILS")
    print(f"  Guardrail Status:  {confidence.get('guardrail_status', 'PASS')}")
    print(f"  Guardrail Reasons: {format_list(confidence.get('guardrail_reasons', []))}")
    print(f"  Guardrail Warnings: {format_list(confidence.get('guardrail', {}).get('warnings', []))}")

    print("TRADE PLAN")
    print(f"  Plan Quality:      {trade_plan.get('plan_quality', 'invalid')}")
    print(f"  Entry:             {trade_plan.get('entry', {}).get('price', 0.0)}")
    print(f"  SL:                {trade_plan.get('stop_loss', {}).get('price', 0.0)}")
    print(f"  TP1:               {trade_plan.get('take_profit', {}).get('tp1', 0.0)}")
    print(f"  TP2:               {trade_plan.get('take_profit', {}).get('tp2', 0.0)}")
    print(f"  TP3:               {trade_plan.get('take_profit', {}).get('tp3', 0.0)}")
    print(f"  Lot Size:          {trade_plan.get('risk', {}).get('lot_size', 0.0)}")

    print("FINAL DECISION")
    print(f"  State:             {final_state}")
    print(f"  Decision:          {decision}")
    print(f"  Commentary:        {COMMENTARY.get(final_state, COMMENTARY['COLD'])}")


def print_btc_observer_dashboard(btc: dict[str, Any], symbol_registry: SymbolRegistry) -> None:
    """Print BTCUSD observer-only diagnostics."""
    confidence = btc.get("confidence", {})
    killzone = btc.get("killzone", {})
    trade_plan = btc.get("trade_plan", {})
    narrative = btc.get("narrative", {})
    print(f"\n{btc.get('display_symbol', 'BTCUSD (EXPERIMENTAL)')}")
    print("=" * 72)
    print(f"Tier: {symbol_registry.display_tier_for(BTCObserver.SYMBOL).upper()}")
    print("OBSERVER MODE")
    print("  Status:            Experimental diagnostics only")
    print("  Execution Allowed: False")
    print("KILLZONE")
    print(f"  Killzone:          {KillzoneAnalyzer.display_name(str(killzone.get('active_killzone', 'none')))}")
    print(f"  Killzone Quality:  {killzone.get('quality_score', 0)}")
    print("SMT")
    print(f"  SMT:               {SMTAnalyzer.format_summary(confidence.get('smt', {}))}")
    print("CONFIDENCE")
    print(f"  Score:             {confidence.get('total_confidence', 0)}")
    print(f"  Band:              {confidence.get('confidence_band', 'UNAVAILABLE')}")
    print(f"  Decision:          {confidence.get('decision', 'REJECTED')}")
    print(f"  Rejection Reasons: {format_list(confidence.get('rejection_reasons', []))}")
    print("NARRATIVE")
    print(f"  Summary:           {narrative.get('summary', 'BTCUSD observer mode.')}")
    print("TRADE PLAN")
    print(f"  Plan Quality:      {trade_plan.get('plan_quality', 'observer_only')}")
    print(f"  Execution Allowed: {trade_plan.get('execution_allowed', False)}")
    print("FINAL DECISION")
    print("  State:             OBSERVE")
    print("  Decision:          BLOCKED")


def print_nas100_observer_dashboard(nas100: dict[str, Any], symbol_registry: SymbolRegistry) -> None:
    """Print NAS100 observer-only diagnostics."""
    confidence = nas100.get("confidence", {})
    killzone = nas100.get("killzone", {})
    trade_plan = nas100.get("trade_plan", {})
    narrative = nas100.get("narrative", {})
    print(f"\n{nas100.get('display_symbol', 'NAS100 (OBSERVER)')}")
    print("=" * 72)
    print(f"Tier: {symbol_registry.display_tier_for(NAS100Observer.SYMBOL).upper()}")
    print("OBSERVER MODE")
    print("  Status:            Index diagnostics only")
    print("  Execution Allowed: False")
    print("KILLZONE")
    print(f"  Killzone:          {KillzoneAnalyzer.display_name(str(killzone.get('active_killzone', 'none')))}")
    print(f"  Killzone Quality:  {killzone.get('quality_score', 0)}")
    print("CONFIDENCE")
    print(f"  Score:             {confidence.get('total_confidence', 0)}")
    print(f"  Band:              {confidence.get('confidence_band', 'UNAVAILABLE')}")
    print(f"  Decision:          {confidence.get('decision', 'REJECTED')}")
    print(f"  Rejection Reasons: {format_list(confidence.get('rejection_reasons', []))}")
    print("NARRATIVE")
    print(f"  Summary:           {narrative.get('summary', 'NAS100 observer mode.')}")
    print("TRADE PLAN")
    print(f"  Plan Quality:      {trade_plan.get('plan_quality', 'observer_only')}")
    print(f"  Execution Allowed: {trade_plan.get('execution_allowed', False)}")
    print("FINAL DECISION")
    print("  State:             OBSERVE")
    print("  Decision:          BLOCKED")


def market_watch_contexts(symbol_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build Market Watch contexts from live command-center snapshots."""
    contexts: dict[str, dict[str, Any]] = {}
    for item in symbol_results:
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        ict = item.get("ict", {})
        liquidity = item.get("liquidity", {})
        confidence = item.get("confidence", {})
        killzone = item.get("killzone", {})
        contexts[symbol] = {
            "session": killzone.get("active_killzone", "none"),
            "trend": item.get("trend", {}),
            "ict": ict,
            "liquidity": liquidity,
            "narrative": confidence.get("narrative", {}),
            "sweep_detected": bool(liquidity.get("latest_sweep") or liquidity.get("sweep_detected", False)),
            "mss_confirmed": bool(ict.get("mss", {}).get("detected", False)),
            "fvg_present": bool(ict.get("fvg", {}).get("detected", False)),
            "order_block_present": bool(ict.get("order_block", {}).get("detected", False)),
            "narrative_alignment": confidence.get("total_confidence", 50),
        }
    return contexts


def print_market_watch_dashboard(result: dict[str, Any]) -> None:
    """Print one Market Watch advisory row."""
    scores = result.get("scores", {})
    print(f"\n{result.get('symbol', 'UNKNOWN')}")
    print("=" * 72)
    print(f"Session Quality:    {result.get('session_quality', 0)}")
    print(f"Pattern:            {display_label(result.get('dominant_pattern', 'no_clear_pattern'))}")
    print(f"ICT Liquidity:      {scores.get('ict_liquidity', 0)}")
    print(f"Trend Following:    {scores.get('trend_following', 0)}")
    print(f"Mean Reversion:     {scores.get('mean_reversion', 0)}")
    print("Selected Strategy:")
    print(f"  {display_label(result.get('selected_strategy', 'no_trade'))}")
    print("Mode:")
    print("  ADVISORY ONLY")
    print("Production Impact:")
    print(f"  {bool(result.get('affects_production', False))}")


def final_decision(final_state: str, risk: dict[str, Any], trade_plan: dict[str, Any], confidence: dict[str, Any] | None = None) -> str:
    """Map risk, confidence band, and planner permission to final action."""
    confidence = confidence or {}
    if not risk.get("permission", {}).get("trade_allowed", False):
        return "BLOCKED"
    if confidence.get("guardrail_status") == "BLOCKED":
        return "BLOCKED"
    if final_state == "COLD":
        return "WAIT"
    if final_state == "WARM":
        return "MONITOR"
    if final_state == "HOT":
        return "PREPARE"
    if final_state == "EXECUTION_READY" and trade_plan.get("execution_allowed"):
        return "TRADE APPROVED"
    return "BLOCKED"


def primary_target(liquidity: dict[str, Any]) -> str:
    """Return the highest-ranked liquidity target when available."""
    priority = liquidity.get("liquidity_priority", [])
    if priority:
        target = priority[0]
        return f"{target.get('name')} @ {target.get('price')}"

    buy_target = liquidity.get("nearest_buy_side_target")
    sell_target = liquidity.get("nearest_sell_side_target")
    target = buy_target or sell_target
    if target:
        return f"{target.get('name')} @ {target.get('price')}"
    return "none"


def format_detection(component: dict[str, Any]) -> str:
    """Format detected ICT components."""
    detected = component.get("detected", False)
    direction = component.get("direction")
    if direction:
        return f"{detected} ({direction})"
    return str(detected)


def format_list(values: list[str]) -> str:
    """Return clean terminal list text."""
    return ", ".join(values) if values else "none"


def display_label(value: Any) -> str:
    """Return readable display label for internal Market Watch names."""
    return str(value or "none").replace("_", " ").title()


if __name__ == "__main__":
    raise SystemExit(main())
