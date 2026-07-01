"""Overview page components."""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.news_filter.news_filter import NewsFilter


def render_overview(st: Any, snapshot: dict[str, Any]) -> None:
    """Render dashboard overview metrics."""
    risk = snapshot.get("risk", {})
    account = risk.get("account", {})
    permission = risk.get("permission", {})
    readiness = snapshot.get("readiness", {})
    columns = st.columns(6)
    columns[0].metric("Balance", f"{account.get('balance', 0.0)} {account.get('currency', 'USD')}")
    columns[1].metric("Equity", f"{account.get('equity', 0.0)} {account.get('currency', 'USD')}")
    columns[2].metric("Risk Status", permission.get("status", "UNKNOWN"))
    columns[3].metric("News Status", NewsFilter.format_status(snapshot.get("news", {})))
    columns[4].metric("Execution Mode", snapshot.get("execution_mode", "advisor"))
    columns[5].metric("Readiness", "READY" if readiness.get("ready") else "BLOCKED")
    if snapshot.get("error"):
        st.warning(f"Live MT5 snapshot unavailable: {snapshot['error']}")
    st.caption("Advisor Mode only. No execution controls are available in this dashboard.")
    registry_rows = snapshot.get("symbol_registry", [])
    if registry_rows:
        st.subheader("Symbol Registry")
        st.dataframe(registry_rows, use_container_width=True, hide_index=True)
    render_market_watch_panel(st, snapshot.get("market_watch", {}))


def render_market_watch_panel(st: Any, market_watch: dict[str, Any]) -> None:
    """Render Market Watch advisory diagnostics."""
    st.subheader("Market Watch")
    if not market_watch.get("available"):
        st.info("Market Watch report unavailable. Run scripts/run_market_watch_backtest.py.")
        return
    data = market_watch.get("data", {})
    columns = st.columns(4)
    columns[0].metric("Mode", "ADVISORY ONLY")
    columns[1].metric("Production Impact", str(bool(data.get("market_watch", {}).get("affect_production", False))).upper())
    columns[2].metric("Best Strategy", data.get("best_strategy", "none"))
    columns[3].metric("Decision", data.get("decision", "FAIL"))
    rows = []
    for symbol, item in data.get("strategy_diagnostics", {}).items():
        scores = item.get("scores", {})
        rows.append(
            {
                "symbol": symbol,
                "pattern": item.get("dominant_pattern", "no_clear_pattern"),
                "ict": scores.get("ict_liquidity", 0),
                "trend": scores.get("trend_following", 0),
                "mean_reversion": scores.get("mean_reversion", 0),
                "selected_strategy": item.get("selected_strategy", "no_trade"),
                "session_quality": item.get("session_quality", 0),
                "status": item.get("market_watch_status", "ADVISORY"),
                "production_impact": bool(item.get("affects_production", False)),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_live_paper_panel(st: Any, live_paper: dict[str, Any]) -> None:
    """Render Sprint 7 live paper phase telemetry."""
    st.subheader("Live Paper Phase")
    if not live_paper.get("available"):
        st.info("Live paper report unavailable. Run scripts/run_live_paper_phase.py.")
        return
    data = live_paper.get("data", {})
    health = data.get("live_feed_health", {})
    stats = data.get("paper_stats", {})
    drift = data.get("drift", {})
    columns = st.columns(5)
    columns[0].metric("Live Feed Health", health.get("classification", "UNUSABLE"))
    columns[1].metric("Paper Trades", stats.get("trades", 0))
    columns[2].metric("PF", stats.get("pf", 0.0))
    columns[3].metric("WR", f"{stats.get('win_rate', 0.0)}%")
    columns[4].metric("Drift", drift.get("classification", "UNKNOWN"))
    st.caption("Paper trading only. Broker order submission and autonomous execution are disabled.")
    active = data.get("active_paper_trades", [])
    if active:
        st.subheader("Active Paper Trades")
        st.dataframe(pd.DataFrame(active), use_container_width=True, hide_index=True)
    trades = data.get("paper_trades", [])
    if trades:
        st.subheader("Paper Trade Stats")
        st.dataframe(
            pd.DataFrame(trades).reindex(
                columns=[
                    "paper_trade_id",
                    "symbol",
                    "state",
                    "strategy",
                    "micro_regime",
                    "quality_grade",
                    "rr",
                    "spread",
                    "slippage",
                    "latency",
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("Slippage Monitor")
        st.dataframe(
            pd.DataFrame(trades).reindex(columns=["paper_trade_id", "symbol", "expected_entry", "actual_simulated_entry", "slippage_points"]),
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("Latency Monitor")
        st.dataframe(
            pd.DataFrame(trades).reindex(columns=["paper_trade_id", "symbol", "signal_delay_ms", "execution_delay_ms", "latency"]),
            use_container_width=True,
            hide_index=True,
        )


def render_emergency_live_panel(st: Any, emergency_live: dict[str, Any]) -> None:
    """Render emergency live deployment controls/status."""
    st.subheader("Emergency Live Deployment")
    if not emergency_live.get("available"):
        st.info("Emergency live report unavailable. Run scripts/run_emergency_live_protocol.py.")
        return
    data = emergency_live.get("data", {})
    config = data.get("config", {})
    columns = st.columns(6)
    columns[0].metric("Live Deployment Mode", data.get("status", "UNKNOWN"))
    columns[1].metric("Risk Lock", "LOCKED" if data.get("risk_lock", {}).get("locked") else "OPEN")
    columns[2].metric("Kill Switch", data.get("status", "UNKNOWN"))
    columns[3].metric("Live PnL", "0.0R")
    columns[4].metric("Consecutive Losses", 0)
    columns[5].metric("Max Trades/Day", config.get("max_trades_per_day", 2))
    st.caption("Controlled assisted live only. Human approval mandatory. Sentinel broker order submission disabled.")
    queue = data.get("approval_queue", [])
    if queue:
        st.subheader("Approval Queue")
        rows = []
        for item in queue:
            proposal = item.get("proposal", {})
            rows.append(
                {
                    "approval_id": item.get("approval_id"),
                    "status": item.get("status"),
                    "symbol": proposal.get("symbol"),
                    "strategy": proposal.get("strategy"),
                    "quality_grade": proposal.get("quality_grade"),
                    "risk_percent": proposal.get("risk_percent"),
                    "expected_pf": proposal.get("expected_pf"),
                    "expected_wr": proposal.get("expected_wr"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_challenge_command_center_panel(st: Any, challenge: dict[str, Any]) -> None:
    """Render advisory Challenge Command Center panels."""
    st.subheader("Challenge Command Center")
    if not challenge.get("available"):
        st.info("Challenge Command Center report unavailable. Run scripts/run_challenge_command_center.py.")
        return
    data = challenge.get("data", {})
    status = data.get("challenge_status", {})
    profit = data.get("profit_progress", {})
    risk = data.get("risk_buffer", {})
    governor = data.get("governor_status", {})
    performance = data.get("trading_performance", {})
    recommendation = data.get("recommendation", {})

    columns = st.columns(4)
    columns[0].metric("Challenge Mode", status.get("challenge_mode", "DISABLED"))
    columns[1].metric("Profile", status.get("profile", "BALANCED"))
    columns[2].metric("Current Phase", status.get("current_phase", "PHASE_1"))
    columns[3].metric("Status", status.get("status", "PAUSED"))
    st.caption("Advisory dashboard only. Challenge activation, broker orders, and autonomous execution are disabled.")

    st.subheader("Profit Progress")
    progress_columns = st.columns(5)
    progress_columns[0].metric("Starting Balance", profit.get("starting_balance", 0.0))
    progress_columns[1].metric("Current Balance", profit.get("current_balance", 0.0))
    progress_columns[2].metric("Current Equity", profit.get("current_equity", 0.0))
    progress_columns[3].metric("Net PnL", f"{profit.get('net_pnl', 0.0)}")
    progress_columns[4].metric("Net PnL %", f"{profit.get('net_pnl_percent', 0.0)}%")
    st.progress(min(100, int(float(profit.get("progress_percent", 0.0) or 0.0))) / 100)
    st.write(f"Remaining target: {profit.get('remaining_target', 0.0)} / {profit.get('remaining_target_percent', 0.0)}%")

    st.subheader("Risk Buffer")
    daily = risk.get("daily_loss_limit", {})
    total = risk.get("total_drawdown_limit", {})
    risk_columns = st.columns(3)
    risk_columns[0].metric("Daily Used", f"{daily.get('current_used_percent', 0.0)}%")
    risk_columns[1].metric("Total DD Used", f"{total.get('current_used_percent', 0.0)}%")
    risk_columns[2].metric("State", risk.get("color_state", "SAFE"))

    st.subheader("Governor Status")
    governor_columns = st.columns(5)
    governor_columns[0].metric("Soft Stop", f"{governor.get('soft_stop_percent', 2)}%")
    governor_columns[1].metric("Hard Stop", f"{governor.get('hard_stop_percent', 3)}%")
    governor_columns[2].metric("Loss Streak", governor.get("loss_streak", 0))
    governor_columns[3].metric("Risk Mode", governor.get("risk_mode", "NORMAL"))
    governor_columns[4].metric("Current Risk", f"{governor.get('current_risk_percent', 0.8)}%")

    st.subheader("Trading Performance")
    st.dataframe(pd.DataFrame([performance]), use_container_width=True, hide_index=True)

    st.subheader("Challenge Recommendation")
    st.write(recommendation.get("recommendation", "No recommendation available."))
    st.caption(f"Confidence: {recommendation.get('confidence', 'LOW')}")


def render_assisted_execution_panel(st: Any, assisted: dict[str, Any]) -> None:
    """Render demo-only assisted execution bridge status."""
    st.subheader("Assisted Execution Bridge")
    if not assisted.get("available"):
        st.info("Assisted execution report unavailable. Run scripts/run_assisted_execution_status.py.")
        return
    data = assisted.get("data", {})
    ticket = data.get("current_ticket", {})
    validation = data.get("final_safety_status", {})
    checks = validation.get("checks", {})
    dry_run = data.get("dry_run", {})
    config = data.get("config", {})
    safety = data.get("safety", {})
    submit_state = "DRY RUN ONLY" if safety.get("dry_run_only", not config.get("submit_orders", False)) else "GATED"

    columns = st.columns(6)
    columns[0].metric("Assisted Execution", data.get("assisted_execution", "DISABLED"))
    columns[1].metric("Mode", data.get("mode", "DEMO_ONLY"))
    columns[2].metric("Account Mode", data.get("account_mode", "UNKNOWN"))
    columns[3].metric("Final Safety", validation.get("status", "BLOCKED"))
    columns[4].metric("Submit Orders", "BLOCKED" if not config.get("submit_orders", False) else "ENABLED")
    columns[5].metric("Dry Run", submit_state)
    st.caption("DRY RUN ONLY. No funded execution, no autonomous execution, and no dashboard broker-submit control.")

    st.subheader("Current Ticket")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ticket_id": ticket.get("ticket_id"),
                    "status": ticket.get("status"),
                    "expires_at": ticket.get("expires_at"),
                    "symbol": ticket.get("symbol"),
                    "side": ticket.get("side"),
                    "risk_percent": ticket.get("risk_percent"),
                    "lot_size": ticket.get("lot_size"),
                    "grade": ticket.get("grade"),
                    "confidence": ticket.get("confidence"),
                }
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Final Safety Gates")
    st.dataframe(pd.DataFrame([{"check": key, "passed": value} for key, value in checks.items()]), use_container_width=True, hide_index=True)

    st.subheader("Dry Run Payload")
    st.dataframe(pd.DataFrame([dry_run.get("order_payload", {})]), use_container_width=True, hide_index=True)


def render_demo_sandbox_panel(st: Any, sandbox_summary: dict[str, Any]) -> None:
    """Render demo-only sandbox symbol tier status."""
    st.subheader("Demo Sandbox")
    if not sandbox_summary.get("available"):
        st.info("Demo sandbox report unavailable. Run scripts/run_demo_sandbox_status.py.")
        return
    data = sandbox_summary.get("data", {})
    sandbox = data.get("sandbox", {})
    performance = data.get("performance", {})
    tiers = data.get("symbol_tiers", {})
    dry_run = data.get("dry_run", {})
    integration = data.get("assisted_integration", {})

    columns = st.columns(6)
    columns[0].metric("Sandbox", "ENABLED" if sandbox.get("enabled") else "DISABLED")
    columns[1].metric("Mode", sandbox.get("mode", "DEMO_ONLY"))
    columns[2].metric("Allowed", ", ".join(sandbox.get("allowed_symbols", [])))
    columns[3].metric("Submit Orders", "ENABLED" if sandbox.get("submit_orders") else "BLOCKED")
    columns[4].metric("Production Excluded", str(bool(sandbox.get("production_metrics_excluded", True))).upper())
    columns[5].metric("Challenge", "BLOCKED" if not sandbox.get("challenge_mode_allowed", False) else "REVIEW")
    st.caption("SANDBOX DEMO ONLY. NOT PRODUCTION. NOT FUNDED. NOT CHALLENGE.")

    st.subheader("Symbol Tiers")
    st.dataframe(
        pd.DataFrame(
            [
                {"tier": "PRODUCTION", "symbols": ", ".join(tiers.get("production", []))},
                {"tier": "DEMO_SANDBOX", "symbols": ", ".join(tiers.get("demo_sandbox", []))},
                {"tier": "OBSERVER_ONLY", "symbols": ", ".join(tiers.get("observer_only", []))},
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Sandbox Performance")
    st.dataframe(pd.DataFrame([performance]), use_container_width=True, hide_index=True)

    st.subheader("Assisted Sandbox Dry Run")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "ticket_id": dry_run.get("ticket_id"),
                    "ticket_type": dry_run.get("ticket_type"),
                    "symbol": dry_run.get("symbol"),
                    "risk_percent": dry_run.get("risk_percent"),
                    "validation": dry_run.get("validation", {}).get("status"),
                    "dry_run_only": integration.get("dry_run_only", True),
                }
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
