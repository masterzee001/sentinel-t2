"""MetaTrader 5 market data connector for Project Sentinel."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from loguru import logger

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - exercised indirectly in environments without MT5.
    mt5 = None


class MT5ConnectorError(RuntimeError):
    """Raised when MetaTrader 5 operations fail."""


class MT5Connector:
    """Thin, testable wrapper around the MetaTrader 5 Python package."""

    SUPPORTED_SYMBOLS = frozenset({"XAUUSD", "US30", "EURUSD", "GBPUSD", "BTCUSD", "NAS100"})

    def __init__(
        self,
        env_path: str | Path | None = None,
        mt5_module: Any | None = None,
        supported_symbols: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.env_path = Path(env_path) if env_path else None
        self.mt5 = mt5_module if mt5_module is not None else mt5
        self.supported_symbols = supported_symbols or self.SUPPORTED_SYMBOLS
        self.symbol_aliases = self._load_symbol_aliases()
        self._initialized = False

        if self.mt5 is None:
            logger.warning("MetaTrader5 package is not installed or could not be imported.")

    def connect(self) -> bool:
        """Initialize MetaTrader 5 using credentials from environment variables."""
        self._load_environment()
        self._require_mt5_package()

        credentials = self._read_credentials()
        logger.info("Initializing MetaTrader 5 connection.")

        if credentials:
            initialized = self.mt5.initialize(**credentials)
        else:
            logger.warning("MT5 credentials are incomplete; initializing terminal without login.")
            initialized = self.mt5.initialize()

        if not initialized:
            raise MT5ConnectorError(f"Failed to initialize MetaTrader 5: {self._last_error()}")

        self._initialized = True
        logger.info("MetaTrader 5 initialized successfully.")
        return True

    def shutdown(self) -> None:
        """Shutdown MetaTrader 5 cleanly."""
        if self.mt5 is None:
            return

        if self._initialized:
            logger.info("Shutting down MetaTrader 5 connection.")
            self.mt5.shutdown()
            self._initialized = False

    def is_initialized(self) -> bool:
        """Return whether the connector believes MT5 is initialized and responsive."""
        if not self._initialized or self.mt5 is None:
            return False

        try:
            return self.mt5.terminal_info() is not None
        except Exception as exc:  # pragma: no cover - defensive guard around vendor package.
            logger.error("Failed to read MT5 terminal info: {}", exc)
            return False

    def get_account_info(self) -> dict[str, Any]:
        """Return account information from MetaTrader 5."""
        self._require_initialized()

        account_info = self.mt5.account_info()
        if account_info is None:
            raise MT5ConnectorError(f"Failed to retrieve account information: {self._last_error()}")

        logger.info("Retrieved MT5 account information.")
        return self._to_dict(account_info)

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Return symbol metadata for a supported instrument."""
        self._require_initialized()
        normalized_symbol = self._normalize_symbol(symbol)
        broker_symbol = self._select_broker_symbol(normalized_symbol)

        symbol_info = self.mt5.symbol_info(broker_symbol)
        if symbol_info is None:
            raise MT5ConnectorError(
                f"Failed to retrieve symbol information for {normalized_symbol}: {self._last_error()}"
            )

        logger.info("Retrieved symbol information for {}.", normalized_symbol)
        return self._to_dict(symbol_info)

    def get_latest_tick(self, symbol: str) -> dict[str, Any]:
        """Return the latest tick for a supported instrument."""
        self._require_initialized()
        normalized_symbol = self._normalize_symbol(symbol)
        broker_symbol = self._select_broker_symbol(normalized_symbol)

        tick = self.mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            raise MT5ConnectorError(f"Failed to retrieve latest tick for {normalized_symbol}: {self._last_error()}")

        logger.info("Retrieved latest tick for {}.", normalized_symbol)
        return self._to_dict(tick)

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: str | int,
        count: int = 100,
        start_pos: int = 0,
    ) -> pd.DataFrame:
        """Return historical candles as a pandas DataFrame."""
        self._require_initialized()
        normalized_symbol = self._normalize_symbol(symbol)
        timeframe_value = self._resolve_timeframe(timeframe)
        broker_symbol = self._select_broker_symbol(normalized_symbol)

        if count <= 0:
            raise ValueError("count must be greater than zero.")
        if start_pos < 0:
            raise ValueError("start_pos cannot be negative.")

        rates = self.mt5.copy_rates_from_pos(broker_symbol, timeframe_value, start_pos, count)
        if rates is None:
            raise MT5ConnectorError(
                f"Failed to retrieve candles for {normalized_symbol}: {self._last_error()}"
            )

        dataframe = pd.DataFrame(rates)
        if dataframe.empty:
            logger.warning("No historical candles returned for {}.", normalized_symbol)
            return dataframe

        if "time" in dataframe.columns:
            dataframe["time"] = pd.to_datetime(dataframe["time"], unit="s", utc=True)

        logger.info("Retrieved {} candles for {}.", len(dataframe), normalized_symbol)
        return dataframe

    def _load_environment(self) -> None:
        if self.env_path:
            load_dotenv(self.env_path)
            return

        load_dotenv()

    def _read_credentials(self) -> dict[str, Any]:
        login = os.getenv("MT5_LOGIN")
        password = os.getenv("MT5_PASSWORD")
        server = os.getenv("MT5_SERVER")

        if not all([login, password, server]):
            return {}

        try:
            login_value = int(login)
        except ValueError as exc:
            raise MT5ConnectorError("MT5_LOGIN must be an integer account number.") from exc

        credentials: dict[str, Any] = {
            "login": login_value,
            "password": password,
            "server": server,
        }
        terminal_path = os.getenv("MT5_TERMINAL_PATH")
        if terminal_path:
            credentials["path"] = terminal_path
        return credentials

    def _require_mt5_package(self) -> None:
        if self.mt5 is None:
            raise MT5ConnectorError(
                "MetaTrader5 package is not available. Install dependencies with pip install -r requirements.txt."
            )

    def _require_initialized(self) -> None:
        if not self.is_initialized():
            raise MT5ConnectorError("MetaTrader 5 is not initialized. Call connect() before requesting data.")

    def _normalize_symbol(self, symbol: str) -> str:
        normalized_symbol = symbol.upper().strip()
        if normalized_symbol not in self.supported_symbols:
            supported = ", ".join(sorted(self.supported_symbols))
            raise ValueError(f"Unsupported symbol '{symbol}'. Supported symbols: {supported}.")

        return normalized_symbol

    def _select_broker_symbol(self, symbol: str) -> str:
        """Select canonical symbol or first configured broker alias."""
        last_error = None
        for candidate in self._candidate_symbols(symbol):
            selected = self.mt5.symbol_select(candidate, True)
            if selected:
                return candidate
            last_error = self._last_error()
        raise MT5ConnectorError(f"Failed to select symbol {symbol}: {last_error or self._last_error()}")

    def _candidate_symbols(self, symbol: str) -> list[str]:
        aliases = [str(value).upper().strip() for value in self.symbol_aliases.get(symbol, [])]
        candidates = [symbol, *aliases]
        deduped: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def _resolve_timeframe(self, timeframe: str | int) -> int:
        if isinstance(timeframe, int):
            return timeframe

        timeframe_name = f"TIMEFRAME_{timeframe.upper().strip()}"
        if not hasattr(self.mt5, timeframe_name):
            raise ValueError(f"Unsupported MT5 timeframe '{timeframe}'.")

        return getattr(self.mt5, timeframe_name)

    def _last_error(self) -> Any:
        if self.mt5 is None or not hasattr(self.mt5, "last_error"):
            return "unknown error"

        return self.mt5.last_error()

    @staticmethod
    def _load_symbol_aliases() -> dict[str, list[str]]:
        """Load optional broker alias candidates from symbol_registry.yaml."""
        project_root = Path(__file__).resolve().parents[2]
        path = project_root / "config" / "symbol_registry.yaml"
        if not path.exists():
            return {}
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
        aliases = config.get("aliases", {})
        if not isinstance(aliases, dict):
            return {}
        return {
            str(symbol).upper().strip(): [str(alias).upper().strip() for alias in values]
            for symbol, values in aliases.items()
            if isinstance(values, list)
        }

    @staticmethod
    def _to_dict(value: Any) -> dict[str, Any]:
        if hasattr(value, "_asdict"):
            return dict(value._asdict())
        if isinstance(value, Mapping):
            return dict(value)
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)

        raise MT5ConnectorError(f"Cannot convert MT5 response of type {type(value)!r} to dict.")
