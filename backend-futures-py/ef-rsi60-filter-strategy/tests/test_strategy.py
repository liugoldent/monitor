from __future__ import annotations

import sys
import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy import (  # noqa: E402
    ALL_STRATEGIES,
    RsiSnapshot,
    SignalEvent,
    apply_event,
    direction_is_allowed,
    latest_rsi_snapshot,
    load_rsi_snapshots,
    net_position,
    parse_signal_row,
    rsi_snapshot_is_fresh,
    sixty_minute_bar_start,
    trading_session_start,
    wilder_rsi,
)


def event(previous: int, new: int, *, row: int = 1) -> SignalEvent:
    return SignalEvent(
        row_number=row,
        timestamp=datetime(2026, 8, 26, 10, 30),
        strategy_code=ALL_STRATEGIES[0],
        previous_position=previous,
        new_position=new,
    )


def snapshot(rsi: float, *, available_second: int = 0) -> RsiSnapshot:
    return RsiSnapshot(
        bar_time=datetime(2026, 8, 26, 8, 45),
        available_time=datetime(2026, 8, 26, 9, 45, available_second),
        close=45000,
        rsi=rsi,
    )


class SessionAlignmentTests(unittest.TestCase):
    def test_day_session_is_anchored_at_0845(self):
        self.assertEqual(
            trading_session_start(datetime(2026, 8, 26, 10, 15)),
            datetime(2026, 8, 26, 8, 45),
        )
        self.assertEqual(
            sixty_minute_bar_start(datetime(2026, 8, 26, 9, 44)),
            datetime(2026, 8, 26, 8, 45),
        )
        self.assertEqual(
            sixty_minute_bar_start(datetime(2026, 8, 26, 9, 45)),
            datetime(2026, 8, 26, 9, 45),
        )

    def test_night_session_is_anchored_at_1500_across_midnight(self):
        self.assertEqual(
            sixty_minute_bar_start(datetime(2026, 8, 26, 16, 20)),
            datetime(2026, 8, 26, 16, 0),
        )
        self.assertEqual(
            sixty_minute_bar_start(datetime(2026, 8, 27, 0, 30)),
            datetime(2026, 8, 27, 0, 0),
        )

    def test_break_time_has_no_session(self):
        self.assertIsNone(trading_session_start(datetime(2026, 8, 26, 14, 30)))


class RsiTests(unittest.TestCase):
    def test_wilder_rsi_for_monotonic_and_flat_prices(self):
        self.assertEqual(wilder_rsi(range(15))[-1], 100.0)
        self.assertEqual(wilder_rsi(range(15, 0, -1))[-1], 0.0)
        self.assertEqual(wilder_rsi([10] * 15)[-1], 50.0)

    def test_long_and_short_use_opposite_threshold_sides(self):
        self.assertTrue(direction_is_allowed(1, 50.0))
        self.assertFalse(direction_is_allowed(1, 49.99))
        self.assertTrue(direction_is_allowed(-1, 50.0))
        self.assertFalse(direction_is_allowed(-1, 50.01))

    def test_snapshot_is_not_visible_before_actual_record_time(self):
        value = snapshot(55.0, available_second=1)
        self.assertIsNone(
            latest_rsi_snapshot([value], datetime(2026, 8, 26, 9, 45, 0))
        )
        self.assertEqual(
            latest_rsi_snapshot([value], datetime(2026, 8, 26, 9, 45, 1)), value
        )

    def test_stale_snapshot_is_rejected_mid_session(self):
        stale = RsiSnapshot(
            bar_time=datetime(2026, 8, 25, 12, 45),
            available_time=datetime(2026, 8, 25, 13, 45),
            close=45000,
            rsi=55,
        )
        self.assertFalse(
            rsi_snapshot_is_fresh(stale, datetime(2026, 8, 26, 3, 30))
        )
        self.assertIsNone(
            latest_rsi_snapshot([stale], datetime(2026, 8, 26, 3, 30))
        )

    def test_first_day_bar_accepts_previous_night_session_close(self):
        previous_night = RsiSnapshot(
            bar_time=datetime(2026, 8, 26, 4, 0),
            available_time=datetime(2026, 8, 26, 5, 0, 1),
            close=45000,
            rsi=55,
        )
        self.assertTrue(
            rsi_snapshot_is_fresh(previous_night, datetime(2026, 8, 26, 8, 50))
        )

    def test_price_file_builds_session_aligned_completed_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["TradingView Time", "Record Time", "Close"],
                )
                writer.writeheader()
                for offset in range(15):
                    day = datetime(2026, 8, 1, 8, 45) + timedelta(days=offset)
                    writer.writerow(
                        {
                            "TradingView Time": (day + timedelta(minutes=59)).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "Record Time": (day + timedelta(hours=1, seconds=1)).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "Close": 45000 + offset,
                        }
                    )
            snapshots = load_rsi_snapshots(path)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].bar_time, datetime(2026, 8, 15, 8, 45))
            self.assertEqual(
                snapshots[0].available_time, datetime(2026, 8, 15, 9, 45, 1)
            )
            self.assertEqual(snapshots[0].rsi, 100.0)

    def test_incomplete_hour_is_not_exposed_as_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["TradingView Time", "Record Time", "Close"],
                )
                writer.writeheader()
                for offset in range(15):
                    day = datetime(2026, 8, 1, 8, 45) + timedelta(days=offset)
                    writer.writerow(
                        {
                            "TradingView Time": (day + timedelta(minutes=30)).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "Record Time": (day + timedelta(minutes=31)).strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "Close": 45000 + offset,
                        }
                    )
            self.assertEqual(load_rsi_snapshots(path), [])


class FilterDecisionTests(unittest.TestCase):
    def setUp(self):
        self.positions = {code: 0 for code in ALL_STRATEGIES}

    def test_allows_aligned_long_and_updates_net_position(self):
        decision = apply_event(self.positions, event(0, 1), snapshot(55.0))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.filtered_position, 1)
        self.assertEqual(decision.net_position, 1)

    def test_blocks_countertrend_long_without_delayed_entry(self):
        blocked = apply_event(self.positions, event(0, 1), snapshot(45.0))
        unchanged = apply_event(self.positions, event(1, 1, row=2), snapshot(55.0))
        self.assertFalse(blocked.allowed)
        self.assertEqual(unchanged.filtered_position, 0)
        self.assertEqual(net_position(self.positions), 0)

    def test_exit_always_closes_an_allowed_position(self):
        apply_event(self.positions, event(0, 1), snapshot(55.0))
        decision = apply_event(self.positions, event(1, 0, row=2), None)
        self.assertIsNone(decision.allowed)
        self.assertEqual(decision.filtered_position, 0)

    def test_reverse_closes_old_direction_when_new_direction_is_blocked(self):
        apply_event(self.positions, event(0, 1), snapshot(55.0))
        decision = apply_event(self.positions, event(1, -1, row=2), snapshot(55.0))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.previous_filtered_position, 1)
        self.assertEqual(decision.filtered_position, 0)

    def test_missing_rsi_blocks_new_entry(self):
        decision = apply_event(self.positions, event(0, -1), None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.filtered_position, 0)


class ParsingTests(unittest.TestCase):
    def test_normalizes_known_strategy_alias(self):
        parsed = parse_signal_row(
            {
                "message_time": "2026-08-26 10:30:00",
                "strategy_code": "CFCWN01m",
                "previous_position": "0",
                "new_position": "1",
            },
            1,
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.strategy_code, "CFCWIN01m")

    def test_prefers_received_time_for_actionable_execution(self):
        parsed = parse_signal_row(
            {
                "message_time": "2026-08-26 10:30:00",
                "received_at": "2026-08-26 10:31:15",
                "strategy_code": "CFC07m",
                "previous_position": "0",
                "new_position": "1",
            },
            1,
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.timestamp, datetime(2026, 8, 26, 10, 31, 15))


class ExecutionPriceTests(unittest.TestCase):
    def test_signal_0845_uses_0846_open(self):
        from strategy import ExecutionBar, next_minute_open

        bars = [
            ExecutionBar(
                datetime(2026, 8, 27, 8, 45),
                datetime(2026, 8, 27, 8, 46),
                46157,
            ),
            ExecutionBar(
                datetime(2026, 8, 27, 8, 46),
                datetime(2026, 8, 27, 8, 47),
                46150,
            ),
        ]
        result = next_minute_open(bars, datetime(2026, 8, 27, 8, 45, 4))
        self.assertIsNotNone(result)
        self.assertEqual(result.bar_time, datetime(2026, 8, 27, 8, 46))
        self.assertEqual(result.open, 46150)


if __name__ == "__main__":
    unittest.main()
