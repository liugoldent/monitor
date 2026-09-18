import unittest
from datetime import datetime

from hysteresis_strategy import evaluate_hysteresis_event, hysteresis_target
from strategy import ALL_STRATEGIES, PORTFOLIO_E, PORTFOLIO_F, PriceBar, SignalEvent


class HysteresisTargetTests(unittest.TestCase):
    def positions(self, e=(), f=()):
        result = dict.fromkeys(ALL_STRATEGIES, 0)
        for code, value in zip(PORTFOLIO_E, e):
            result[code] = value
        for code, value in zip(PORTFOLIO_F, f):
            result[code] = value
        return result

    def test_requires_two_votes_in_each_group_to_enter(self):
        weak = self.positions((1,), (1,))
        strong = self.positions((1, 1), (1, 1))
        self.assertEqual(hysteresis_target(weak, 0)[0], 0)
        self.assertEqual(hysteresis_target(strong, 0)[0], 1)

    def test_holds_with_one_vote_in_each_group(self):
        positions = self.positions((1,), (1,))
        self.assertEqual(hysteresis_target(positions, 1)[0], 1)

    def test_exits_when_one_group_loses_direction(self):
        positions = self.positions((1,), ())
        self.assertEqual(hysteresis_target(positions, 1)[0], 0)

    def test_opposite_entry_consensus_reverses(self):
        positions = self.positions((-1, -1), (-1, -1))
        target, _, _, reason = hysteresis_target(positions, 1)
        self.assertEqual(target, -1)
        self.assertEqual(reason, "bear_entry_consensus")

    def test_event_holds_existing_long_at_one_vote_each(self):
        positions = self.positions((1, 1), (1,))
        event = SignalEvent(
            1, datetime(2026, 9, 18, 9), PORTFOLIO_E[1], 1, 0, "test"
        )
        bar = PriceBar(
            datetime(2026, 9, 18, 9, 1),
            datetime(2026, 9, 18, 9, 2),
            47000,
            47001,
        )
        decision = evaluate_hysteresis_event(positions, 1, event, bar)
        self.assertEqual(decision.target_position, 1)
        self.assertEqual(decision.relation, "hold_long")

    def test_event_exits_when_one_group_reaches_zero(self):
        positions = self.positions((1,), (1,))
        event = SignalEvent(
            1, datetime(2026, 9, 18, 9), PORTFOLIO_F[0], 1, 0, "test"
        )
        bar = PriceBar(
            datetime(2026, 9, 18, 9, 1),
            datetime(2026, 9, 18, 9, 2),
            47000,
            47001,
        )
        decision = evaluate_hysteresis_event(positions, 1, event, bar)
        self.assertEqual(decision.target_position, 0)
        self.assertEqual(decision.relation, "consensus_lost")

    def test_morning_event_updates_state_but_stays_flat(self):
        positions = self.positions((1,), (1, 1))
        event = SignalEvent(
            1, datetime(2026, 9, 18, 8, 44, 30), PORTFOLIO_E[1], 0, 1, "test"
        )
        bar = PriceBar(
            datetime(2026, 9, 18, 8, 45),
            datetime(2026, 9, 18, 8, 46),
            47000,
            47001,
        )
        decision = evaluate_hysteresis_event(positions, 0, event, bar)
        self.assertEqual(decision.target_position, 0)
        self.assertEqual(decision.relation, "morning_block")


if __name__ == "__main__":
    unittest.main()
