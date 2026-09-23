import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import capture_baseline, decide

E = ("e1", "e2", "e3")
F = ("f1", "f2", "f3")


class TotalBreakoutTests(unittest.TestCase):
    def test_total_must_exceed_preopen_baseline(self):
        preopen = {"e1": 1, "e2": 1, "e3": 0, "f1": 1, "f2": 1, "f3": 1}
        baseline = capture_baseline(preopen, E, F)
        self.assertEqual(baseline.bull, 5)
        weaker = {**preopen, "f3": 0}
        self.assertEqual(decide(weaker, E, F, 0, baseline).target, 0)
        equal = preopen.copy()
        self.assertEqual(decide(equal, E, F, 0, baseline).target, 0)
        stronger = {**preopen, "e3": 1}
        self.assertEqual(decide(stronger, E, F, 0, baseline).target, 1)

    def test_hold_uses_one_vote_after_entry(self):
        baseline = capture_baseline({}, E, F)
        positions = {"e1": 1, "f1": 1}
        self.assertEqual(decide(positions, E, F, 1, baseline).target, 1)
        positions["f1"] = 0
        self.assertEqual(decide(positions, E, F, 1, baseline).target, 0)

    def test_bear_rule_is_symmetric(self):
        preopen = {"e1": -1, "e2": -1, "f1": -1, "f2": -1, "f3": -1}
        baseline = capture_baseline(preopen, E, F)
        self.assertEqual(baseline.bear, 5)
        self.assertEqual(decide(preopen, E, F, 0, baseline).target, 0)
        stronger = {**preopen, "e3": -1}
        self.assertEqual(decide(stronger, E, F, 0, baseline).target, -1)

    def test_opposite_consensus_exits_without_unqualified_reversal(self):
        preopen = {"e1": 1, "e2": 1, "f1": 1, "f2": 1, "f3": 1}
        baseline = capture_baseline(preopen, E, F)
        self.assertEqual(decide(preopen, E, F, -1, baseline).target, 0)


if __name__ == "__main__":
    unittest.main()
