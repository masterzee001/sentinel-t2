from __future__ import annotations

from copy import deepcopy

from backend.symbols.symbol_registry import SymbolRegistry
from scripts.run_backtest_365d import approved_xau_smt_split
from scripts.run_validation_checkpoint import build_validation_checkpoint


def passing_report() -> dict:
    observer_diagnostics = {
        "XAUUSD": observer("GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES", 61, 0.86, 47.62),
        "BTCUSD": observer("CANDLES_AVAILABLE_NO_SETUPS", 0, 0.0, 0.0),
        "EURUSD": observer("GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES", 106, 1.23, 52.44),
        "GBPUSD": observer("GUARDRAILS_BLOCKING_ALL_OPPORTUNITIES", 117, 0.96, 48.96),
    }
    return {
        "advisor_mode_only": True,
        "production_symbols": ["US30", "NAS100"],
        "symbol_expansion": {"observer_only": True, "affect_production": False},
        "comparison": {
            "raw_baseline": {"pf": 1.16, "win_rate": 52.22, "trades": 123, "max_drawdown": 3.94},
            "approved_robustness_baseline": {"pf": 1.58, "win_rate": 58.7, "trades": 56, "max_drawdown": 2.97},
            "symbol_expansion_observer_only": {"pf": 1.58, "win_rate": 58.7, "trades": 56, "max_drawdown": 2.97},
        },
        "production_portfolio": {
            "source": "current_backtest_scan",
            "symbol_breakdown": {
                "US30": {"profit_factor": 1.69, "win_rate": 60.0, "trades": 47, "max_drawdown": 2.0},
                "NAS100": {"profit_factor": 1.6, "win_rate": 60.0, "trades": 39, "max_drawdown": 2.0},
            },
        },
        "reconciliation": {"status": "PASS"},
        "symbol_breakdown": {
            "US30": {"profit_factor": 1.69, "win_rate": 60.0, "trades": 47, "max_drawdown": 2.0},
            "NAS100": {"profit_factor": 1.6, "win_rate": 60.0, "trades": 39, "max_drawdown": 2.0},
        },
        "observer_diagnostics": observer_diagnostics,
        "xau_smt_split": approved_xau_smt_split(),
    }


def observer(status: str, trades: int, pf: float, wr: float) -> dict:
    return {
        "data_status": status,
        "trades": trades,
        "pf": pf,
        "wr": wr,
        "execution_allowed": False,
        "production_excluded": True,
        "historical_data": True,
        "candles_available": True,
    }


def checkpoint(report: dict, *, execution_mode: str = "advisor", guardrail_config: dict | None = None) -> dict:
    return build_validation_checkpoint(
        report,
        generated_at="2026-06-29T00:00:00+00:00",
        registry=SymbolRegistry(),
        guardrail_config=guardrail_config
        or {"robustness_365d": {"xauusd_smt_min_sample": 10, "xauusd_smt_sample_trades": 0}},
        execution_config={"execution_mode": execution_mode},
    )


def test_validation_checkpoint_passes_when_baseline_matches():
    result = checkpoint(passing_report())

    assert result["decision"] == "PASS"
    assert result["matches_approved_baseline"] is True
    assert all(gate["pass"] for gate in result["gates"])


def test_honest_metric_drift_from_legacy_baseline_does_not_fail_gates():
    report = passing_report()
    report["comparison"]["symbol_expansion_observer_only"].update({"pf": 1.21, "win_rate": 52.94, "trades": 73})

    result = checkpoint(report)

    assert result["decision"] == "PASS"
    assert result["matches_approved_baseline"] is False


def test_validation_checkpoint_fails_when_metrics_come_from_stored_constants():
    report = passing_report()
    report["production_portfolio"]["source"] = "approved_robustness_baseline"

    result = checkpoint(report)

    assert result["decision"] == "FAIL"
    assert not gate_passed(result, "production_metrics_recomputed_from_scan")


def test_validation_checkpoint_fails_when_report_does_not_reconcile():
    report = passing_report()
    report["reconciliation"] = {"status": "FAIL"}

    result = checkpoint(report)

    assert result["decision"] == "FAIL"
    assert not gate_passed(result, "production_reconciles_internally")


def test_observer_trades_cannot_affect_production_metrics():
    report = passing_report()
    report["observer_diagnostics"]["EURUSD"].update({"trades": 200, "pf": 9.0, "wr": 90.0})

    result = checkpoint(report)

    assert result["decision"] == "PASS"
    assert result["symbol_expansion_observer_only"]["pf"] == 1.58
    assert result["observer_diagnostics"]["EURUSD"]["trades"] == 200


def test_xau_smt_hard_block_remains_disabled_below_sample_threshold():
    result = checkpoint(passing_report())

    assert result["decision"] == "PASS"
    assert gate_passed(result, "xau_smt_hard_block_disabled_below_sample")


def test_validation_checkpoint_fails_if_observer_symbol_becomes_executable():
    report = passing_report()
    report["observer_diagnostics"]["EURUSD"]["execution_allowed"] = True

    result = checkpoint(report)

    assert result["decision"] == "FAIL"
    assert not gate_passed(result, "observer_symbols_execution_disabled")


def test_validation_checkpoint_fails_if_autonomous_mode_enabled():
    result = checkpoint(passing_report(), execution_mode="autonomous")

    assert result["decision"] == "FAIL"
    assert not gate_passed(result, "autonomous_execution_disabled")


def test_validation_checkpoint_fails_if_xau_hard_block_enabled_below_sample_threshold():
    report = passing_report()
    report["xau_smt_split"] = deepcopy(report["xau_smt_split"])
    report["xau_smt_split"]["rule"]["hard_block_enabled"] = True

    result = checkpoint(report)

    assert result["decision"] == "FAIL"
    assert not gate_passed(result, "xau_smt_hard_block_disabled_below_sample")


def gate_passed(result: dict, name: str) -> bool:
    return next(gate for gate in result["gates"] if gate["name"] == name)["pass"]
