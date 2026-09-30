import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import decide, exit_on_new_short, should_lock_long, should_lock_short

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

    def test_tracked_zero_to_short_exits_long_then_original_entry_can_reenter(self):
        e = ("e1", "e2", "e3", "e4")
        positions = {"e1": 1, "e2": 1, "e3": 1, "e4": -1,
                     "f1": 1, "f2": 1}
        original = decide(positions, e, F, 1, False)
        self.assertEqual(original.target, 1)
        exited = exit_on_new_short(original, 1, 0, -1)
        self.assertEqual(exited.target, 0)
        self.assertIn("0→-1", exited.reason)
        self.assertEqual(decide(positions, e, F, exited.target, False).target, 1)

    def test_only_tracked_zero_to_short_adds_an_exit(self):
        positions = {"e1": 1, "f1": 1}
        held = decide(positions, E, F, 1, False)
        self.assertEqual(held.target, 1)
        for current, previous_leg, new_leg in ((1, 1, 0), (1, 1, -1),
                                                (1, 0, 1), (-1, 0, -1)):
            self.assertEqual(exit_on_new_short(held, current, previous_leg, new_leg), held)
        flat = decide({"e1": 0, "f1": 0}, E, F, 1, False)
        self.assertEqual(flat.target, 0)
        self.assertEqual(exit_on_new_short(flat, 1, 0, -1), flat)


if __name__ == "__main__":
    unittest.main()
