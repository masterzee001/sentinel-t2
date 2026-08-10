from __future__ import annotations

from pathlib import Path

from backend.sizing.expectancy_sizer import ExpectancySizer
from scripts.run_sizing_walkforward import book_metrics, sizing_verdict, walkforward_evaluation


def cell_trades(symbol: str, phase: str, killzone: str, rrs: list[float], quarter: str = "2025-Q1") -> list[dict]:
    month = {"Q1": "02", "Q2": "05", "Q3": "08", "Q4": "11"}[quarter[-2:]]
    year = quarter[:4]
    return [
        {
            "symbol": symbol,
            "narrative_phase": phase,
            "killzone": killzone,
            "rr": rr,
            "timestamp": f"{year}-{month}-10T08:00:00+00:00",
        }
        for rr in rrs
    ]


def test_build_table_aggregates_cells():
    trades = cell_trades("US30", "expansion", "new_york_open", [2.9, -1.0, 2.9, -1.0])
    table = ExpectancySizer.build_table(trades)
    row = table[ExpectancySizer.cell_key("US30", "expansion", "new_york_open")]

    assert row["trades"] == 4
    assert row["wins"] == 2
    assert row["losses"] == 2
    assert row["net_rr"] == 3.8
    assert row["avg_rr"] == 0.95
    assert row["profit_factor"] == 2.9


def test_multiplier_bands_strong_positive_marginal_starved_unproven():
    strong = cell_trades("US30", "expansion", "new_york_open", [1.0] * 20)
    positive = cell_trades("US30", "reversal", "new_york_open", [0.1] * 20)
    marginal = cell_trades("US30", "range", "new_york_open", [0.02] * 20)
    starved = cell_trades("XAUUSD", "distribution", "new_york_open", [-0.5] * 20)
    sizer = ExpectancySizer.from_trades([*strong, *positive, *marginal, *starved])

    assert sizer.multiplier_for("US30", "expansion", "new_york_open")["multiplier"] == 1.5
    assert sizer.multiplier_for("US30", "reversal", "new_york_open")["multiplier"] == 1.0
    assert sizer.multiplier_for("US30", "range", "new_york_open")["multiplier"] == 0.5
    starved_result = sizer.multiplier_for("XAUUSD", "distribution", "new_york_open")
    assert starved_result["multiplier"] == 0.0
    assert starved_result["classification"] == "STARVED"
    unproven = sizer.multiplier_for("NAS100", "expansion", "london_open")
    assert unproven["classification"] == "UNPROVEN"
    assert unproven["multiplier"] == 0.5


def test_size_risk_scales_and_blocks():
    sizer = ExpectancySizer.from_trades(cell_trades("XAUUSD", "distribution", "new_york_open", [-0.5] * 20))

    sized = sizer.size_risk(
        symbol="XAUUSD", narrative_phase="distribution", killzone="new_york_open", base_risk_percent=0.5
    )

    assert sized["risk_percent"] == 0.0
    assert sized["trade_allowed"] is False


def test_table_round_trips_through_disk(tmp_path: Path):
    sizer = ExpectancySizer.from_trades(cell_trades("US30", "expansion", "new_york_open", [1.0] * 20))
    path = tmp_path / "table.json"
    sizer.save(path)
    loaded = ExpectancySizer.load(path)

    assert loaded.multiplier_for("US30", "expansion", "new_york_open")["multiplier"] == 1.5


def test_walkforward_uses_only_prior_quarters_and_judges_out_of_sample():
    # 4 training quarters of a strongly positive US30 cell and a losing XAU cell,
    # then an evaluation quarter containing one trade in each cell.
    trades: list[dict] = []
    for quarter in ("2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"):
        trades += cell_trades("US30", "expansion", "new_york_open", [1.0] * 5, quarter)
        trades += cell_trades("XAUUSD", "distribution", "new_york_open", [-0.6] * 5, quarter)
    trades += cell_trades("US30", "expansion", "new_york_open", [2.0], "2025-Q1")
    trades += cell_trades("XAUUSD", "distribution", "new_york_open", [-1.0], "2025-Q1")

    report = walkforward_evaluation(sorted(trades, key=lambda t: t["timestamp"]))

    assert report["evaluated_quarters"] == 1
    assert report["evaluated_trades"] == 2
    # Baseline: +2.0 - 1.0 = +1.0R. Sized: 2.0*1.5 (strong) + -1.0*0.0 (starved) = +3.0R.
    assert report["baseline"]["net_rr"] == 1.0
    assert report["sized"]["net_rr"] == 3.0
    assert report["trades_skipped_by_sizer"] == 1
    assert report["verdict"]["promote"] is True


def test_sizing_verdict_requires_both_improvements():
    better = book_metrics([3.0])
    worse = book_metrics([1.0, -1.0])

    assert sizing_verdict(worse, better)["promote"] is True
    assert sizing_verdict(better, worse)["promote"] is False
