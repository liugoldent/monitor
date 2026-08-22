from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy import (  # noqa: E402
    ALL_STRATEGIES,
    MAX_POSITION_UNIT,
    PORTFOLIO_E,
    PORTFOLIO_F,
    build_h_trade_rows,
    evaluate_strategy,
    load_latest_ef_positions,
    load_latest_h_position,
    load_latest_recorded_close,
    parse_h_signal,
    parse_six_strategy_signal,
    position_event_action,
    scale_target_position,
    scaled_relation_reason,
    simulated_order_action,
    unchanged_target_notification_text,
    validate_position_unit,
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

    def test_unchanged_target_notification_contains_decision_and_no_order_result(self):
        message = unchanged_target_notification_text(
            timestamp="2026-08-20 21:00:00",
            trigger_reason="CFC07m -1->1、CFCTX16m 0->1",
            decision_summary=(
                "H=空1口，E淨部位=0，F淨部位=1，EF共識=空手，"
                "永豐目標=空1口；E、F沒有一致共識，永豐只跟H持有1口"
            ),
            target_position=-1,
        )
        self.assertIn(
            "判斷：H=空1口，E淨部位=0，F淨部位=1，EF共識=空手，"
            "永豐目標=空1口；E、F沒有一致共識，永豐只跟H持有1口",
            message,
        )
        self.assertIn(
            "結果：最終倉位檔未改變，維持 空1口，不送Discord模擬單",
            message,
        )


class PositionUnitTests(unittest.TestCase):
    def test_u_one_preserves_current_zero_one_two_rule(self):
        self.assertEqual(scale_target_position(-2, 1), -2)
        self.assertEqual(scale_target_position(-1, 1), -1)
        self.assertEqual(scale_target_position(0, 1), 0)

    def test_u_scales_follow_h_and_consensus_targets(self):
        self.assertEqual(scale_target_position(-1, 3), -3)
        self.assertEqual(scale_target_position(2, 3), 6)

    def test_u_reason_displays_scaled_quantity(self):
        self.assertEqual(
            scaled_relation_reason("neutral", 3),
            "E、F沒有一致共識，永豐只跟H持有3口（U=3）",
        )
        self.assertEqual(
            scaled_relation_reason("same", 3),
            "E、F形成一致共識且與H同向，永豐持有6口（2U，U=3）",
        )

    def test_u_rejects_zero_negative_decimal_boolean_and_too_large(self):
        for value in (0, -1, 1.5, True, MAX_POSITION_UNIT + 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_position_unit(value)


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


class HTradeRecordTests(unittest.TestCase):
    def test_market_price_never_reads_a_future_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one_minute.csv"
            path.write_text(
                "Record Time,Close\n"
                "2026-08-20 20:00:00,100\n"
                "2026-08-20 20:02:00,999\n",
                encoding="utf-8",
            )
            cutoff = datetime(
                2026,
                8,
                20,
                20,
                1,
                tzinfo=ZoneInfo("Asia/Taipei"),
            )
            self.assertEqual(load_latest_recorded_close(path, cutoff), 100.0)

    def test_add_position_closes_old_segment_and_opens_new_quantity(self):
        rows = build_h_trade_rows(
            timestamp="2026-08-20 21:00:00",
            previous_position=1,
            target_position=2,
            price=110,
            previous_entry_price=100,
        )
        self.assertEqual(
            rows,
            [
                {
                    "timestamp": "2026-08-20 21:00:00",
                    "action": "exiting",
                    "side": "bull",
                    "price": 110.0,
                    "pnl": 100.0,
                    "quantity": 1,
                },
                {
                    "timestamp": "2026-08-20 21:00:00",
                    "action": "enter",
                    "side": "bull",
                    "price": 110.0,
                    "pnl": "",
                    "quantity": 2,
                },
            ],
        )

    def test_short_exit_pnl_uses_short_direction(self):
        rows = build_h_trade_rows(
            timestamp="2026-08-20 21:00:00",
            previous_position=-1,
            target_position=0,
            price=90,
            previous_entry_price=100,
        )
        self.assertEqual(rows[0]["pnl"], 100.0)
        self.assertEqual(rows[0]["quantity"], 1)
        self.assertEqual(rows[0]["side"], "bear")

    def test_scaled_position_quantity_is_recorded(self):
        rows = build_h_trade_rows(
            timestamp="2026-08-20 21:00:00",
            previous_position=2,
            target_position=6,
            price=110,
            previous_entry_price=100,
        )
        self.assertEqual(rows[0]["quantity"], 2)
        self.assertEqual(rows[1]["quantity"], 6)

    def test_unknown_initial_entry_does_not_invent_pnl(self):
        rows = build_h_trade_rows(
            timestamp="2026-08-20 21:00:00",
            previous_position=-1,
            target_position=1,
            price=44482,
            previous_entry_price=None,
        )
        self.assertEqual(rows[0]["action"], "exiting")
        self.assertEqual(rows[0]["pnl"], "")
        self.assertEqual(rows[1]["action"], "enter")
        self.assertEqual(rows[1]["side"], "bull")


if __name__ == "__main__":
    unittest.main()
