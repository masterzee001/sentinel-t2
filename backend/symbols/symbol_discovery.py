"""Broker symbol discovery helpers."""

from __future__ import annotations

from typing import Any


class SymbolDiscovery:
    """Scan MT5 symbol metadata for broker-specific aliases."""

    @staticmethod
    def discover(mt5_module: Any, patterns: list[str]) -> list[dict[str, Any]]:
        """Return matching broker symbols from an initialized MT5 module."""
        if mt5_module is None or not hasattr(mt5_module, "symbols_get"):
            raise RuntimeError("MT5 symbols_get is unavailable.")
        raw_symbols = mt5_module.symbols_get() or []
        normalized_patterns = [pattern.upper() for pattern in patterns]
        matches = []
        for raw in raw_symbols:
            data = SymbolDiscovery.to_dict(raw)
            name = str(data.get("name", data.get("symbol", ""))).strip()
            path = str(data.get("path", "") or "")
            description = str(data.get("description", "") or "")
            haystack = f"{name} {path} {description}".upper()
            if any(pattern in haystack for pattern in normalized_patterns):
                matches.append(
                    {
                        "symbol": name,
                        "path": path,
                        "description": description,
                        "visible": bool(data.get("visible", False)),
                        "trade_mode": data.get("trade_mode", ""),
                    }
                )
        return sorted(matches, key=lambda item: item["symbol"])

    @staticmethod
    def choose_preferred(matches: list[dict[str, Any]], candidates: list[str]) -> str:
        """Return the best canonical candidate match if present."""
        by_symbol = {str(item.get("symbol", "")).upper(): str(item.get("symbol", "")) for item in matches}
        for candidate in candidates:
            value = by_symbol.get(str(candidate).upper())
            if value:
                return value
        return str(matches[0]["symbol"]) if matches else ""

    @staticmethod
    def to_dict(value: Any) -> dict[str, Any]:
        if hasattr(value, "_asdict"):
            return dict(value._asdict())
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return {"name": str(value)}
