from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor_and_trade as monitor  # noqa: E402
from strategy import ALL_STRATEGIES, FilterDecision, SignalEvent  # noqa: E402


class ShadowTradeTests(unittest.TestCase):
    def test_quantity_aware_shadow_trade_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            trade_path = Path(directory) / "trades.csv"
            state: dict[str, object] = {}
            with patch.object(monitor, "TRADE_PATH", trade_path):
                monitor.append_shadow_transition(
                    state,
                    previous_target=0,
                    target=2,
                    price=45000,
                    timestamp=datetime(2026, 8, 26, 10, 0),
                )
                monitor.append_shadow_transition(
                    state,
                    previous_target=2,
                    target=0,
                    price=45010,
                    timestamp=datetime(2026, 8, 26, 11, 0),
                )
            with trade_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["action"], "enter")
            self.assertEqual(rows[0]["quantity"], "2")
            self.assertEqual(rows[1]["action"], "exiting")
            self.assertEqual(rows[1]["pnl_points"], "20.0")
            self.assertEqual(rows[1]["pnl_twd"], "200.0")
            self.assertIsNone(state["shadow_entry_price"])


class MonitorFormattingTests(unittest.TestCase):
    def test_normalized_positions_rejects_invalid_runtime_values(self):
        positions = monitor.normalized_positions(
            {ALL_STRATEGIES[0]: "1", ALL_STRATEGIES[1]: 99}
        )
        self.assertEqual(positions[ALL_STRATEGIES[0]], 1)
        self.assertEqual(positions[ALL_STRATEGIES[1]], 0)
        self.assertEqual(len(positions), len(ALL_STRATEGIES))

    def test_blocked_decision_message_is_explicitly_shadow_only(self):
        source = SignalEvent(
            row_number=1,
            timestamp=datetime(2026, 8, 26, 10, 0),
            strategy_code=ALL_STRATEGIES[0],
            previous_position=0,
            new_position=1,
            strategy_name="測試策略",
        )
        decision = FilterDecision(
            event=source,
            previous_filtered_position=0,
            filtered_position=0,
            previous_net_position=0,
            net_position=0,
            rsi=45.0,
            rsi_bar_time=datetime(2026, 8, 26, 8, 45),
            allowed=False,
            reason="test",
        )
        message = monitor.decision_message(decision)
        self.assertIn("阻擋進場", message)
        self.assertIn("不送實單", message)


if __name__ == "__main__":
    unittest.main()
