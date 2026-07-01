"""Table components for the Streamlit dashboard."""

from __future__ import annotations

from typing import Any

import pandas as pd


def render_live_monitor(st: Any, symbols: pd.DataFrame) -> None:
    """Render live monitor symbols."""
    st.dataframe(
        symbols.reindex(
            columns=[
                "symbol",
                "badge",
                "mode",
                "state",
                "raw_confidence",
                "adjusted_confidence",
                "guardrail_penalty",
                "decision",
                "killzone",
                "narrative",
                "observer_note",
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def render_trade_plans(st: Any, plans: pd.DataFrame) -> None:
    """Render trade plan table."""
    st.dataframe(plans, use_container_width=True, hide_index=True)


def render_journal(st: Any, journal: pd.DataFrame) -> None:
    """Render filterable journal table."""
    if journal.empty:
        st.info("No journal records available.")
        return
    symbols = sorted(value for value in journal["symbol"].dropna().unique() if value)
    selected = st.multiselect("Symbols", symbols, default=symbols)
    filtered = journal[journal["symbol"].isin(selected)] if selected else journal
    decision_values = sorted(value for value in filtered["decision"].dropna().unique() if value)
    decision = st.selectbox("Decision", ["All", *decision_values])
    if decision != "All":
        filtered = filtered[filtered["decision"] == decision]
    st.dataframe(filtered, use_container_width=True, hide_index=True)


def render_coach(st: Any, report: dict[str, Any]) -> None:
    """Render AI Coach recommendations."""
    st.subheader(report.get("summary", "AI Coach unavailable."))
    for section in ("strengths", "weaknesses", "recommendations", "risk_notes", "next_actions"):
        st.markdown(f"**{section.replace('_', ' ').title()}**")
        items = report.get(section, [])
        if not items:
            st.caption("none")
            continue
        for item in items:
            st.write(f"[{item.get('severity', 'INFO')}] {item.get('category', 'psychology')}: {item.get('message', '')}")
