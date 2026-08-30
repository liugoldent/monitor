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
from strategy import ALL_STRATEGIES, PriceBar, net_position  # noqa: E402


def blank_positions() -> dict[str, int]:
    return {code: 0 for code in ALL_STRATEGIES}


class LiveClockTests(unittest.TestCase):
    def state(self) -> dict:
        raw = blank_positions()
        raw[ALL_STRATEGIES[0]] = 1
        raw[ALL_STRATEGIES[1]] = 1
        return {
            "live_raw_positions": raw,
            "live_active_positions": dict(raw),
            "live_boundary_keys": [],
        }

    def paths(self, directory: str):
        return (
            patch.object(monitor, "STATE_PATH", Path(directory) / "state.json"),
            patch.object(monitor, "POSITION_PATH", Path(directory) / "position.json"),
            patch.object(monitor, "CLOCK_PATH", Path(directory) / "clock.csv"),
        )

    def test_1344_flattens_and_1500_restores_latest_raw(self):
        state = self.state()
        bars = [
            PriceBar(datetime(2026, 8, 28, 13, 43), datetime(2026, 8, 28, 13, 44), 45000, 45001)
        ]
        with tempfile.TemporaryDirectory() as directory:
            p1, p2, p3 = self.paths(directory)
            with patch.dict(
                monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}, clear=False
            ), p1, p2, p3, patch.object(
                monitor, "execute_live_target", return_value="ok"
            ) as execute, patch.object(monitor, "send_discord"), patch("builtins.print"):
                applied = monitor.apply_live_clock_event(
                    state, datetime(2026, 8, 28, 13, 44, 2), bars
                )
                self.assertTrue(applied)
                self.assertEqual(net_position(state["live_active_positions"]), 0)
                execute.assert_called_with(
                    state,
                    0,
                    trigger="clock_day_flat",
                    event_key="clock:2026-08-28:day_flat",
                )

                monitor.apply_live_clock_event(
                    state, datetime(2026, 8, 28, 15, 0, 2), bars
                )
                self.assertEqual(net_position(state["live_active_positions"]), 2)
                execute.assert_called_with(
                    state,
                    2,
                    trigger="clock_night_restore",
                    event_key="clock:2026-08-28:night_restore",
                )

    def test_0459_flattens_and_0845_does_not_restore(self):
        state = self.state()
        bars = [
            PriceBar(datetime(2026, 8, 27, 15, 0), datetime(2026, 8, 27, 15, 1), 45000, 45001),
            PriceBar(datetime(2026, 8, 28, 4, 58), datetime(2026, 8, 28, 4, 59), 44900, 44901),
        ]
        with tempfile.TemporaryDirectory() as directory:
            p1, p2, p3 = self.paths(directory)
            with patch.dict(
                monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}, clear=False
            ), p1, p2, p3, patch.object(
                monitor, "execute_live_target", return_value="ok"
            ), patch.object(monitor, "send_discord"), patch("builtins.print"):
                self.assertTrue(
                    monitor.apply_live_clock_event(
                        state, datetime(2026, 8, 28, 4, 59, 2), bars
                    )
                )
                self.assertEqual(net_position(state["live_active_positions"]), 0)
                self.assertFalse(
                    monitor.apply_live_clock_event(
                        state, datetime(2026, 8, 28, 8, 45, 2), bars
                    )
                )
                self.assertEqual(net_position(state["live_active_positions"]), 0)

    def test_clock_event_is_idempotent(self):
        state = self.state()
        bars = [
            PriceBar(datetime(2026, 8, 28, 13, 43), datetime(2026, 8, 28, 13, 44), 45000, 45001)
        ]
        with tempfile.TemporaryDirectory() as directory:
            p1, p2, p3 = self.paths(directory)
            with p1, p2, p3, patch.object(
                monitor, "execute_live_target", return_value="ok"
            ), patch.object(monitor, "send_discord"), patch("builtins.print"):
                current = datetime(2026, 8, 28, 13, 44, 2)
                self.assertTrue(monitor.apply_live_clock_event(state, current, bars))
                self.assertFalse(monitor.apply_live_clock_event(state, current, bars))
            with (Path(directory) / "clock.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)

    def test_clock_event_is_not_backfilled_after_ten_minute_window(self):
        state = self.state()
        bars = [
            PriceBar(
                datetime(2026, 8, 28, 13, 43),
                datetime(2026, 8, 28, 13, 44),
                45000,
                45001,
            )
        ]
        self.assertFalse(
            monitor.apply_live_clock_event(
                state, datetime(2026, 8, 28, 13, 54), bars
            )
        )

    def test_1500_restore_uses_signal_received_during_day_break(self):
        state = {
            "live_source_row_count": 0,
            "live_raw_positions": blank_positions(),
            "live_active_positions": blank_positions(),
            "live_boundary_keys": [],
        }
        rows = [
            {
                "received_at": "2026-08-28 14:59:10",
                "strategy_code": ALL_STRATEGIES[0],
                "strategy_name": "test EF",
                "previous_position": "0",
                "new_position": "-1",
            }
        ]
        bars = [
            PriceBar(
                datetime(2026, 8, 28, 13, 43),
                datetime(2026, 8, 28, 13, 44),
                45000,
                45001,
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            p1, p2, p3 = self.paths(directory)
            with patch.dict(
                monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}, clear=False
            ), p1, p2, p3, patch.object(
                monitor, "execute_live_target", return_value="ok"
            ) as execute, patch.object(monitor, "send_discord"), patch("builtins.print"):
                # 正式監控迴圈必須維持這個順序：先收EF訊號，再跑15:00排程。
                monitor.process_live_rows(state, rows)
                self.assertEqual(state["live_raw_positions"][ALL_STRATEGIES[0]], -1)
                self.assertEqual(state["live_active_positions"][ALL_STRATEGIES[0]], 0)

                monitor.apply_live_clock_event(
                    state, datetime(2026, 8, 28, 15, 0, 2), bars
                )
                self.assertEqual(state["live_active_positions"][ALL_STRATEGIES[0]], -1)
                execute.assert_called_with(
                    state,
                    -1,
                    trigger="clock_night_restore",
                    event_key="clock:2026-08-28:night_restore",
                )


class OrderDeduplicationTests(unittest.TestCase):
    def test_duplicate_event_key_is_not_sent_twice(self):
        state = {"last_order_attempt_key": "clock:2026-08-28:day_flat"}
        with patch.dict(
            monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}, clear=False
        ), patch.object(monitor, "execute_target_position") as execute:
            text = monitor.execute_live_target(
                state,
                0,
                trigger="clock_day_flat",
                event_key="clock:2026-08-28:day_flat",
            )
        execute.assert_not_called()
        self.assertIn("防止重送", text)


if __name__ == "__main__":
    unittest.main()
