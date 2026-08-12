"""Live trading constants must match the research that justified them.

The 6-day time stop was validated on 2026-08-12 and its own report recorded
the verdict "PROMOTABLE - improves on the half it was never chosen on". The
promotion was then never done: the research commit touched no engine file, so
the book kept running the 10-day stop that the work had already beaten, and
the discrepancy only surfaced when the operator read a Telegram message and
said "i thought we agreed 6 days".

A number in a report and a number in the engine drifting apart is silent by
nature. These tie them together.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXIT_RESEARCH = json.loads(
    (PROJECT_ROOT / "data" / "reports" / "exit_rules_research.json").read_text(encoding="utf-8")
)
engine = importlib.import_module("scripts.run_mean_reversion_live")


def test_live_time_stop_is_the_variant_the_research_chose():
    chosen = EXIT_RESEARCH["selection"]["chosen_on_discovery"]
    assert chosen == "H5_time_stop_6d"
    assert engine.MAX_HOLD_DAYS == 6, (
        f"research chose {chosen} but the engine holds for {engine.MAX_HOLD_DAYS} days"
    )


def test_the_chosen_variant_actually_validated_on_the_holdout():
    """Guards against promoting something on the strength of the half it was
    selected on."""
    selection = EXIT_RESEARCH["selection"]
    assert selection["VALIDATES_ON_HOLDOUT"] is True
    assert selection["holdout_net_rr"] > selection["baseline_holdout"]
    assert selection["beats_baseline_on_discovery"] is True


def test_the_live_variant_beats_the_incumbent_on_the_holdout():
    results = EXIT_RESEARCH["results"]
    live = results[f"H5_time_stop_{engine.MAX_HOLD_DAYS}d"]["holdout"]
    incumbent = results["H5_time_stop_10d"]["holdout"]
    assert live["rw_pf"] > incumbent["rw_pf"]
    assert live["net_rr"] > incumbent["net_rr"]
    assert live["positive_quarter_share"] >= incumbent["positive_quarter_share"]


def test_promoted_research_is_not_still_labelled_research_only():
    """The label is what let this sit unnoticed - a report that says
    RESEARCH_ONLY while its finding is live is worse than no label."""
    assert EXIT_RESEARCH["status"] == "PROMOTED"
    assert EXIT_RESEARCH["promotion"]["to"] == engine.MAX_HOLD_DAYS


def test_the_engine_tells_the_operator_the_hold_it_actually_uses():
    """The startup and fill messages quote MAX_HOLD_DAYS rather than a literal,
    so they cannot drift from behaviour."""
    source = (PROJECT_ROOT / "scripts" / "run_mean_reversion_live.py").read_text(encoding="utf-8")
    assert "{MAX_HOLD_DAYS}d" in source
    assert "or 10d" not in source
