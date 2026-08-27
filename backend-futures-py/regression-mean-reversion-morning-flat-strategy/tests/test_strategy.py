from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy import (  # noqa: E402
    Config,
    MeanReversionEngine,
    PriceBar,
    RiskContext,
    regression_channel,
    state_from_dict,
    state_to_dict,
)


def bar(at: datetime, open_: float, high: float, low: float, close: float) -> PriceBar:
    return PriceBar(at, at + timedelta(minutes=1), open_, high, low, close)


class RegressionTests(unittest.TestCase):
    def test_linear_series_has_current_center_and_slope(self):
        channel = regression_channel([100, 102, 104, 106, 108], 2)
        self.assertAlmostEqual(channel.center, 108)
        self.assertAlmostEqual(channel.slope, 2)
        self.assertAlmostEqual(channel.residual_std, 0)


class TimingAndRiskTests(unittest.TestCase):
    def make_engine(self) -> MeanReversionEngine:
        config = Config(
            regression_length=60,
            channel_width=1.0,
            stop_points=10,
            max_abs_slope=10,
            abnormal_range_multiple=100,
            min_reward_risk=1.0,
            gap_points=1000,
        )
        engine = MeanReversionEngine(config)
        start = datetime(2026, 8, 3, 7, 51)
        engine.warm([bar(start + timedelta(minutes=i), 100, 101, 99, 100) for i in range(59)])
        return engine

    def test_signal_at_close_fills_only_next_minute_open(self):
        engine = self.make_engine()
        signal_bar = bar(datetime(2026, 8, 3, 8, 50), 100, 101, 79, 80)
        actions, decisions = engine.process_bar(signal_bar)
        self.assertEqual(actions, [])
        self.assertTrue(any(item.accepted for item in decisions))
        next_bar = bar(datetime(2026, 8, 3, 8, 51), 80, 85, 79, 84)
        actions, _ = engine.process_bar(next_bar)
        self.assertEqual(actions, [])
        executable_bar = bar(datetime(2026, 8, 3, 8, 52), 82, 85, 81, 84)
        actions, _ = engine.process_bar(executable_bar)
        self.assertEqual(actions[0].action, "enter")
        self.assertEqual(actions[0].price, 82)

    def test_aligned_h_ef_blocks_opposite_signal(self):
        engine = self.make_engine()
        signal_bar = bar(datetime(2026, 8, 3, 8, 50), 100, 121, 99, 120)
        _, decisions = engine.process_bar(
            signal_bar,
            RiskContext(h_position=1, ef_target=1, e_net=2, f_net=3, relation="h_ef_aligned_bull"),
        )
        self.assertFalse(decisions[-1].accepted)
        self.assertIn("H與EF", decisions[-1].reason)

    def test_stop_locks_same_direction_for_day(self):
        engine = self.make_engine()
        engine.process_bar(bar(datetime(2026, 8, 3, 8, 50), 100, 101, 79, 80))
        engine.process_bar(bar(datetime(2026, 8, 3, 8, 51), 80, 81, 79, 80))
        engine.process_bar(bar(datetime(2026, 8, 3, 8, 52), 80, 81, 79, 80))
        actions, _ = engine.process_bar(bar(datetime(2026, 8, 3, 8, 53), 80, 81, 69, 70))
        self.assertEqual(actions[0].reason, "fixed_stop")
        self.assertTrue(engine.state.long_locked)

    def test_1320_flattens_at_open(self):
        engine = self.make_engine()
        engine.process_bar(bar(datetime(2026, 8, 3, 8, 50), 100, 101, 79, 80))
        engine.process_bar(bar(datetime(2026, 8, 3, 8, 51), 80, 81, 79, 80))
        engine.process_bar(bar(datetime(2026, 8, 3, 8, 52), 80, 81, 79, 80))
        actions, _ = engine.process_bar(bar(datetime(2026, 8, 3, 13, 20), 90, 91, 89, 90))
        self.assertEqual(actions[0].reason, "13:20_force_flat")
        self.assertEqual(actions[0].price, 90)

    def test_state_round_trip_preserves_pending_datetime(self):
        engine = self.make_engine()
        engine.process_bar(bar(datetime(2026, 8, 3, 8, 50), 100, 101, 79, 80))
        restored = state_from_dict(state_to_dict(engine.state))
        self.assertEqual(restored.pending, engine.state.pending)
        self.assertEqual(restored.last_bar_time, engine.state.last_bar_time)


if __name__ == "__main__":
    unittest.main()
