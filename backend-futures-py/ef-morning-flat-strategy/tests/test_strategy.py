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
    next_minute_open,
    parse_signal_row,
)


class PriceAlignmentTests(unittest.TestCase):
    def test_signal_0845_executes_at_0846_open(self):
        bars = [
            PriceBar(
                bar_time=datetime(2026, 8, 27, 8, 45),
                record_time=datetime(2026, 8, 27, 8, 46),
                open=46157,
                close=46151,
            ),
            PriceBar(
                bar_time=datetime(2026, 8, 27, 8, 46),
                record_time=datetime(2026, 8, 27, 8, 47),
                open=46150,
                close=46162,
            ),
        ]
        result = next_minute_open(bars, datetime(2026, 8, 27, 8, 45, 4))
        self.assertIsNotNone(result)
        self.assertEqual(result.bar_time, datetime(2026, 8, 27, 8, 46))
        self.assertEqual(result.open, 46150)

    def test_received_time_is_used_instead_of_embedded_message_time(self):
        event = parse_signal_row(
            {
                "message_time": "2026-08-27 08:45:03",
                "received_at": "2026-08-27 08:46:22",
                "strategy_code": ALL_STRATEGIES[0],
                "previous_position": "0",
                "new_position": "1",
            },
            1,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.timestamp, datetime(2026, 8, 27, 8, 46, 22))


class MorningFlatRuleTests(unittest.TestCase):
    def setUp(self):
        self.code = ALL_STRATEGIES[0]

    def event(self, timestamp: datetime, previous: int, new: int) -> SignalEvent:
        return SignalEvent(1, timestamp, self.code, previous, new, "test")

    def bar(self, timestamp: datetime, price: float = 45000) -> PriceBar:
        return PriceBar(timestamp, timestamp, price, price)

    def test_pre_open_signal_does_not_restore_position(self):
        positions = {code: 0 for code in ALL_STRATEGIES}
        decision = apply_signal(
            positions,
            self.event(datetime(2026, 8, 27, 8, 44, 50), 0, 1),
            self.bar(datetime(2026, 8, 27, 8, 45)),
        )
        self.assertEqual(decision.shadow_position, 0)
        self.assertEqual(decision.net_position, 0)

    def test_new_signal_after_0845_opens_position(self):
        positions = {code: 0 for code in ALL_STRATEGIES}
        decision = apply_signal(
            positions,
            self.event(datetime(2026, 8, 27, 8, 45, 4), 0, 1),
            self.bar(datetime(2026, 8, 27, 8, 46)),
        )
        self.assertEqual(decision.shadow_position, 1)
        self.assertEqual(decision.net_position, 1)

    def test_exit_after_flat_does_not_reopen_old_position(self):
        positions = {code: 0 for code in ALL_STRATEGIES}
        decision = apply_signal(
            positions,
            self.event(datetime(2026, 8, 27, 9, 0), 1, 0),
            self.bar(datetime(2026, 8, 27, 9, 1)),
        )
        self.assertEqual(decision.shadow_position, 0)

    def test_reversal_after_flat_is_a_new_signal(self):
        positions = {code: 0 for code in ALL_STRATEGIES}
        decision = apply_signal(
            positions,
            self.event(datetime(2026, 8, 27, 9, 0), 1, -1),
            self.bar(datetime(2026, 8, 27, 9, 1)),
        )
        self.assertEqual(decision.shadow_position, -1)


if __name__ == "__main__":
    unittest.main()
