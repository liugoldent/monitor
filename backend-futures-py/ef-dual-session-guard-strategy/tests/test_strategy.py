from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy import (  # noqa: E402
    ALL_STRATEGIES,
    PriceBar,
    SignalEvent,
    apply_signal,
    flatten_positions,
    net_position,
    restore_latest_positions,
    session_boundaries,
    trading_phase,
)


def positions() -> dict[str, int]:
    return {code: 0 for code in ALL_STRATEGIES}


class PhaseTests(unittest.TestCase):
    def test_expected_trading_phases(self):
        self.assertEqual(trading_phase(datetime(2026, 8, 28, 4, 59)), "morning_block")
        self.assertEqual(trading_phase(datetime(2026, 8, 28, 8, 45)), "day_active")
        self.assertEqual(trading_phase(datetime(2026, 8, 28, 13, 44)), "day_break")
        self.assertEqual(trading_phase(datetime(2026, 8, 28, 15, 0)), "night_active")

    def test_morning_signal_updates_raw_but_does_not_restore(self):
        raw = positions()
        active = positions()
        event = SignalEvent(1, datetime(2026, 8, 28, 8, 44, 50), ALL_STRATEGIES[0], 0, 1)
        bar = PriceBar(
            datetime(2026, 8, 28, 8, 45),
            datetime(2026, 8, 28, 8, 46),
            45000,
            45001,
        )
        decision = apply_signal(raw, active, event, bar)
        self.assertEqual(raw[ALL_STRATEGIES[0]], 1)
        self.assertEqual(active[ALL_STRATEGIES[0]], 0)
        self.assertEqual(decision.phase, "day_active")

    def test_new_day_signal_after_open_can_enter(self):
        raw = positions()
        active = positions()
        event = SignalEvent(1, datetime(2026, 8, 28, 8, 45, 1), ALL_STRATEGIES[0], 0, 1)
        bar = PriceBar(
            datetime(2026, 8, 28, 8, 46),
            datetime(2026, 8, 28, 8, 47),
            45000,
            45001,
        )
        decision = apply_signal(raw, active, event, bar)
        self.assertEqual(decision.target_position, 1)
        self.assertEqual(decision.net_position, 1)

    def test_break_signal_updates_latest_raw_for_1500_restore(self):
        raw = positions()
        active = positions()
        event = SignalEvent(1, datetime(2026, 8, 28, 14, 59, 10), ALL_STRATEGIES[0], 0, -1)
        bar = PriceBar(
            datetime(2026, 8, 28, 15, 0),
            datetime(2026, 8, 28, 15, 1),
            45000,
            44990,
        )
        apply_signal(raw, active, event, bar)
        self.assertEqual(active[ALL_STRATEGIES[0]], 0)
        restored, _, target_net = restore_latest_positions(raw, active)
        self.assertEqual(restored[ALL_STRATEGIES[0]], -1)
        self.assertEqual(target_net, -1)


class BoundaryTests(unittest.TestCase):
    def test_generates_day_flat_night_restore_and_morning_flat(self):
        bars = [
            PriceBar(datetime(2026, 8, 27, 13, 44), datetime(2026, 8, 27, 13, 45), 46000, 46001),
            PriceBar(datetime(2026, 8, 27, 15, 0), datetime(2026, 8, 27, 15, 1), 46010, 46011),
            PriceBar(datetime(2026, 8, 28, 4, 59), datetime(2026, 8, 28, 5, 0), 45900, 45901),
        ]
        boundaries = session_boundaries(bars)
        self.assertEqual(
            [(item.kind, item.timestamp) for item in boundaries],
            [
                ("day_flat", datetime(2026, 8, 27, 13, 44)),
                ("night_restore", datetime(2026, 8, 27, 15, 0)),
                ("morning_flat", datetime(2026, 8, 28, 4, 59)),
            ],
        )

    def test_missing_1500_bar_uses_first_available_night_bar(self):
        bars = [
            PriceBar(datetime(2026, 8, 27, 13, 44), datetime(2026, 8, 27, 13, 45), 46000, 46001),
            PriceBar(datetime(2026, 8, 27, 15, 2), datetime(2026, 8, 27, 15, 3), 46020, 46021),
        ]
        restore = next(item for item in session_boundaries(bars) if item.kind == "night_restore")
        self.assertEqual(restore.timestamp, datetime(2026, 8, 27, 15, 2))
        self.assertEqual(restore.price, 46020)

    def test_flatten_and_restore_keep_raw_state_separate(self):
        raw = positions()
        raw[ALL_STRATEGIES[0]] = 1
        raw[ALL_STRATEGIES[1]] = -1
        active = dict(raw)
        flat, previous, target = flatten_positions(active)
        self.assertEqual(previous, 0)
        self.assertEqual(target, 0)
        self.assertEqual(net_position(flat), 0)
        self.assertEqual(raw[ALL_STRATEGIES[0]], 1)
        restored, _, _ = restore_latest_positions(raw, flat)
        self.assertEqual(restored, raw)


if __name__ == "__main__":
    unittest.main()
