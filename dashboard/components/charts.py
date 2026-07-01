"""Analytics charts for the Streamlit dashboard."""

from __future__ import annotations

from typing import Any

import pandas as pd


def render_analytics(
    st: Any,
    summary: dict[str, Any],
    analytics: pd.DataFrame,
    live_data: dict[str, Any] | None = None,
    stress: dict[str, Any] | None = None,
) -> None:
    """Render performance and live-data analytics charts."""
    if not summary.get("available") or analytics.empty or float(analytics["value"].sum()) == 0.0:
        st.info("No backtest summary available. Run backtest first.")
    else:
        windows = [
            (label, metrics)
            for label, metrics in (
                ("30D", summary.get("days_30", {})),
                ("90D", summary.get("days_90", {})),
                ("365D", summary.get("days_365", {})),
            )
            if metrics and int(metrics.get("trades", 0) or 0) > 0
        ]
        columns = st.columns(max(len(windows) * 2 + 1, 1))
        column_index = 0
        for label, metrics in windows:
            columns[column_index].metric(f"{label} PF", metrics.get("pf", 0.0))
            columns[column_index + 1].metric(f"{label} WR", f"{metrics.get('win_rate', 0.0)}%")
            column_index += 2
        columns[column_index].metric("Phase Decision", summary.get("phase_decision", "Unavailable"))

        for metric in ("Profit Factor", "Win Rate", "Max Drawdown", "Trade Count"):
            rows = analytics[analytics["metric"] == metric].set_index("window")
            st.markdown(f"**{metric}**")
            st.bar_chart(rows["value"])

        st.markdown("**Summary**")
        st.write(f"Generated at: {summary.get('generated_at', 'unknown')}")

    render_monte_carlo(st, stress or {})
    render_live_data_analytics(st, live_data or {})


def render_monte_carlo(st: Any, stress: dict[str, Any]) -> None:
    """Render Monte Carlo stress-test analytics."""
    st.markdown("**Monte Carlo Stress Test**")
    if not stress.get("available"):
        st.info("No Monte Carlo stress test available yet.")
        return

    safe_risk = stress.get("safe_risk_percent", 0.0)
    columns = st.columns(3)
    columns[0].metric("Safe Risk", f"{safe_risk}%")
    columns[1].metric("Autonomous Mode", "NOT RECOMMENDED" if not stress.get("autonomous_mode_recommended") else "RECOMMENDED")
    columns[2].metric("Trades Used", stress.get("trades_used", 0))

    comparison_rows = []
    for label, result in stress.get("risk_models", {}).items():
        comparison_rows.append(
            {
                "risk_model": label,
                "p95_dd": result.get("drawdown", {}).get("p95_dd", 0.0),
                "max_dd": result.get("drawdown", {}).get("max_dd", 0.0),
                "breach_4_percent": result.get("risk_of_ruin", {}).get("breach_4_percent", 0.0),
                "breach_6_percent": result.get("risk_of_ruin", {}).get("breach_6_percent", 0.0),
                "worst_losing_streak": result.get("streaks", {}).get("worst_losing_streak", 0),
            }
        )
    if comparison_rows:
        comparison = pd.DataFrame(comparison_rows).set_index("risk_model")
        st.markdown("**Risk Model Comparison**")
        st.dataframe(comparison)
        st.bar_chart(comparison[["p95_dd", "max_dd"]])

    safe_model = stress.get("risk_models", {}).get(f"{float(safe_risk):g}%", {})
    histogram = safe_model.get("drawdown", {}).get("histogram", {})
    if histogram:
        st.markdown("**DD Histogram**")
        st.bar_chart(pd.Series(histogram, name="simulations"))

    recommendations = stress.get("recommendations", [])
    notes = stress.get("risk_notes", [])
    if recommendations:
        st.markdown("**Safe Risk Recommendation**")
        for item in recommendations[:4]:
            st.write(f"- {item}")
    if notes:
        st.markdown("**Risk Notes**")
        for item in notes[:4]:
            st.write(f"- {item}")


def render_live_data_analytics(st: Any, summary: dict[str, Any]) -> None:
    """Render live-data collection summaries."""
    st.markdown("**Live Data Collection**")
    if not summary.get("available"):
        st.info("No live data stats available yet.")
        return

    st.write(f"Records collected: {summary.get('total_records', 0)}")
    symbol_rows = []
    for symbol, stats in summary.get("symbols", {}).items():
        if int(stats.get("total_scans", 0) or 0) == 0:
            continue
        symbol_rows.append(
            {
                "symbol": symbol,
                "Warm": stats.get("warm", 0),
                "Hot": stats.get("hot", 0),
                "Exec Ready": stats.get("execution_ready", 0),
                "Mode": stats.get("symbol_mode", "production"),
            }
        )
    if symbol_rows:
        symbol_frame = pd.DataFrame(symbol_rows).set_index("symbol")
        st.dataframe(symbol_frame)
        st.bar_chart(symbol_frame[["Warm", "Hot", "Exec Ready"]])

    for title, key in (
        ("Setups by Killzone", "killzones"),
        ("Setups by Narrative", "narratives"),
        ("Rejection Reasons", "rejection_reasons"),
    ):
        values = summary.get(key, {})
        if not values:
            continue
        st.markdown(f"**{title}**")
        st.bar_chart(pd.Series(values, name="count"))
