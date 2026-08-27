from __future__ import annotations

import csv
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = RuntimeError
    requests_stub.post = lambda *args, **kwargs: None
    sys.modules["requests"] = requests_stub

import monitor_and_trade as monitor  # noqa: E402
from strategy import ALL_STRATEGIES, PriceBar  # noqa: E402


class ShadowTradeTests(unittest.TestCase):
    def test_scheduled_flatten_records_each_leg_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            records = Path(directory)
            state = {
                "shadow_positions": {code: 0 for code in ALL_STRATEGIES},
                "entry_prices": {ALL_STRATEGIES[0]: 45000},
            }
            state["shadow_positions"][ALL_STRATEGIES[0]] = 1
            boundary = PriceBar(
                datetime(2026, 8, 28, 4, 59),
                datetime(2026, 8, 28, 5, 0),
                45020,
                45030,
            )
            with (
                patch.object(monitor, "TRADE_PATH", records / "trades.csv"),
                patch.object(monitor, "DECISION_PATH", records / "decisions.csv"),
                patch.object(monitor, "POSITION_PATH", records / "position.json"),
            ):
                first = monitor.apply_flatten_bar(
                    state, boundary, persist=True, notify=False
                )
                second = monitor.apply_flatten_bar(
                    state, boundary, persist=False, notify=False
                )
            self.assertTrue(first)
            self.assertFalse(second)
            with (records / "trades.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["action"], "exiting")
            self.assertEqual(rows[0]["pnl_twd"], "200.0")
            self.assertEqual(rows[0]["trigger"], "04:59_morning_flat")


if __name__ == "__main__":
    unittest.main()
