from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
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
    def test_new_webhook_variable_has_priority(self):
        with patch.dict(
            monitor.os.environ,
            {
                "DISCORD_EF_RSIFILTER_WEBHOOK_URL": "https://new.example",
                "DISCORD_EF_RSI60_WEBHOOK_URL": "https://old.example",
            },
            clear=True,
        ):
            self.assertEqual(monitor.webhook_url(), "https://new.example")

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

    def test_decision_message_describes_received_next_minute_open(self):
        source = SignalEvent(
            row_number=1,
            timestamp=datetime(2026, 8, 26, 8, 47, 22),
            strategy_code=ALL_STRATEGIES[0],
            previous_position=0,
            new_position=1,
            strategy_name="測試策略",
        )
        decision = FilterDecision(
            event=source,
            previous_filtered_position=0,
            filtered_position=1,
            previous_net_position=0,
            net_position=1,
            rsi=55.0,
            rsi_bar_time=datetime(2026, 8, 26, 4, 0),
            allowed=True,
            reason="test",
        )
        message = monitor.decision_message(
            decision,
            execution_time=datetime(2026, 8, 26, 8, 48),
            execution_price=45000,
        )
        self.assertIn("收到時間：2026-08-26 08:47:22", message)
        self.assertIn("收到訊號後下一分鐘1分K Open", message)
        self.assertIn("2026-08-26 08:48:00 @ 45000", message)


class ImmediateLiveOrderTests(unittest.TestCase):
    def decision(self, target: int = 4) -> FilterDecision:
        source = SignalEvent(
            row_number=1,
            timestamp=datetime(2026, 8, 26, 8, 47, 22),
            strategy_code=ALL_STRATEGIES[0],
            previous_position=0,
            new_position=1,
            strategy_name="測試策略",
        )
        return FilterDecision(
            event=source,
            previous_filtered_position=0,
            filtered_position=1,
            previous_net_position=target - 1,
            net_position=target,
            rsi=55.0,
            rsi_bar_time=datetime(2026, 8, 26, 4, 0),
            allowed=True,
            reason="test",
        )

    def test_first_new_signal_reconciles_full_target_immediately(self):
        state = {"live_started": False}
        result = SimpleNamespace(
            order_sent=True,
            side="buy",
            quantity=4,
            previous_position=0,
            actual_position=4,
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true"},
            clear=False,
        ), patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(
            monitor, "execute_target_position", return_value=result
        ) as execute:
            text = monitor.execute_live_for_decision(state, self.decision(4))

        execute.assert_called_once_with(4)
        self.assertTrue(state["live_started"])
        self.assertEqual(state["last_executed_target"], 4)
        self.assertIn("TMF 4口", text)

    def test_same_attempted_target_is_not_resent(self):
        state = {"live_started": True, "last_order_attempt_target": 4}
        with patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true"},
            clear=False,
        ), patch.object(monitor, "execute_target_position") as execute:
            text = monitor.execute_live_for_decision(state, self.decision(4))

        execute.assert_not_called()
        self.assertIn("不重送", text)


if __name__ == "__main__":
    unittest.main()
