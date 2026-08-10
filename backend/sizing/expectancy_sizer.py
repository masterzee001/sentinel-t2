"""Expectancy-weighted position sizing (Phase 2).

Replaces binary quality vetoes with continuous risk multipliers derived from
measured per-cell expectancy. A cell is (symbol, narrative_phase, killzone).
Tier 1 safety and structural vetoes keep full veto power upstream; this layer
only decides HOW MUCH to risk on an already-valid candidate.

Tables must be built from historical outcomes strictly BEFORE the trades they
size (walk-forward discipline). scripts/run_sizing_walkforward.py proves the
layer out-of-sample before it may touch the live path (Constitution Law 5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ExpectancySizer:
    """Size risk per candidate from measured cell expectancy."""

    # Multiplier bands over per-trade average R (net of costs).
    STRONG_AVG_RR = 0.20
    POSITIVE_AVG_RR = 0.05
    STRONG_MULTIPLIER = 1.5
    BASE_MULTIPLIER = 1.0
    MARGINAL_MULTIPLIER = 0.5
    STARVED_MULTIPLIER = 0.0
    UNPROVEN_MULTIPLIER = 0.5
    MIN_CELL_SAMPLE = 15

    def __init__(self, table: dict[str, dict[str, Any]] | None = None) -> None:
        self.table = table or {}

    # ------------------------------------------------------------------ build
    @classmethod
    def cell_key(cls, symbol: str, narrative_phase: str, killzone: str) -> str:
        return "|".join(
            [
                str(symbol or "UNKNOWN").upper().strip(),
                str(narrative_phase or "unknown").lower().strip(),
                str(killzone or "none").lower().strip(),
            ]
        )

    @classmethod
    def build_table(cls, trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Aggregate realized outcomes into per-cell expectancy rows.

        ``trades`` must carry symbol, narrative_phase, killzone, and rr
        (realized R net of costs). Only pass trades that occurred strictly
        before the period being sized.
        """
        cells: dict[str, dict[str, Any]] = {}
        for trade in trades:
            key = cls.cell_key(
                trade.get("symbol"), trade.get("narrative_phase"), trade.get("killzone")
            )
            row = cells.setdefault(
                key,
                {"trades": 0, "wins": 0, "losses": 0, "net_rr": 0.0, "gross_win_rr": 0.0, "gross_loss_rr": 0.0},
            )
            rr = float(trade.get("rr", trade.get("simulation", {}).get("rr", 0.0)) or 0.0)
            row["trades"] += 1
            row["net_rr"] = round(row["net_rr"] + rr, 4)
            if rr > 0:
                row["wins"] += 1
                row["gross_win_rr"] = round(row["gross_win_rr"] + rr, 4)
            elif rr < 0:
                row["losses"] += 1
                row["gross_loss_rr"] = round(row["gross_loss_rr"] + abs(rr), 4)
        for row in cells.values():
            row["avg_rr"] = round(row["net_rr"] / row["trades"], 4) if row["trades"] else 0.0
            row["profit_factor"] = (
                round(row["gross_win_rr"] / row["gross_loss_rr"], 2) if row["gross_loss_rr"] else row["gross_win_rr"]
            )
        return cells

    @classmethod
    def from_trades(cls, trades: list[dict[str, Any]]) -> "ExpectancySizer":
        return cls(cls.build_table(trades))

    # ------------------------------------------------------------------ apply
    def multiplier_for(self, symbol: str, narrative_phase: str, killzone: str) -> dict[str, Any]:
        """Return the risk multiplier and rationale for one candidate cell."""
        key = self.cell_key(symbol, narrative_phase, killzone)
        row = self.table.get(key)
        if not row or int(row.get("trades", 0)) < self.MIN_CELL_SAMPLE:
            return {
                "cell": key,
                "multiplier": self.UNPROVEN_MULTIPLIER,
                "classification": "UNPROVEN",
                "sample": int(row.get("trades", 0)) if row else 0,
                "avg_rr": float(row.get("avg_rr", 0.0)) if row else 0.0,
            }
        avg_rr = float(row.get("avg_rr", 0.0))
        if avg_rr >= self.STRONG_AVG_RR:
            multiplier, label = self.STRONG_MULTIPLIER, "STRONG"
        elif avg_rr >= self.POSITIVE_AVG_RR:
            multiplier, label = self.BASE_MULTIPLIER, "POSITIVE"
        elif avg_rr > 0.0:
            multiplier, label = self.MARGINAL_MULTIPLIER, "MARGINAL"
        else:
            multiplier, label = self.STARVED_MULTIPLIER, "STARVED"
        return {
            "cell": key,
            "multiplier": multiplier,
            "classification": label,
            "sample": int(row.get("trades", 0)),
            "avg_rr": avg_rr,
        }

    def size_risk(
        self,
        *,
        symbol: str,
        narrative_phase: str,
        killzone: str,
        base_risk_percent: float,
    ) -> dict[str, Any]:
        """Return the sized risk decision for one candidate."""
        sizing = self.multiplier_for(symbol, narrative_phase, killzone)
        risk_percent = round(float(base_risk_percent) * float(sizing["multiplier"]), 4)
        return {
            **sizing,
            "base_risk_percent": float(base_risk_percent),
            "risk_percent": risk_percent,
            "trade_allowed": sizing["multiplier"] > 0.0,
        }

    # -------------------------------------------------------------- persist
    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"cells": self.table}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ExpectancySizer":
        target = Path(path)
        if not target.exists():
            return cls({})
        payload = json.loads(target.read_text(encoding="utf-8"))
        return cls(dict(payload.get("cells", {})))
