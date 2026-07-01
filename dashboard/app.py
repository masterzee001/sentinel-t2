"""Project Sentinel Streamlit dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.components.charts import render_analytics
from dashboard.components.overview import (
    render_challenge_command_center_panel,
    render_assisted_execution_panel,
    render_demo_sandbox_panel,
    render_emergency_live_panel,
    render_live_paper_panel,
    render_market_watch_panel,
    render_overview,
)
from dashboard.components.tables import render_coach, render_journal, render_live_monitor, render_trade_plans
from dashboard.utils.data_loader import (
    analytics_dataframe,
    analytics_summary,
    build_dashboard_snapshot,
    load_challenge_command_center_summary,
    load_assisted_execution_summary,
    load_demo_sandbox_summary,
    coach_report,
    journal_dataframe,
    load_backtest_summary,
    load_dashboard_config,
    load_emergency_live_summary,
    load_journal_records,
    load_live_data_summary,
    load_live_paper_summary,
    load_market_watch_summary,
    load_monte_carlo_summary,
    plan_dataframe,
    symbol_dataframe,
)


def main() -> None:
    """Render the Project Sentinel dashboard."""
    st.set_page_config(page_title="Project Sentinel", layout="wide")
    st.title("Project Sentinel")
    st.caption("Advisor Mode only. No execution controls.")

    config = load_dashboard_config(PROJECT_ROOT / "dashboard" / "config.yaml")
    with st.sidebar:
        st.header("Navigation")
        page = st.radio("Page", config.get("pages", ["Overview"]), label_visibility="collapsed")
        st.caption(f"Refresh target: {config.get('refresh_seconds', 60)} seconds")

    snapshot = build_dashboard_snapshot(config=config, project_root=PROJECT_ROOT)
    backtest = load_backtest_summary(PROJECT_ROOT, config)
    live_data = load_live_data_summary(PROJECT_ROOT, config)
    stress = load_monte_carlo_summary(PROJECT_ROOT, config)
    market_watch = load_market_watch_summary(PROJECT_ROOT, config)
    live_paper = load_live_paper_summary(PROJECT_ROOT, config)
    emergency_live = load_emergency_live_summary(PROJECT_ROOT, config)
    challenge_command_center = load_challenge_command_center_summary(PROJECT_ROOT, config)
    assisted_execution = load_assisted_execution_summary(PROJECT_ROOT, config)
    demo_sandbox = load_demo_sandbox_summary(PROJECT_ROOT, config)

    if page == "Overview":
        render_overview(st, snapshot)
    elif page == "Live Monitor":
        render_live_monitor(st, symbol_dataframe(snapshot))
    elif page == "Trade Plans":
        render_trade_plans(st, plan_dataframe(snapshot))
    elif page == "Analytics":
        render_analytics(st, analytics_summary(backtest), analytics_dataframe(backtest), live_data, stress)
    elif page == "Market Watch":
        render_market_watch_panel(st, market_watch)
    elif page == "Live Paper":
        render_live_paper_panel(st, live_paper)
    elif page == "Emergency Live":
        render_emergency_live_panel(st, emergency_live)
    elif page == "Challenge Command Center":
        render_challenge_command_center_panel(st, challenge_command_center)
    elif page == "Assisted Execution":
        render_assisted_execution_panel(st, assisted_execution)
    elif page == "Demo Sandbox":
        render_demo_sandbox_panel(st, demo_sandbox)
    elif page == "Journal":
        records = load_journal_records(PROJECT_ROOT, config)
        render_journal(st, journal_dataframe(records))
    elif page == "AI Coach":
        render_coach(st, coach_report(PROJECT_ROOT, backtest))
    else:
        st.info("Page unavailable.")


if __name__ == "__main__":
    main()
