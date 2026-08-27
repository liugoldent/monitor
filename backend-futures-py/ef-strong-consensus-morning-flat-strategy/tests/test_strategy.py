from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy import (  # noqa: E402
    ALL_STRATEGIES,
    PORTFOLIO_E,
    PORTFOLIO_F,
    PriceBar,
    SignalEvent,
    consensus_target,
    evaluate_event,
    next_minute_open,
    parse_signal_row,
)


def empty_positions() -> dict[str, int]:
    return {code: 0 for code in ALL_STRATEGIES}


class ConsensusTests(unittest.TestCase):
    def test_requires_two_votes_in_each_group(self):
        positions = empty_positions()
        positions[PORTFOLIO_E[0]] = 1
        positions[PORTFOLIO_F[0]] = 1
        self.assertEqual(consensus_target(positions)[0], 0)
        positions[PORTFOLIO_E[1]] = 1
        positions[PORTFOLIO_F[1]] = 1
        self.assertEqual(consensus_target(positions)[0], 1)

    def test_enters_one_short_contract_only(self):
        positions = empty_positions()
        for code in PORTFOLIO_E[:4] + PORTFOLIO_F[:5]:
            positions[code] = -1
        self.assertEqual(consensus_target(positions)[0], -1)


class TimingTests(unittest.TestCase):
    def test_uses_received_at_and_next_minute_open(self):
        event = parse_signal_row(
            {
                "received_at": "2026-08-27 08:45:04",
                "message_time": "2026-08-27 08:44:58",
                "strategy_code": PORTFOLIO_E[0],
                "previous_position": "0",
                "new_position": "1",
            },
            1,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.timestamp, datetime(2026, 8, 27, 8, 45, 4))
        bars = [
            PriceBar(datetime(2026, 8, 27, 8, 45), datetime(2026, 8, 27, 8, 46), 100, 101),
            PriceBar(datetime(2026, 8, 27, 8, 46), datetime(2026, 8, 27, 8, 47), 102, 103),
        ]
        self.assertEqual(next_minute_open(bars, event.timestamp).open, 102)

    def test_untimed_row_is_not_actionable(self):
        self.assertIsNone(
            parse_signal_row(
                {
                    "strategy_code": PORTFOLIO_E[0],
                    "previous_position": "0",
                    "new_position": "1",
                },
                1,
            )
        )


class MorningFlatTests(unittest.TestCase):
    def test_preopen_signal_does_not_reenter(self):
        positions = empty_positions()
        positions[PORTFOLIO_E[1]] = 1
        positions[PORTFOLIO_F[0]] = 1
        positions[PORTFOLIO_F[1]] = 1
        event = SignalEvent(
            1,
            datetime(2026, 8, 27, 8, 44, 50),
            PORTFOLIO_E[0],
            0,
            1,
        )
        bar = PriceBar(
            datetime(2026, 8, 27, 8, 45),
            datetime(2026, 8, 27, 8, 46),
            45000,
            45010,
        )
        decision = evaluate_event(positions, 0, event, bar)
        self.assertEqual(decision.target_position, 0)
        self.assertEqual(decision.relation, "morning_block")

    def test_new_postopen_signal_can_reenter_consensus(self):
        positions = empty_positions()
        positions[PORTFOLIO_E[1]] = 1
        positions[PORTFOLIO_F[0]] = 1
        positions[PORTFOLIO_F[1]] = 1
        event = SignalEvent(
            1,
            datetime(2026, 8, 27, 8, 45, 4),
            PORTFOLIO_E[0],
            0,
            1,
        )
        bar = PriceBar(
            datetime(2026, 8, 27, 8, 46),
            datetime(2026, 8, 27, 8, 47),
            45000,
            45010,
        )
        decision = evaluate_event(positions, 0, event, bar)
        self.assertEqual(decision.target_position, 1)


if __name__ == "__main__":
    unittest.main()
