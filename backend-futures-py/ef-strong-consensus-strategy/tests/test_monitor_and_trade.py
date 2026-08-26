from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor_and_trade  # noqa: E402
from strategy import Decision  # noqa: E402


class MonitorAndTradeTests(unittest.TestCase):
    def test_webhook_uses_only_the_requested_utl_environment_name(self):
        values = {
            "DISCORD_EF_STRONG_WEBHOOK_UTL": "https://discord.example/strong",
            "DISCORD_EF_STRONG_WEBHOOK_URL": "https://discord.example/wrong-spelling",
            "DISCORD_MXF_ALERT_WEBHOOK_URL": "https://discord.example/other-strategy",
        }
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(
                monitor_and_trade.discord_webhook(),
                "https://discord.example/strong",
            )

    def test_live_mode_reconciles_second_account_and_records_confirmed_position(self):
        decision = Decision(
            ready=True,
            e_net=2,
            f_net=3,
            target_position=1,
            relation="strong_bull",
            reason="test consensus",
            missing_strategies=(),
        )
        order_result = SimpleNamespace(
            previous_position=-1,
            target_position=1,
            actual_position=1,
            side="buy",
            quantity=2,
            order_sent=True,
        )
        state = {"last_simulated_target": 0}
        with (
            patch.object(monitor_and_trade, "evaluate_records", return_value=decision),
            patch.object(monitor_and_trade, "write_position"),
            patch.object(monitor_and_trade, "append_csv"),
            patch.object(monitor_and_trade, "save_json_atomic"),
            patch.object(monitor_and_trade, "env_flag", return_value=True),
            patch.object(
                monitor_and_trade,
                "execute_target_position",
                return_value=order_result,
            ) as execute,
            patch.object(monitor_and_trade, "send_discord", return_value=True),
            patch.object(monitor_and_trade, "append_trade_transition") as record,
        ):
            monitor_and_trade.process_batch(state, [], [], 2, 1)

        execute.assert_called_once_with(1)
        record.assert_called_once()
        self.assertEqual(state["last_executed_target"], 1)
        self.assertEqual(state["last_simulated_target"], 1)

    def test_unchanged_signal_still_notifies_current_position(self):
        decision = Decision(
            ready=True,
            e_net=2,
            f_net=3,
            target_position=1,
            relation="strong_bull",
            reason="test consensus",
            missing_strategies=(),
        )
        state = {"last_simulated_target": 1}
        new_rows = [
            {
                "strategy_code": "CFCTX18m",
                "previous_position": "0",
                "new_position": "1",
            }
        ]
        with (
            patch.object(monitor_and_trade, "evaluate_records", return_value=decision),
            patch.object(monitor_and_trade, "write_position"),
            patch.object(monitor_and_trade, "append_csv"),
            patch.object(monitor_and_trade, "env_flag", return_value=False),
            patch.object(monitor_and_trade, "send_discord", return_value=True) as notify,
        ):
            monitor_and_trade.process_batch(state, [], new_rows, 2, 1)

        notify.assert_called_once()
        message = notify.call_args.args[0]
        self.assertIn("CFCTX18m 0→1", message)
        self.assertIn("目前策略部位：多1口", message)
        self.assertIn("目標未變，維持多1口，無需動作", message)

    def test_every_signal_in_same_batch_gets_its_own_status_notification(self):
        decision = Decision(
            ready=True,
            e_net=2,
            f_net=2,
            target_position=0,
            relation="no_strong_consensus",
            reason="test no action",
            missing_strategies=(),
        )
        rows = [
            {
                "strategy_code": "CFCTX18m",
                "previous_position": "1",
                "new_position": "0",
            },
            {
                "strategy_code": "CFCCPm",
                "previous_position": "1",
                "new_position": "0",
            },
        ]
        state = {"last_simulated_target": 0}
        with (
            patch.object(monitor_and_trade, "evaluate_records", return_value=decision),
            patch.object(monitor_and_trade, "write_position"),
            patch.object(monitor_and_trade, "append_csv"),
            patch.object(monitor_and_trade, "env_flag", return_value=False),
            patch.object(monitor_and_trade, "send_discord", return_value=True) as notify,
        ):
            monitor_and_trade.process_batch(state, [], rows, 2, 1)

        self.assertEqual(notify.call_count, 2)
        self.assertIn("批次：1/2", notify.call_args_list[0].args[0])
        self.assertIn("批次：2/2", notify.call_args_list[1].args[0])

    def test_not_ready_signal_still_notifies_known_current_position(self):
        decision = Decision(
            ready=False,
            e_net=0,
            f_net=0,
            target_position=None,
            relation="not_ready",
            reason="missing strategies",
            missing_strategies=("CFCTX23m",),
        )
        state = {"last_simulated_target": -1}
        new_rows = [
            {
                "strategy_code": "CFCTX17m",
                "previous_position": "0",
                "new_position": "-1",
            }
        ]
        with (
            patch.object(monitor_and_trade, "evaluate_records", return_value=decision),
            patch.object(monitor_and_trade, "write_position"),
            patch.object(monitor_and_trade, "send_discord", return_value=True) as notify,
        ):
            monitor_and_trade.process_batch(state, [], new_rows, 2, 1)

        notify.assert_called_once()
        message = notify.call_args.args[0]
        self.assertIn("目前策略部位：空1口", message)
        self.assertIn("資料尚未齊全，暫不動作", message)

    def test_failed_live_order_is_locked_and_starts_five_notice_sequence(self):
        decision = Decision(
            ready=True,
            e_net=-2,
            f_net=-2,
            target_position=-1,
            relation="strong_bear",
            reason="test consensus",
            missing_strategies=(),
        )
        state = {"last_simulated_target": 0}
        with (
            patch.object(monitor_and_trade, "evaluate_records", return_value=decision),
            patch.object(monitor_and_trade, "write_position"),
            patch.object(monitor_and_trade, "append_csv"),
            patch.object(monitor_and_trade, "save_json_atomic"),
            patch.object(monitor_and_trade, "env_flag", return_value=True),
            patch.object(
                monitor_and_trade,
                "execute_target_position",
                side_effect=RuntimeError("測試下單錯誤"),
            ) as execute,
            patch.object(monitor_and_trade, "send_discord", return_value=True) as notify,
        ):
            monitor_and_trade.process_batch(state, [], [], 2, 1)
            monitor_and_trade.process_batch(state, [], [], 2, 1)

        execute.assert_called_once_with(-1)
        self.assertEqual(state["last_order_attempt_target"], -1)
        self.assertEqual(state["active_order_failure"]["notification_count"], 1)
        self.assertIn("提醒：1/5", notify.call_args.args[0])

    def test_fifth_failure_notice_stops_future_reminders(self):
        state = {
            "active_order_failure": {
                "target_position": 1,
                "error": "broker error",
                "failed_at": "2026-08-25 14:00:00",
                "notification_count": 4,
                "next_notification_at": "2026-08-25 14:04:00",
            }
        }
        current_time = datetime(2026, 8, 25, 14, 4, tzinfo=monitor_and_trade.TZ)
        with (
            patch.object(monitor_and_trade, "send_discord", return_value=True) as notify,
            patch.object(monitor_and_trade, "save_json_atomic"),
        ):
            sent = monitor_and_trade.process_order_failure_reminder(state, current_time)
            sent_again = monitor_and_trade.process_order_failure_reminder(state, current_time)

        self.assertTrue(sent)
        self.assertFalse(sent_again)
        notify.assert_called_once()
        self.assertIn("提醒：5/5", notify.call_args.args[0])
        failure = state["active_order_failure"]
        self.assertEqual(failure["notification_count"], 5)
        self.assertIsNone(failure["next_notification_at"])


if __name__ == "__main__":
    unittest.main()
