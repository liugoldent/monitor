import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import capture_baseline, decide

E = ("e1", "e2", "e3")
F = ("f1", "f2", "f3")


def load_monitor():
    # The monitor uses the shared EF parser under the same module name as its
    # local rule, so isolate that import from the pure-rule tests.
    local_rule = sys.modules.pop("strategy")
    try:
        import monitor_and_trade
        return monitor_and_trade
    finally:
        sys.modules["strategy"] = local_rule


class TotalBreakoutTests(unittest.TestCase):
    def test_night_close_baseline_ignores_0500_and_later_signals(self):
        monitor = load_monitor()

        e1, e2, e3, e4 = monitor.PORTFOLIO_E[:4]
        f1, f2, f3 = monitor.PORTFOLIO_F[:3]

        def row(stamp, code, old, new):
            return {"received_at": stamp, "strategy_code": code,
                    "previous_position": str(old), "new_position": str(new)}

        prior = [row("2026-09-23 04:58:00", code, 0, 1)
                 for code in (e1, e2, e3, f1, f2)]
        later = [
            row("2026-09-23 05:00:00", f3, 0, 1),
            row("2026-09-23 08:45:01", f3, 1, 0),
            row("2026-09-23 09:00:00", e4, 0, 1),
        ]
        rows = prior + later
        state = {"source_row_count": len(prior), "raw_positions":
                 monitor.positions_before(rows, datetime(2026, 9, 23, 5)),
                 "target": 0, "baseline_cycle": None,
                 "bull_baseline": 0, "bear_baseline": 0}
        with patch.object(monitor, "persist"), patch.object(monitor, "append_decision"), \
             patch("builtins.print"):
            self.assertTrue(monitor.freeze_night_close_baseline(state, rows, date(2026, 9, 23)))
            self.assertEqual(state["bull_baseline"], 5)
            self.assertEqual(state["baseline_asof"], "2026-09-23T05:00:00")
            self.assertFalse(monitor.freeze_night_close_baseline(state, rows, date(2026, 9, 23)))
            monitor.process_rows(state, rows, lambda _: None)
        self.assertEqual(state["bull_baseline"], 5)
        self.assertEqual(state["target"], 1)

    def test_old_shadow_target_is_rebuilt_with_night_close_baseline(self):
        monitor = load_monitor()
        codes = monitor.PORTFOLIO_E[:3] + monitor.PORTFOLIO_F[:3]
        rows = [{"received_at": "2026-09-23 04:58:00", "strategy_code": code,
                 "previous_position": "0", "new_position": "1"} for code in codes]
        rows.append({"received_at": "2026-09-23 09:44:59",
                     "strategy_code": monitor.PORTFOLIO_F[2],
                     "previous_position": "1", "new_position": "0"})
        state = {"source_row_count": len(rows), "target": 1,
                 "baseline_cycle": "2026-09-23", "bull_baseline": 5,
                 "bear_baseline": 0}
        with patch.object(monitor, "persist"):
            monitor.migrate_saved_target(state, rows, datetime(2026, 9, 23, 11))
        self.assertEqual(state["bull_baseline"], 6)
        self.assertEqual(state["target"], 0)
        self.assertEqual(state["target_rule_version"], 2)

    def test_exact_baseline_is_not_a_breakout(self):
        baseline = capture_baseline({"e1": 1, "e2": 1, "f1": 1, "f2": 1}, E, F)
        self.assertEqual(decide({"e1": 1, "e2": 1, "f1": 1, "f2": 1},
                                E, F, 0, baseline).target, 0)

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
