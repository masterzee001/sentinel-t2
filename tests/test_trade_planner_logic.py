from __future__ import annotations

from backend.trade_planner.trade_planner import TradePlanner


def ict_fixture() -> dict:
    return {
        "fvg": {"detected": True, "direction": "bullish", "low": 100.0, "high": 110.0},
        "order_block": {"detected": True, "direction": "bullish", "low": 105.0, "high": 115.0},
        "premium_discount": {"current_zone": "discount"},
    }


def liquidity_fixture() -> dict:
    return {
        "latest_sweep": {"level_price": 95.0},
        "liquidity_classification": {
            "internal": [
                {"name": "Internal Swing High", "price": 125.0, "side": "buy_side"},
                {"name": "Internal Swing Low", "price": 90.0, "side": "sell_side"},
            ],
            "engineered": [
                {"name": "EQH", "price": 150.0, "side": "buy_side"},
                {"name": "EQL", "price": 80.0, "side": "sell_side"},
            ],
            "external": [
                {"name": "PDH", "price": 180.0, "side": "buy_side"},
                {"name": "PDL", "price": 70.0, "side": "sell_side"},
            ],
        },
    }


def test_entry_calculation_uses_ob_fvg_overlap():
    entry = TradePlanner.calculate_entry(ict_fixture(), "bullish")

    assert entry == {
        "type": "limit",
        "price": 107.5,
        "source": "OB_FVG_confluence",
    }


def test_entry_calculation_falls_back_to_fvg_midpoint():
    ict = ict_fixture()
    ict["order_block"] = {"detected": False}

    entry = TradePlanner.calculate_entry(ict, "bullish")

    assert entry["price"] == 105.0
    assert entry["source"] == "FVG_midpoint"


def test_planner_test_mode_entry_ignores_rejected_zone():
    ict = ict_fixture()
    ict["premium_discount"] = {"current_zone": "unavailable"}

    entry = TradePlanner.calculate_test_mode_entry(ict, "bullish", liquidity_fixture())

    assert entry["price"] == 107.5
    assert entry["source"] == "OB_FVG_confluence_test_mode"


def test_stop_loss_logic_uses_sweep_then_buffer(tmp_path):
    planner = TradePlanner(connector=object(), config_dir=tmp_path)

    stop = planner.calculate_stop_loss(
        symbol="XAUUSD",
        direction="bullish",
        entry_price=107.5,
        latest_sweep={"level_price": 95.0},
        order_block=ict_fixture()["order_block"],
        liquidity=liquidity_fixture(),
    )

    assert stop["price"] == 94.0
    assert stop["distance"] == 13.5
    assert stop["source"] == "liquidity_sweep"


def test_planner_test_mode_stop_loss_uses_directional_liquidity(tmp_path):
    planner = TradePlanner(connector=object(), config_dir=tmp_path)

    stop = planner.calculate_test_mode_stop_loss(
        symbol="XAUUSD",
        direction="bullish",
        entry_price=107.5,
        liquidity=liquidity_fixture(),
    )

    assert stop["price"] == 94.0
    assert stop["distance"] == 13.5
    assert stop["source"] == "test_mode_directional_liquidity"


def test_tp_selection_uses_directional_liquidity_classes():
    targets = TradePlanner.select_take_profit_targets("bullish", 107.5, 13.5, liquidity_fixture())

    assert targets == {"tp1": 125.0, "tp2": 150.0, "tp3": 180.0}


def test_tp_selection_for_short_uses_targets_below_entry():
    targets = TradePlanner.select_take_profit_targets("bearish", 107.5, 10.0, liquidity_fixture())

    assert targets == {"tp1": 90.0, "tp2": 80.0, "tp3": 70.0}


def test_wrong_side_targets_are_skipped():
    liquidity = {
        "liquidity_classification": {
            "internal": [{"name": "Wrong Side", "price": 90.0, "side": "sell_side"}],
            "engineered": [{"name": "EQH", "price": 150.0, "side": "buy_side"}],
            "external": [
                {"name": "Wrong Side External", "price": 170.0, "side": "sell_side"},
                {"name": "PDH", "price": 180.0, "side": "buy_side"},
            ],
        }
    }

    targets = TradePlanner.select_take_profit_targets("bullish", 100.0, 10.0, liquidity)

    assert targets == {"tp1": 150.0, "tp2": 180.0, "tp3": 180.0}


def test_poor_rr_final_target_is_skipped_for_next_valid_external():
    liquidity = {
        "liquidity_classification": {
            "internal": [],
            "engineered": [],
            "external": [
                {"name": "Nearby PDL", "price": 80.0, "side": "sell_side"},
                {"name": "Far Weekly Low", "price": 60.0, "side": "sell_side"},
            ],
        }
    }

    targets = TradePlanner.select_take_profit_targets("bearish", 100.0, 10.0, liquidity)

    assert targets["tp3"] == 60.0


def test_targets_are_ordered_by_monotonic_rr():
    liquidity = {
        "liquidity_classification": {
            "internal": [{"name": "Far Internal", "price": 70.0, "side": "sell_side"}],
            "engineered": [{"name": "Near Engineered", "price": 90.0, "side": "sell_side"}],
            "external": [{"name": "Final External", "price": 60.0, "side": "sell_side"}],
        }
    }

    targets = TradePlanner.select_take_profit_targets("bearish", 100.0, 10.0, liquidity)
    rr_values = [
        TradePlanner.calculate_rr(100.0, targets["tp1"], 10.0),
        TradePlanner.calculate_rr(100.0, targets["tp2"], 10.0),
        TradePlanner.calculate_rr(100.0, targets["tp3"], 10.0),
    ]

    assert targets == {"tp1": 90.0, "tp2": 70.0, "tp3": 60.0}
    assert rr_values[0] <= rr_values[1] <= rr_values[2]


def test_lot_size_math():
    lot_size = TradePlanner.calculate_lot_size(
        risk_amount=100.0,
        stop_distance=10.0,
        symbol_info={"trade_tick_value": 1.0, "trade_tick_size": 1.0, "volume_min": 0.01, "volume_step": 0.01},
        lot_config={"lot_precision": 2},
    )

    assert lot_size == 10.0


def test_rr_calculation():
    assert TradePlanner.calculate_rr(100.0, 130.0, 10.0) == 3.0


def test_planner_test_mode_fills_missing_take_profits():
    targets = TradePlanner.fill_test_mode_take_profit_targets(
        direction="bullish",
        entry_price=100.0,
        stop_distance=10.0,
        take_profit={"tp1": 0.0, "tp2": 140.0, "tp3": 0.0},
    )

    assert targets == {"tp1": 110.0, "tp2": 140.0, "tp3": 130.0}


def test_execution_gating_rejects_upstream_blocks():
    reasons = TradePlanner.evaluate_execution_gates(
        confidence={"decision": "REJECTED", "rejection_reasons": ["Outside valid session"]},
        risk={"permission": {"trade_allowed": False, "block_reasons": ["Daily loss limit hit"]}},
        ict={"execution_ready": False, "rejection_reasons": ["MSS not confirmed"]},
        direction="bullish",
        entry={"price": 100.0},
        stop_loss={"distance": 10.0},
        take_profit={"tp1": 120.0, "tp2": 140.0, "tp3": 160.0},
        risk_block={"rr_to_tp3": 6.0},
    )

    assert "Confidence Engine decision is not APPROVED" in reasons
    assert "Outside valid session" in reasons
    assert "Risk Governor blocked trading" in reasons
    assert "Daily loss limit hit" in reasons
    assert "ICT execution is not ready" in reasons
    assert "MSS not confirmed" in reasons


def test_execution_gating_allows_clean_plan():
    reasons = TradePlanner.evaluate_execution_gates(
        confidence={"decision": "APPROVED", "rejection_reasons": []},
        risk={"permission": {"trade_allowed": True, "block_reasons": []}},
        ict={"execution_ready": True, "rejection_reasons": []},
        direction="bullish",
        entry={"price": 100.0},
        stop_loss={"distance": 10.0},
        take_profit={"tp1": 120.0, "tp2": 140.0, "tp3": 160.0},
        risk_block={"rr_to_tp3": 6.0},
    )

    assert reasons == []


def test_rr_below_three_rejects_plan():
    reasons = TradePlanner.evaluate_execution_gates(
        confidence={"decision": "APPROVED", "rejection_reasons": []},
        risk={"permission": {"trade_allowed": True, "block_reasons": []}},
        ict={"execution_ready": True, "rejection_reasons": []},
        direction="bullish",
        entry={"price": 100.0},
        stop_loss={"distance": 10.0},
        take_profit={"tp1": 110.0, "tp2": 120.0, "tp3": 125.0},
        risk_block={"rr_to_tp3": 2.5},
    )

    assert "RR below minimum 3" in reasons


def test_plan_quality_invalid_when_rr_below_three_without_test_mode():
    assert TradePlanner.determine_plan_quality(
        execution_allowed=False,
        planner_test_mode=False,
        synthetic_plan=False,
        rejection_reasons=["RR below minimum 3"],
    ) == "invalid"


def test_plan_quality_diagnostic_only_in_planner_test_mode():
    assert TradePlanner.determine_plan_quality(
        execution_allowed=False,
        planner_test_mode=True,
        synthetic_plan=True,
        rejection_reasons=["Confidence Engine decision is not APPROVED"],
    ) == "diagnostic_only"
