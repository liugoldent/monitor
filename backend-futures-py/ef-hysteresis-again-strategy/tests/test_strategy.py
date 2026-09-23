import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import decide, should_lock_long, should_lock_short

E = ("e1", "e2")
F = ("f1", "f2")


class AgainStrategyTests(unittest.TestCase):
    def test_hot_open_is_locked_until_consensus_breaks_and_recrosses(self):
        positions = {"e1": 1, "e2": 1, "f1": 1, "f2": 1}
        self.assertTrue(should_lock_long(positions, E, F))
        hot = decide(positions, E, F, 0, True)
        self.assertEqual(hot.target, 0)
        self.assertTrue(hot.long_locked)

        positions["e2"] = 0
        cold = decide(positions, E, F, 0, True)
        self.assertEqual(cold.target, 0)
        self.assertFalse(cold.long_locked)

        positions["e2"] = 1
        recross = decide(positions, E, F, 0, cold.long_locked)
        self.assertEqual(recross.target, 1)

    def test_first_postopen_cross_is_allowed(self):
        positions = {"e1": 1, "e2": 0, "f1": 1, "f2": 1}
        self.assertFalse(should_lock_long(positions, E, F))
        positions["e2"] = 1
        self.assertEqual(decide(positions, E, F, 0, False).target, 1)

    def test_cold_open_is_locked_until_consensus_breaks_and_recrosses(self):
        positions = {"e1": -1, "e2": -1, "f1": -1, "f2": -1}
        self.assertTrue(should_lock_short(positions, E, F))
        cold = decide(positions, E, F, 0, False, True)
        self.assertEqual(cold.target, 0)
        self.assertTrue(cold.short_locked)

        positions["e2"] = 0
        warm = decide(positions, E, F, 0, False, True)
        self.assertEqual(warm.target, 0)
        self.assertFalse(warm.short_locked)

        positions["e2"] = -1
        recross = decide(positions, E, F, 0, False, warm.short_locked)
        self.assertEqual(recross.target, -1)

    def test_first_postopen_short_cross_is_allowed(self):
        positions = {"e1": -1, "e2": 0, "f1": -1, "f2": -1}
        self.assertFalse(should_lock_short(positions, E, F))
        positions["e2"] = -1
        self.assertEqual(decide(positions, E, F, 0, False, False).target, -1)

    def test_long_lock_does_not_block_short_reversal(self):
        positions = {"e1": -1, "e2": -1, "f1": -1, "f2": -1}
        self.assertEqual(decide(positions, E, F, 1, True, False).target, -1)

    def test_hold_threshold_is_one(self):
        positions = {"e1": 1, "e2": 0, "f1": 1, "f2": 0}
        self.assertEqual(decide(positions, E, F, 1, False).target, 1)
        positions["f1"] = 0
        self.assertEqual(decide(positions, E, F, 1, False).target, 0)


if __name__ == "__main__":
    unittest.main()
