from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor_and_trade  # noqa: E402
from strategy import SixStrategySignal  # noqa: E402


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


class SignalBatchNotificationTests(unittest.TestCase):
    def test_every_signal_in_batch_is_processed_separately(self):
        reasons = ["H 空1口->多1口", "H 多1口->多1口", "CFCTX22m 0->1"]

        with patch.object(monitor_and_trade, "execute_current_decision") as execute:
            monitor_and_trade.execute_signal_batch(reasons)

        self.assertEqual(
            execute.call_args_list,
            [call(reason) for reason in reasons],
        )


class SixStrategyCompatibilityOutputTests(unittest.TestCase):
    def test_ef_signal_updates_original_csv_and_json_outputs(self):
        signal = SixStrategySignal(
            account="6008770",
            strategy_code="CFCTX16m",
            raw_strategy_code="CFCTX16m",
            previous_position=-1,
            new_position=0,
        )
        raw_message = (
            "【08.23 22:10:05】\n【訊號通知】【群益】\n【6008770】\n"
            "《策略》CFCTX16m《倉位》-1.0 -> 0.0"
        )

        with tempfile.TemporaryDirectory() as directory:
            signal_log_path = Path(directory) / "six_strategy_signal_events.csv"
            state_path = Path(directory) / "six_strategy_position_state.json"
            state_path.write_text(
                json.dumps({"strategies": {"CFC07m": {"position": 1}}}),
                encoding="utf-8",
            )
            with (
                patch.object(
                    monitor_and_trade,
                    "SIX_STRATEGY_SIGNAL_LOG_PATH",
                    signal_log_path,
                ),
                patch.object(
                    monitor_and_trade,
                    "SIX_STRATEGY_STATE_PATH",
                    state_path,
                ),
                patch.object(
                    monitor_and_trade,
                    "now_text",
                    return_value="2026-08-23 22:10:06",
                ),
            ):
                monitor_and_trade.write_six_strategy_compatible_outputs(
                    signal,
                    raw_message,
                )

            with signal_log_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["strategy_code"], "CFCTX16m")
            self.assertEqual(rows[0]["action"], "exit")
            self.assertEqual(rows[0]["side"], "bear")
            self.assertEqual(rows[0]["message_time"], "2026-08-23 22:10:05")

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("CFC07m", state["strategies"])
            updated = state["strategies"]["CFCTX16m"]
            self.assertEqual(updated["position"], 0)
            self.assertEqual(updated["side"], "flat")
            self.assertEqual(updated["portfolio"], "贏家投組F")
            self.assertEqual(updated["last_account"], "6008770")


if __name__ == "__main__":
    unittest.main()
