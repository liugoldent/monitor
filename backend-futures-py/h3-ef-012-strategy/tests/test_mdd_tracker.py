from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mdd_tracker import calculate_realized_h3_mdd  # noqa: E402


class H3MddTrackerTests(unittest.TestCase):
    def test_mdd_updates_only_from_realized_exit_pnl(self):
        rows = [
            {
                "timestamp": "09:00",
                "action": "enter",
                "side": "bull",
                "price": 100,
                "pnl": "",
                "quantity": 2,
            },
            {
                "timestamp": "10:00",
                "action": "exiting",
                "side": "bull",
                "price": 200,
                "pnl": 1000,
                "quantity": 2,
            },
            {
                "timestamp": "11:00",
                "action": "enter",
                "side": "bear",
                "price": 200,
                "pnl": "",
                "quantity": 5,
            },
            {
                "timestamp": "12:00",
                "action": "exiting",
                "side": "bear",
                "price": 400,
                "pnl": -2000,
                "quantity": 5,
            },
        ]

        snapshot, history = calculate_realized_h3_mdd(rows)

        self.assertEqual(snapshot["closed_trades_count"], 2)
        self.assertEqual(snapshot["equity_points"], -100.0)
        self.assertEqual(snapshot["peak_equity_points"], 100.0)
        self.assertEqual(snapshot["current_mdd_points"], 200.0)
        self.assertEqual(snapshot["maximum_mdd_points"], 200.0)
        self.assertEqual(len(history), 2)

    def test_unknown_initial_exit_is_skipped(self):
        rows = [
            {
                "timestamp": "09:00",
                "action": "exiting",
                "side": "bear",
                "price": 100,
                "pnl": "",
                "quantity": 1,
            },
            {
                "timestamp": "10:00",
                "action": "exiting",
                "side": "bull",
                "price": 110,
                "pnl": -300,
                "quantity": 1,
            },
        ]

        snapshot, history = calculate_realized_h3_mdd(rows)

        self.assertEqual(snapshot["skipped_unknown_pnl_count"], 1)
        self.assertEqual(snapshot["equity_points"], -30.0)
        self.assertEqual(snapshot["current_mdd_points"], 30.0)
        self.assertEqual(len(history), 1)


if __name__ == "__main__":
    unittest.main()
