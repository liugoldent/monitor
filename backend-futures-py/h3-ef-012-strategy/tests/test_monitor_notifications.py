from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor_and_trade  # noqa: E402
from strategy import SixStrategySignal  # noqa: E402


class CombinedPositionEfDetailsTests(unittest.TestCase):
    def test_rebuilds_all_strategy_details_with_chinese_names_and_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ef.csv"
            path.write_text(
                "received_at,strategy_code,new_position\n"
                "2026-08-24 12:00:00,CFC07m,-1\n"
                "2026-08-24 12:01:00,CFC07m,1\n"
                "2026-08-24 12:02:00,CFCTX16m,0\n",
                encoding="utf-8",
            )

            details = monitor_and_trade.load_ef_strategy_position_details(path)

        self.assertEqual(
            details["E"]["財神列車7號"],
            {
                "strategy_code": "CFC07m",
                "position": 1,
                "position_text": "多1口",
                "updated_at": "2026-08-24 12:01:00",
            },
        )
        self.assertEqual(
            details["F"]["財神列車16號"],
            {
                "strategy_code": "CFCTX16m",
                "position": 0,
                "position_text": "空手",
                "updated_at": "2026-08-24 12:02:00",
            },
        )
        self.assertEqual(
            details["E"]["財神列車17號"]["position_text"],
            "尚無資料",
        )
        self.assertEqual(len(details["E"]), 6)
        self.assertEqual(len(details["F"]), 6)


class LegacySixStrategyRecordTests(unittest.TestCase):
    def test_appends_the_old_csv_schema_from_the_new_listener(self):
        signal = SixStrategySignal(
            account="6008770",
            strategy_code="CFCWIN01m",
            raw_strategy_code="CFCWN01m",
            previous_position=1,
            new_position=-1,
        )
        raw_message = (
            "【08.25 09:24:57】\n【訊號通知】【群益】\n【6008770】\n"
            "《策略》CFCWN01m《倉位》1.0 -> -1.0"
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "six_strategy_signal_events.csv"
            with patch.object(
                monitor_and_trade,
                "LEGACY_SIX_STRATEGY_RECORD_PATH",
                path,
            ):
                monitor_and_trade.append_legacy_six_strategy_record(
                    signal,
                    raw_message,
                    received_at="2026-08-25 09:24:58",
                )
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0],
            {
                "received_at": "2026-08-25 09:24:58",
                "message_time": "2026-08-25 09:24:57",
                "account": "6008770",
                "strategy_code": "CFCWIN01m",
                "raw_strategy_code": "CFCWN01m",
                "strategy_name": "智能引擎1號",
                "previous_position": "1",
                "new_position": "-1",
                "action": "reverse",
                "side": "bear",
                "quantity": "1",
                "signal": "《策略》CFCWN01m《倉位》1.0 -> -1.0",
            },
        )

    def test_backfills_only_events_newer_than_the_last_old_csv_row(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            legacy_path = directory_path / "six_strategy_signal_events.csv"
            ef_path = directory_path / "ef_position_events.csv"
            legacy_path.write_text(
                ",".join(monitor_and_trade.LEGACY_SIX_STRATEGY_RECORD_FIELDS)
                + "\n2026-08-24 21:38:26,,,,,,,,,,,\n",
                encoding="utf-8",
            )
            ef_path.write_text(
                "received_at,raw_message\n"
                '2026-08-24 21:00:00,"old"\n'
                '2026-08-25 09:24:58,"'
                "【08.25 09:24:57】 【訊號通知】【群益】 【6008770】 "
                "《策略》CFCTX22m《倉位》1.0 -> 0.0"
                '"\n',
                encoding="utf-8",
            )

            with (
                patch.object(
                    monitor_and_trade,
                    "LEGACY_SIX_STRATEGY_RECORD_PATH",
                    legacy_path,
                ),
                patch.object(monitor_and_trade, "EF_RECORD_PATH", ef_path),
            ):
                appended = monitor_and_trade.backfill_legacy_six_strategy_records()
            with legacy_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(appended, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["strategy_code"], "CFCTX22m")
        self.assertEqual(rows[-1]["message_time"], "2026-08-25 09:24:57")


class DiscordDeliveryTests(unittest.TestCase):
    def test_waits_for_discord_message_id_before_reporting_success(self):
        response = Mock()
        response.json.return_value = {"id": "discord-message-123"}

        with patch.object(monitor_and_trade.requests, "post", return_value=response) as post:
            success = monitor_and_trade.send_discord_message("訊號測試")

        self.assertTrue(success)
        post.assert_called_once_with(
            monitor_and_trade.os.getenv(monitor_and_trade.DISCORD_WEBHOOK_ENV).strip(),
            params={"wait": "true"},
            json={"username": "NotifierBot", "content": "訊號測試"},
            timeout=15,
        )
        response.raise_for_status.assert_called_once_with()

    def test_retries_when_discord_does_not_confirm_message_creation(self):
        missing_id_response = Mock()
        missing_id_response.json.return_value = {}
        success_response = Mock()
        success_response.json.return_value = {"id": "discord-message-456"}

        with (
            patch.object(
                monitor_and_trade.requests,
                "post",
                side_effect=[missing_id_response, success_response],
            ) as post,
            patch.object(monitor_and_trade.time, "sleep") as sleep,
        ):
            success = monitor_and_trade.send_discord_message("訊號測試")

        self.assertTrue(success)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_network_error_does_not_print_webhook_secret(self):
        webhook_url = monitor_and_trade.os.getenv(
            monitor_and_trade.DISCORD_WEBHOOK_ENV
        ).strip()
        error = monitor_and_trade.requests.exceptions.ConnectionError(
            f"connection failed for {webhook_url}?wait=true"
        )

        with (
            patch.object(monitor_and_trade.requests, "post", side_effect=error),
            patch.object(monitor_and_trade.time, "sleep"),
            patch("builtins.print") as print_message,
        ):
            success = monitor_and_trade.send_discord_message("訊號測試")

        self.assertFalse(success)
        output = "\n".join(
            " ".join(str(argument) for argument in printed.args)
            for printed in print_message.call_args_list
        )
        self.assertNotIn(webhook_url, output)
        self.assertIn("<Discord webhook>", output)

    def test_can_send_the_legacy_notice_to_the_six_strategy_webhook(self):
        response = Mock()
        response.json.return_value = {"id": "legacy-message"}
        secondary_url = monitor_and_trade.os.getenv(
            monitor_and_trade.SECONDARY_DISCORD_WEBHOOK_ENV
        ).strip()

        with patch.object(
            monitor_and_trade.requests,
            "post",
            return_value=response,
        ) as post:
            success = monitor_and_trade.send_discord_message(
                "舊六策略格式",
                monitor_and_trade.SECONDARY_DISCORD_WEBHOOK_ENV,
            )

        self.assertTrue(success)
        post.assert_called_once_with(
            secondary_url,
            params={"wait": "true"},
            json={"username": "NotifierBot", "content": "舊六策略格式"},
            timeout=15,
        )

    def test_builds_the_same_legacy_six_strategy_notice_format(self):
        signal = Mock(
            strategy_code="CFCTX16m",
            previous_position=-1,
            new_position=0,
        )
        with (
            patch.object(
                monitor_and_trade,
                "load_latest_ef_positions",
                return_value={"CFCTX16m": 0, "CFCTX22m": -1},
            ),
            patch.object(monitor_and_trade, "latest_market_price", return_value=45062),
            patch.object(
                monitor_and_trade,
                "latest_mxf_notice_text",
                return_value="籌碼：坦克 241，游擊 -862",
            ),
            patch.object(monitor_and_trade, "datetime") as mocked_datetime,
        ):
            mocked_datetime.now.return_value = datetime(
                2026,
                8,
                22,
                4,
                0,
                3,
                tzinfo=monitor_and_trade.TZ,
            )
            message = monitor_and_trade.build_legacy_six_strategy_message(signal)

        self.assertEqual(
            message,
            "[04:00:03]：贏家F投組。財神列車16號(CFCTX16m) "
            "-1.0 -> 0.0。下單價位：45062，下單後策略倉位：空1口\n"
            "籌碼：坦克 241，游擊 -862",
        )


class SignalBatchNotificationTests(unittest.TestCase):
    def test_every_signal_is_notified_before_one_final_reconciliation(self):
        reasons = ["H 空1口->多1口", "H 多1口->多1口", "CFCTX22m 0->1"]
        decision = Mock(ready=True, target_position=-2)
        events = Mock()

        with (
            patch.object(monitor_and_trade, "load_json", return_value={}),
            patch.object(monitor_and_trade, "save_json_atomic"),
            patch.object(monitor_and_trade, "save_decision"),
            patch.object(monitor_and_trade, "evaluate_records", return_value=decision),
            patch.object(
                monitor_and_trade,
                "write_combined_position",
                return_value={"U": 1, "final_target_position": -2},
            ),
            patch.object(monitor_and_trade, "decision_text", return_value="最終判斷"),
            patch.object(
                monitor_and_trade,
                "send_discord_message",
                side_effect=lambda _message: events.notify() or True,
            ) as notify,
            patch.object(
                monitor_and_trade,
                "apply_final_position_file",
                side_effect=lambda *_args, **_kwargs: events.reconcile(),
            ) as reconcile,
        ):
            monitor_and_trade.execute_signal_batch(reasons)

        self.assertEqual(
            [event[0] for event in events.method_calls],
            ["notify", "notify", "notify", "reconcile"],
        )
        self.assertEqual(notify.call_count, 3)
        reconcile.assert_called_once_with(
            "本批3筆訊號，最後訊號：CFCTX22m 0->1",
            decision_summary="最終判斷",
            notify_unchanged=False,
        )

    def test_preorder_notification_failure_blocks_reconciliation(self):
        decision = Mock(ready=True, target_position=-2)

        with (
            patch.object(monitor_and_trade, "load_json", return_value={}),
            patch.object(monitor_and_trade, "save_json_atomic"),
            patch.object(monitor_and_trade, "save_decision"),
            patch.object(monitor_and_trade, "evaluate_records", return_value=decision),
            patch.object(
                monitor_and_trade,
                "write_combined_position",
                return_value={"U": 1, "final_target_position": -2},
            ),
            patch.object(monitor_and_trade, "decision_text", return_value="最終判斷"),
            patch.object(monitor_and_trade, "send_discord_message", return_value=False),
            patch.object(monitor_and_trade, "apply_final_position_file") as reconcile,
        ):
            monitor_and_trade.execute_signal_batch(["CFCTX22m 0->1"])

        reconcile.assert_not_called()


class RealOrderFailureDeduplicationTests(unittest.TestCase):
    def test_same_failed_target_is_not_ordered_or_notified_again(self):
        state = {
            "last_simulated_target": -1,
            "last_order_error_at": "2026-08-24 19:39:52",
            "last_order_error_target": 1,
            "last_order_error": "可委託金額不足",
        }

        with (
            patch.object(monitor_and_trade, "load_json", return_value=state),
            patch.object(
                monitor_and_trade,
                "reconcile_manual_override",
                return_value={"U": 1, "final_target_position": 1},
            ),
            patch.object(monitor_and_trade, "env_flag", return_value=True),
            patch.object(monitor_and_trade, "execute_target_position") as execute,
            patch.object(monitor_and_trade, "send_discord_message") as notify,
            patch("builtins.print") as print_message,
        ):
            success = monitor_and_trade.apply_final_position_file("新訊號但目標未變")

        self.assertTrue(success)
        execute.assert_not_called()
        notify.assert_not_called()
        self.assertIn("不論上次成功或失敗都不重送", print_message.call_args.args[0])

    def test_same_successful_target_is_not_ordered_again(self):
        state = {
            "last_simulated_target": 1,
            "last_order_attempt_target": 1,
            "last_order_attempt_at": "2026-08-24 19:39:52",
        }

        with (
            patch.object(monitor_and_trade, "load_json", return_value=state),
            patch.object(
                monitor_and_trade,
                "reconcile_manual_override",
                return_value={"U": 1, "final_target_position": 1},
            ),
            patch.object(monitor_and_trade, "env_flag", return_value=True),
            patch.object(monitor_and_trade, "execute_target_position") as execute,
            patch.object(monitor_and_trade, "send_discord_message") as notify,
        ):
            success = monitor_and_trade.apply_final_position_file("新訊號但目標未變")

        self.assertTrue(success)
        execute.assert_not_called()
        notify.assert_not_called()

    def test_successful_different_target_clears_previous_failure_lock(self):
        state = {
            "last_simulated_target": -1,
            "last_order_error_at": "2026-08-24 19:39:52",
            "last_order_error_target": 1,
            "last_order_error": "可委託金額不足",
        }
        order_result = Mock(
            previous_position=-1,
            actual_position=0,
            side="buy",
            quantity=1,
            order_sent=True,
        )

        with (
            patch.object(monitor_and_trade, "load_json", return_value=state),
            patch.object(
                monitor_and_trade,
                "reconcile_manual_override",
                return_value={"U": 1, "final_target_position": 0},
            ),
            patch.object(monitor_and_trade, "env_flag", return_value=True),
            patch.object(
                monitor_and_trade,
                "execute_target_position",
                return_value=order_result,
            ) as execute,
            patch.object(monitor_and_trade, "send_discord_message", return_value=True),
            patch.object(monitor_and_trade, "append_trade_transition"),
            patch.object(monitor_and_trade, "save_json_atomic") as save_state,
        ):
            success = monitor_and_trade.apply_final_position_file("目標改變")

        self.assertTrue(success)
        execute.assert_called_once_with(0)
        saved_state = save_state.call_args.args[1]
        self.assertEqual(saved_state["last_order_attempt_target"], 0)
        self.assertNotIn("last_order_error_at", saved_state)
        self.assertNotIn("last_order_error_target", saved_state)
        self.assertNotIn("last_order_error", saved_state)

    def test_failure_reminders_stop_permanently_after_the_fifth_notification(self):
        state = {
            "active_order_failure": {
                "target_position": 1,
                "error": "可委託金額不足",
                "failed_at": "2026-08-24 19:39:30",
                "notification_count": 4,
                "last_notification_at": "2026-08-24 19:42:30",
                "next_notification_at": "2026-08-24 19:43:30",
            }
        }

        with (
            patch.object(monitor_and_trade, "load_json", return_value=state),
            patch.object(monitor_and_trade, "save_json_atomic") as save_state,
            patch.object(
                monitor_and_trade,
                "send_discord_message",
                return_value=True,
            ) as notify,
            patch("builtins.print"),
        ):
            first_result = monitor_and_trade.process_order_failure_reminder(
                datetime(2026, 8, 24, 19, 43, 30, tzinfo=monitor_and_trade.TZ)
            )
            second_result = monitor_and_trade.process_order_failure_reminder(
                datetime(2026, 8, 24, 19, 45, 30, tzinfo=monitor_and_trade.TZ)
            )

        self.assertTrue(first_result)
        self.assertFalse(second_result)
        notify.assert_called_once()
        self.assertIn("提醒：5/5", notify.call_args.args[0])
        failure = save_state.call_args.args[1]["active_order_failure"]
        self.assertEqual(failure["notification_count"], 5)
        self.assertIsNone(failure["next_notification_at"])
        self.assertIn("reminders_completed_at", failure)

    def test_failure_reminder_waits_until_one_minute_is_due(self):
        state = {
            "active_order_failure": {
                "target_position": 1,
                "error": "可委託金額不足",
                "failed_at": "2026-08-24 19:39:30",
                "notification_count": 1,
                "last_notification_at": "2026-08-24 19:39:30",
                "next_notification_at": "2026-08-24 19:40:30",
            }
        }

        with (
            patch.object(monitor_and_trade, "load_json", return_value=state),
            patch.object(monitor_and_trade, "save_json_atomic") as save_state,
            patch.object(monitor_and_trade, "send_discord_message") as notify,
        ):
            result = monitor_and_trade.process_order_failure_reminder(
                datetime(2026, 8, 24, 19, 40, 29, tzinfo=monitor_and_trade.TZ)
            )

        self.assertFalse(result)
        notify.assert_not_called()
        save_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
