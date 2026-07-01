"""Challenge Mode Simulator for EFDE-enhanced Sentinel.

Simulation only: no production rules, live config, broker execution, or real
challenge activation are changed.
"""

from __future__ import annotations

import random
import calendar
from collections import Counter
from datetime import UTC, date, datetime
from statistics import mean
from typing import Any


EFDE_BASELINE = {
    "pf": 3.02,
    "win_rate": 72.45,
    "trades": 151,
    "max_drawdown": 3.37,
    "average_loss": -0.70,
}
RISK_PROFILES = {
    "profile_0": 0.25,
    "profile_1": 0.50,
    "profile_2": 0.80,
    "profile_3": 1.00,
    "profile_4": 1.20,
}
PHASES = {
    "phase_1": {"target": 10.0, "max_days": 30},
    "phase_2": {"target": 5.0, "max_days": 30},
}
CHALLENGE_RULES = {
    "daily_loss": 5.0,
    "max_loss": 10.0,
    "soft_stop": 2.0,
    "hard_stop": 3.0,
    "trades_per_day": 3,
}


class ChallengeModeEngine:
    """Run deterministic Monte Carlo challenge simulation."""

    def __init__(self, *, runs: int = 500, seed: int = 91312) -> None:
        self.runs = int(runs)
        self.seed = int(seed)

    def build_report(self, *, generated_at: str | None = None) -> dict[str, Any]:
        """Return complete challenge mode report."""
        timestamp = generated_at or datetime.now(UTC).isoformat()
        rr_pool = efde_rr_pool()
        profiles = {
            profile_id: simulate_profile(
                risk_percent=risk,
                runs=self.runs,
                seed=self.seed + index * 1000,
                rr_pool=rr_pool,
            )
            for index, (profile_id, risk) in enumerate(RISK_PROFILES.items())
        }
        comparison = profile_comparison(profiles)
        monthly = monthly_window_analysis()
        rolling = rolling_2month_window_analysis(monthly["windows"])
        best = comparison["best_profile"]
        return {
            "generated_at": timestamp,
            "mode": "SIMULATION_ONLY_CHALLENGE_MODE_EFDE_ENHANCED",
            "baseline": dict(EFDE_BASELINE),
            "target_model": {
                "account_size": 10000,
                "phase_1": {"target": 10.0, "daily_loss": 5.0, "max_loss": 10.0},
                "phase_2": {"target": 5.0, "daily_loss": 5.0, "max_loss": 10.0},
            },
            "challenge_governor": dict(CHALLENGE_RULES),
            "runs_per_profile": self.runs,
            "profiles": profiles,
            "profile_comparison": comparison,
            "monthly_windows": monthly,
            "rolling_2month_windows": rolling,
            "best_profile": best,
            "challenge_verdict": "PASSABLE" if profiles[best]["combined_pass_probability"] >= 60.0 else "NOT_PASSABLE",
            "production_baseline_preserved": True,
            "production_rules_modified": False,
            "live_config_changed": False,
            "real_challenge_activation": False,
            "broker_execution": False,
            "autonomous_execution": False,
        }


def efde_rr_pool() -> list[float]:
    """Return EFDE-enhanced synthetic RR sample."""
    wins = 109
    losses = 42
    win_rr = 0.82
    loss_rr = -0.70
    pool = [win_rr for _ in range(wins)]
    pool.extend(loss_rr for _ in range(losses))
    return pool


def simulate_profile(*, risk_percent: float, runs: int, seed: int, rr_pool: list[float]) -> dict[str, Any]:
    """Run both challenge phases for one risk profile."""
    phase_1_results = []
    phase_2_results = []
    combined_passes = 0
    failure_modes: Counter[str] = Counter()
    for run_index in range(runs):
        rng = random.Random(seed + run_index)
        phase_1 = simulate_phase(
            phase_name="phase_1",
            risk_percent=risk_percent,
            rr_pool=rr_pool,
            rng=rng,
        )
        phase_2 = simulate_phase(
            phase_name="phase_2",
            risk_percent=risk_percent,
            rr_pool=rr_pool,
            rng=rng,
        )
        phase_1_results.append(phase_1)
        phase_2_results.append(phase_2)
        if phase_1["passed"] and phase_2["passed"]:
            combined_passes += 1
        else:
            failure_modes[combined_failure_mode(phase_1, phase_2)] += 1
    all_results = [*phase_1_results, *phase_2_results]
    return {
        "risk_percent": risk_percent,
        "runs": runs,
        "phase_1_pass_probability": pct(len([item for item in phase_1_results if item["passed"]]), runs),
        "phase_2_pass_probability": pct(len([item for item in phase_2_results if item["passed"]]), runs),
        "combined_pass_probability": pct(combined_passes, runs),
        "average_days_to_pass": round(mean([item["days"] for item in all_results if item["passed"]]), 2) if any(item["passed"] for item in all_results) else 0.0,
        "average_max_dd": round(mean([item["max_drawdown"] for item in all_results]), 2),
        "worst_dd": round(max(item["max_drawdown"] for item in all_results), 2),
        "failure_mode_distribution": {key: pct(value, runs) for key, value in sorted(failure_modes.items())},
        "decision": profile_decision(combined_passes, runs),
        "sample_phase_1": phase_1_results[0],
        "sample_phase_2": phase_2_results[0],
    }


def simulate_phase(*, phase_name: str, risk_percent: float, rr_pool: list[float], rng: random.Random) -> dict[str, Any]:
    """Simulate one phase with challenge governor."""
    phase = PHASES[phase_name]
    target = float(phase["target"])
    max_days = int(phase["max_days"])
    balance = 100.0
    peak = balance
    initial = balance
    max_drawdown = 0.0
    consecutive_losses = 0
    trade_count = 0
    balance_path = [round(balance, 3)]

    for day in range(1, max_days + 1):
        day_start = balance
        daily_low = balance
        day_stopped = False
        for _ in range(int(CHALLENGE_RULES["trades_per_day"])):
            active_risk = governed_risk(
                base_risk=risk_percent,
                consecutive_losses=consecutive_losses,
                phase_profit=balance - initial,
                target=target,
            )
            rr = rng.choice(rr_pool)
            balance += active_risk * rr
            trade_count += 1
            peak = max(peak, balance)
            daily_low = min(daily_low, balance)
            max_drawdown = max(max_drawdown, drawdown_pct(peak, balance), drawdown_pct(initial, balance))
            if rr < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0
            daily_loss = max(0.0, day_start - daily_low)
            if daily_loss >= CHALLENGE_RULES["daily_loss"]:
                return phase_result(False, "daily_loss_breach", day, balance, initial, max_drawdown, trade_count, balance_path)
            if max_drawdown >= CHALLENGE_RULES["max_loss"]:
                return phase_result(False, "total_drawdown_breach", day, balance, initial, max_drawdown, trade_count, balance_path)
            if daily_loss >= CHALLENGE_RULES["hard_stop"]:
                day_stopped = True
                break
            if daily_loss >= CHALLENGE_RULES["soft_stop"]:
                day_stopped = True
                break
            if balance - initial >= target:
                balance_path.append(round(balance, 3))
                return phase_result(True, "target_hit", day, balance, initial, max_drawdown, trade_count, balance_path)
            balance_path.append(round(balance, 3))
        if day_stopped:
            consecutive_losses = max(consecutive_losses, 2)
    if balance > initial and (balance - initial) >= target * 0.5:
        mode = "stagnation"
    else:
        mode = "timeout"
    return phase_result(False, mode, max_days, balance, initial, max_drawdown, trade_count, balance_path)


def governed_risk(*, base_risk: float, consecutive_losses: int, phase_profit: float, target: float) -> float:
    """Return risk after challenge governor adjustments."""
    risk = float(base_risk)
    if consecutive_losses >= 2:
        risk *= 0.5
    if phase_profit >= min(5.0, target * 0.6):
        risk *= 0.7
    return round(risk, 4)


def phase_result(
    passed: bool,
    failure_mode: str,
    days: int,
    balance: float,
    initial: float,
    max_drawdown: float,
    trade_count: int,
    balance_path: list[float],
) -> dict[str, Any]:
    """Return normalized phase result."""
    return {
        "passed": passed,
        "failure_mode": failure_mode,
        "days": int(days),
        "return_percent": round(balance - initial, 2),
        "max_drawdown": round(max_drawdown, 2),
        "trades": int(trade_count),
        "balance_path": balance_path[:20],
    }


def drawdown_pct(reference: float, balance: float) -> float:
    """Return positive drawdown percentage-like points from reference."""
    return max(0.0, reference - balance)


def combined_failure_mode(phase_1: dict[str, Any], phase_2: dict[str, Any]) -> str:
    """Return failure mode for combined challenge."""
    if not phase_1["passed"]:
        return str(phase_1["failure_mode"])
    if not phase_2["passed"]:
        return str(phase_2["failure_mode"])
    return "none"


def profile_decision(combined_passes: int, runs: int) -> str:
    """Return profile decision by sprint criteria."""
    combined = pct(combined_passes, runs)
    if combined >= 75.0:
        return "EXCELLENT"
    if combined >= 60.0:
        return "GOOD"
    return "REJECTED"


def profile_comparison(profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return ranked profile comparison."""
    ranked = sorted(
        profiles.items(),
        key=lambda item: (
            -float(item[1]["combined_pass_probability"]),
            float(item[1]["average_max_dd"]),
            float(item[1]["worst_dd"]),
            str(item[0]),
        ),
    )
    rows = []
    for rank, (profile_id, result) in enumerate(ranked, start=1):
        rows.append(
            {
                "rank": rank,
                "profile_id": profile_id,
                "risk_percent": result["risk_percent"],
                "combined_pass_probability": result["combined_pass_probability"],
                "average_max_dd": result["average_max_dd"],
                "worst_dd": result["worst_dd"],
                "decision": result["decision"],
            }
        )
    best = rows[0]["profile_id"]
    return {
        "ranking": rows,
        "best_profile": best,
        "why": (
            f"{best} has the strongest combined pass probability "
            f"with controlled average drawdown and no production changes."
        ),
    }


def pct(count: int, total: int) -> float:
    """Return percentage."""
    return round((count / max(total, 1)) * 100, 2)


def monthly_window_analysis(*, today: date | None = None) -> dict[str, Any]:
    """Return challenge-mode simulation over monthly calendar windows."""
    today = today or datetime.now(UTC).date()
    months = calendar_months(date(today.year - 1, 1, 1), today)
    windows = []
    for month_index, (start, end) in enumerate(months):
        period = monthly_period_stats(month_index, start, end)
        windows.append(window_payload(start, end, [period]))
    return {
        "coverage_start": months[0][0].isoformat() if months else "",
        "coverage_end": months[-1][1].isoformat() if months else "",
        "windows": windows,
        "summary": window_summary(windows),
    }


def rolling_2month_window_analysis(monthly_windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return rolling 2-month challenge windows."""
    windows = []
    for index in range(0, max(len(monthly_windows) - 1, 0)):
        left = monthly_windows[index]
        right = monthly_windows[index + 1]
        periods = [left["period_stats"], right["period_stats"]]
        windows.append(window_payload(date.fromisoformat(left["start_date"]), date.fromisoformat(right["end_date"]), periods))
    return {
        "windows": windows,
        "summary": window_summary(windows),
    }


def calendar_months(start: date, end: date) -> list[tuple[date, date]]:
    """Return calendar month start/end pairs."""
    current = date(start.year, start.month, 1)
    months = []
    while current <= end:
        last_day = calendar.monthrange(current.year, current.month)[1]
        month_end = date(current.year, current.month, last_day)
        if current.year == end.year and current.month == end.month:
            month_end = end
        months.append((current, month_end))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def monthly_period_stats(month_index: int, start: date, end: date) -> dict[str, Any]:
    """Return deterministic monthly trade stats."""
    # Factors intentionally cover strong, weak, and stagnant market periods.
    quality_cycle = [1.18, 0.92, 1.05, 1.24, 0.72, 1.1, 1.0, 0.84, 1.2, 0.78, 1.14, 0.96]
    quality = quality_cycle[month_index % len(quality_cycle)]
    days = (end - start).days + 1
    trades = max(4, round(days / 30 * (8 + (month_index % 5))))
    base_wr = 72.45 + (quality - 1.0) * 18
    wr = round(max(48.0, min(82.0, base_wr)), 2)
    pf = round(max(0.8, EFDE_BASELINE["pf"] * quality), 2)
    avg_win = 0.82 * quality
    avg_loss = -0.70 / max(0.75, quality)
    wins = round(trades * wr / 100)
    losses = max(0, trades - wins)
    net_r = round(wins * avg_win + losses * avg_loss, 2)
    return {
        "label": start.strftime("%b %Y"),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "calendar_days": days,
        "trades": trades,
        "pf": pf,
        "wr": wr,
        "net_r": net_r,
        "quality": round(quality, 2),
    }


def window_payload(start: date, end: date, periods: list[dict[str, Any]]) -> dict[str, Any]:
    """Return one monthly or rolling-window payload across all risk profiles."""
    period_stats = combine_periods(periods)
    profiles = {
        profile_id: window_profile_result(period_stats, risk)
        for profile_id, risk in RISK_PROFILES.items()
    }
    best_profile = best_window_profile(profiles)
    return {
        "label": window_label(start, end),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "period_stats": period_stats,
        "profiles": profiles,
        "best_profile": best_profile,
    }


def combine_periods(periods: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine monthly period stats."""
    trades = sum(int(item["trades"]) for item in periods)
    net_r = sum(float(item["net_r"]) for item in periods)
    weighted_wr = sum(float(item["wr"]) * int(item["trades"]) for item in periods) / max(trades, 1)
    weighted_pf = sum(float(item["pf"]) * int(item["trades"]) for item in periods) / max(trades, 1)
    return {
        "label": "+".join(item["label"] for item in periods),
        "start_date": periods[0]["start_date"],
        "end_date": periods[-1]["end_date"],
        "trades": trades,
        "pf": round(weighted_pf, 2),
        "wr": round(weighted_wr, 2),
        "net_r": round(net_r, 2),
        "quality": round(mean([float(item["quality"]) for item in periods]), 2),
    }


def window_profile_result(period: dict[str, Any], risk_percent: float) -> dict[str, Any]:
    """Return challenge result for one period and risk profile."""
    net_return = round(float(period["net_r"]) * risk_percent, 2)
    max_dd = round(period_drawdown(period, risk_percent), 2)
    target = 10.0
    target_reached = net_return >= target and max_dd < CHALLENGE_RULES["max_loss"]
    days_available = (date.fromisoformat(period["end_date"]) - date.fromisoformat(period["start_date"])).days + 1
    days_to_target = int(max(1, min(days_available, round(days_available * target / max(net_return, 0.01))))) if target_reached else None
    failure = ""
    if not target_reached:
        if max_dd >= CHALLENGE_RULES["daily_loss"]:
            failure = "daily_loss_breach"
        elif max_dd >= CHALLENGE_RULES["max_loss"]:
            failure = "total_drawdown_breach"
        elif net_return > target * 0.5:
            failure = "stagnation"
        else:
            failure = "timeout"
    return {
        "risk_percent": risk_percent,
        "trades": int(period["trades"]),
        "pf": period["pf"],
        "wr": period["wr"],
        "net_return_percent": net_return,
        "max_dd_percent": max_dd,
        "target_reached": target_reached,
        "days_to_target": days_to_target,
        "failure_reason": failure,
    }


def period_drawdown(period: dict[str, Any], risk_percent: float) -> float:
    """Return deterministic period drawdown."""
    quality = float(period["quality"])
    weakness = max(0.0, 1.1 - quality)
    return min(11.5, risk_percent * (1.1 + weakness * 4.5) * max(1.0, int(period["trades"]) / 8))


def best_window_profile(profiles: dict[str, dict[str, Any]]) -> str:
    """Return best risk profile for one window."""
    ranked = sorted(
        profiles.items(),
        key=lambda item: (
            not bool(item[1]["target_reached"]),
            -float(item[1]["net_return_percent"]),
            float(item[1]["max_dd_percent"]),
            str(item[0]),
        ),
    )
    return ranked[0][0]


def window_summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return best/worst windows and risk consistency."""
    scored = []
    for window in windows:
        best_profile_id = window["best_profile"]
        best = window["profiles"][best_profile_id]
        scored.append(
            {
                "label": window["label"],
                "best_profile": best_profile_id,
                "score": round(float(best["net_return_percent"]) - float(best["max_dd_percent"]) * 0.8, 2),
                "target_reached": bool(best["target_reached"]),
                "net_return_percent": best["net_return_percent"],
                "max_dd_percent": best["max_dd_percent"],
            }
        )
    best = max(scored, key=lambda item: (item["score"], item["net_return_percent"])) if scored else {}
    worst = min(scored, key=lambda item: (item["score"], item["net_return_percent"])) if scored else {}
    risk_counts = Counter(item["best_profile"] for item in scored)
    most_consistent = risk_counts.most_common(1)[0][0] if risk_counts else "none"
    avoid = [
        item["label"] for item in scored
        if item["score"] < 3.0 or (not item["target_reached"] and float(item["net_return_percent"]) < 6.0)
    ]
    lower_risk = [
        item["label"] for item in scored
        if item["best_profile"] in {"profile_0", "profile_1"} and item["target_reached"]
    ]
    return {
        "best_window": best,
        "worst_window": worst,
        "most_consistent_risk_profile": most_consistent,
        "avoid_windows": avoid,
        "lower_risk_preferred_windows": lower_risk,
    }


def window_label(start: date, end: date) -> str:
    """Return compact window label."""
    if start.year == end.year and start.month == end.month:
        return start.strftime("%b %Y")
    return f"{start.strftime('%b %Y')}-{end.strftime('%b %Y')}"
