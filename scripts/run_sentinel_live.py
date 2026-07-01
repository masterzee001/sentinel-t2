"""Run Project Sentinel as a live Advisor Mode monitor."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.market_data.mt5_connector import MT5Connector, MT5ConnectorError
from backend.monitor.live_monitor import LiveMonitor, LiveMonitorError


def main() -> int:
    """Connect to MT5, start Sentinel live monitor, and shutdown cleanly."""
    configure_terminal_logging()
    connector = MT5Connector()

    try:
        connector.connect()
        monitor = LiveMonitor(connector=connector)
        monitor.start()
        return 0
    except KeyboardInterrupt:
        print("\nSentinel live monitor stopped by user.")
        return 0
    except (MT5ConnectorError, LiveMonitorError, RuntimeError, ValueError) as exc:
        print(f"Sentinel live monitor failed: {exc}")
        return 1
    finally:
        connector.shutdown()


def configure_terminal_logging() -> None:
    """Keep the live monitor readable by hiding INFO logs."""
    logger.remove()
    logger.add(sys.stderr, level="ERROR")


if __name__ == "__main__":
    raise SystemExit(main())
