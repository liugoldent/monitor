from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy import (  # noqa: E402
    ALL_STRATEGIES,
    PORTFOLIO_E,
    PORTFOLIO_F,
    evaluate_strategy,
    load_latest_ef_positions,
    load_latest_h_position,
    parse_h_signal,
    parse_six_strategy_signal,
    position_event_action,
    simulated_order_action,
)


def positions_with(e_value: int = 0, f_value: int = 0) -> dict[str, int]:
    positions = {code: 0 for code in ALL_STRATEGIES}
    if e_value:
        positions[PORTFOLIO_E[0]] = e_value
    if f_value:
        positions[PORTFOLIO_F[0]] = f_value
    return positions


class EvaluateStrategyTests(unittest.TestCase):
    def test_long_h_and_same_consensus_targets_long_two(self):
        decision = evaluate_strategy(1, positions_with(e_value=1, f_value=1))
        self.assertTrue(decision.ready)
        self.assertEqual(decision.relation, "same")
        self.assertEqual(decision.target_position, 2)

    def test_long_h_and_opposite_consensus_targets_flat(self):
        decision = evaluate_strategy(1, positions_with(e_value=-1, f_value=-1))
        self.assertEqual(decision.relation, "opposite")
        self.assertEqual(decision.target_position, 0)

    def test_long_h_and_conflicting_groups_follows_h_one(self):
        decision = evaluate_strategy(1, positions_with(e_value=1, f_value=-1))
        self.assertEqual(decision.relation, "neutral")
        self.assertEqual(decision.target_position, 1)

    def test_short_h_and_same_consensus_targets_short_two(self):
        decision = evaluate_strategy(-1, positions_with(e_value=-1, f_value=-1))
        self.assertEqual(decision.target_position, -2)

    def test_short_h_and_opposite_consensus_targets_flat(self):
        decision = evaluate_strategy(-1, positions_with(e_value=1, f_value=1))
        self.assertEqual(decision.target_position, 0)

    def test_one_group_flat_means_no_consensus(self):
        decision = evaluate_strategy(-1, positions_with(e_value=-1, f_value=0))
        self.assertEqual(decision.target_position, -1)

    def test_missing_h_is_not_ready(self):
        decision = evaluate_strategy(None, positions_with())
        self.assertFalse(decision.ready)
        self.assertIsNone(decision.target_position)

    def test_missing_six_strategy_is_not_ready(self):
        positions = positions_with()
        positions.pop(ALL_STRATEGIES[-1])
        decision = evaluate_strategy(1, positions)
        self.assertFalse(decision.ready)
        self.assertIn(ALL_STRATEGIES[-1], decision.missing_strategies)


class SignalParserTests(unittest.TestCase):
    def test_parse_h_long(self):
        text = (
            "期權醫生-浩克3\n浩克3V3訊號通知\n"
            "小型台指近一訊號部位為: 多1口"
        )
        signal = parse_h_signal(text)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.position, 1)
        self.assertEqual(signal.announced_quantity, 1)

    def test_parse_h_short_with_full_width_colon(self):
        text = "浩克3V3訊號通知\n小型台指近一訊號部位為：空1口"
        signal = parse_h_signal(text)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.position, -1)

    def test_reject_other_h_message(self):
        self.assertIsNone(parse_h_signal("小型台指近一訊號部位為: 多1口"))

    def test_parse_six_signal(self):
        text = (
            "【08.20 15:00:02】\n【訊號通知】【群益】\n【6008770】\n"
            "《策略》CFCTX16m《倉位》-1.0 -> 0.0"
        )
        signal = parse_six_strategy_signal(text, required_account="6008770")
        self.assertIsNotNone(signal)
        self.assertEqual(signal.strategy_code, "CFCTX16m")
        self.assertEqual(signal.previous_position, -1)
        self.assertEqual(signal.new_position, 0)

    def test_parse_six_signal_accepts_all_six_transitions(self):
        transitions = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, -1), (-1, 1))
        for previous, new in transitions:
            with self.subTest(previous=previous, new=new):
                text = (
                    "【訊號通知】【群益】【6008770】"
                    f"《策略》CFCTX16m《倉位》{float(previous)} -> {float(new)}"
                )
                signal = parse_six_strategy_signal(text, required_account="6008770")
                self.assertIsNotNone(signal)
                self.assertEqual(signal.previous_position, previous)
                self.assertEqual(signal.new_position, new)

    def test_reject_wrong_capital_account(self):
        text = (
            "【訊號通知】【群益】【1234567】"
            "《策略》CFCTX16m《倉位》-1.0 -> 0.0"
        )
        self.assertIsNone(
            parse_six_strategy_signal(text, required_account="6008770")
        )


class SimulatedOrderActionTests(unittest.TestCase):
    def test_flat_to_long_is_buy_entry(self):
        self.assertEqual(simulated_order_action(0, 2), ("進場", "買進", 2))

    def test_long_to_flat_is_sell_close(self):
        self.assertEqual(simulated_order_action(1, 0), ("平倉", "賣出", 1))

    def test_short_to_long_is_buy_reversal(self):
        self.assertEqual(simulated_order_action(-1, 1), ("反向切換", "買進", 2))

    def test_long_one_to_long_two_is_buy_add(self):
        self.assertEqual(simulated_order_action(1, 2), ("加碼", "買進", 1))

    def test_short_two_to_short_one_is_buy_reduce(self):
        self.assertEqual(simulated_order_action(-2, -1), ("減碼", "買進", 1))


class PositionRecordTests(unittest.TestCase):
    def test_position_event_action_covers_all_six_ef_transitions(self):
        expected = {
            (0, 1): "多單進場",
            (0, -1): "空單進場",
            (1, 0): "多單平倉",
            (-1, 0): "空單平倉",
            (1, -1): "多單平倉並轉空",
            (-1, 1): "空單平倉並轉多",
        }
        for transition, action in expected.items():
            with self.subTest(transition=transition):
                self.assertEqual(position_event_action(*transition), action)

    def test_load_latest_h_position_uses_last_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "h.csv"
            path.write_text(
                "new_position\n1\n-1\n",
                encoding="utf-8",
            )
            self.assertEqual(load_latest_h_position(path), -1)

    def test_load_latest_ef_positions_rebuilds_each_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ef.csv"
            path.write_text(
                "strategy_code,new_position\n"
                "CFC07m,-1\n"
                "CFCTX17m,1\n"
                "CFCTX17m,0\n",
                encoding="utf-8",
            )
            self.assertEqual(
                load_latest_ef_positions(path),
                {"CFC07m": -1, "CFCTX17m": 0},
            )


if __name__ == "__main__":
    unittest.main()
