from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor_and_trade  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
