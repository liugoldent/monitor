from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy import (  # noqa: E402
    ALL_STRATEGIES,
    PORTFOLIO_E,
    PORTFOLIO_F,
    build_trade_rows,
    evaluate_strategy,
    load_latest_positions,
)


def positions() -> dict[str, int]:
    return {code: 0 for code in ALL_STRATEGIES}


class EvaluateStrategyTests(unittest.TestCase):
    def test_does_not_require_h_and_enters_on_strong_bull_consensus(self):
        value = positions()
        value[PORTFOLIO_E[0]] = 1
        value[PORTFOLIO_E[1]] = 1
        value[PORTFOLIO_F[0]] = 1
        value[PORTFOLIO_F[1]] = 1
        decision = evaluate_strategy(value)
        self.assertTrue(decision.ready)
        self.assertEqual(decision.target_position, 1)
        self.assertEqual(decision.relation, "strong_bull")

    def test_enters_on_strong_bear_consensus(self):
        value = positions()
        value[PORTFOLIO_E[0]] = -1
        value[PORTFOLIO_E[1]] = -1
        value[PORTFOLIO_F[0]] = -1
        value[PORTFOLIO_F[1]] = -1
        self.assertEqual(evaluate_strategy(value).target_position, -1)

    def test_weak_same_direction_consensus_stays_flat(self):
        value = positions()
        value[PORTFOLIO_E[0]] = 1
        value[PORTFOLIO_F[0]] = 1
        decision = evaluate_strategy(value)
        self.assertEqual(decision.target_position, 0)
        self.assertEqual(decision.relation, "no_strong_consensus")

    def test_conflicting_strong_groups_stay_flat(self):
        value = positions()
        for code in PORTFOLIO_E[:2]:
            value[code] = 1
        for code in PORTFOLIO_F[:2]:
            value[code] = -1
        self.assertEqual(evaluate_strategy(value).target_position, 0)

    def test_position_unit_scales_pure_ef_target(self):
        value = positions()
        for code in PORTFOLIO_E[:2] + PORTFOLIO_F[:2]:
            value[code] = 1
        self.assertEqual(evaluate_strategy(value, position_unit=3).target_position, 3)

    def test_missing_strategy_is_not_ready(self):
        value = positions()
        value.pop(ALL_STRATEGIES[-1])
        decision = evaluate_strategy(value)
        self.assertFalse(decision.ready)
        self.assertIsNone(decision.target_position)


class TradeRecordTests(unittest.TestCase):
    def test_pnl_multiplies_quantity(self):
        rows = build_trade_rows(
            timestamp="2026-08-25 12:00:00",
            previous_position=2,
            target_position=0,
            price=45158,
            previous_entry_price=45110,
        )
        self.assertEqual(rows[0]["pnl"], 960.0)
        self.assertEqual(rows[0]["quantity"], 2)


class PositionLoadingTests(unittest.TestCase):
    def test_loads_latest_positions_and_normalizes_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["strategy_code", "new_position"],
                )
                writer.writeheader()
                writer.writerow({"strategy_code": "CFCWN01m", "new_position": "-1"})
                writer.writerow({"strategy_code": "CFCWIN01m", "new_position": "1"})
            self.assertEqual(load_latest_positions(path)["CFCWIN01m"], 1)


if __name__ == "__main__":
    unittest.main()
