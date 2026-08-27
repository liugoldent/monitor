import csv
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch


os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test")
os.environ.setdefault("DISCORD_H_TRADE_WEBHOOK_URL", "https://example.test/h")
os.environ.setdefault("DISCORD_SIX_STRATEGY_WEBHOOK_URL", "https://example.test/ef")

import telegram_signal_relay as relay
from telegram_signal_relay import _discord_chunks, classify_signal


class TelegramSignalRelayTests(unittest.TestCase):
    def test_classifies_h_signal(self):
        self.assertEqual(classify_signal("浩克3V3訊號通知\n目前方向：多"), "h")

    def test_classifies_ef_signal(self):
        text = "訊號通知《策略》CFC07m《倉位》0 -> 1"
        self.assertEqual(classify_signal(text), "ef")

    def test_ignores_unrelated_message(self):
        self.assertIsNone(classify_signal("一般 Telegram 訊息"))

    def test_h_requires_h_marker(self):
        self.assertIsNone(classify_signal("小型台指近一訊號部位為：多 1 口"))

    def test_discord_chunks_stay_within_limit(self):
        chunks = _discord_chunks("prefix\n", "x" * 5000)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))

    def test_startup_notice_delivery_uses_supplied_content(self):
        response = Mock()
        response.raise_for_status.return_value = None
        with patch.object(relay.requests, "post", return_value=response) as post:
            delivered, detail = relay.send_discord_notice(
                "https://example.test/webhook",
                "startup content",
            )
        self.assertTrue(delivered)
        self.assertIn("attempt 1", detail)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"], {"content": "startup content"})

    def test_records_ef_in_existing_csv_format(self):
        with tempfile.TemporaryDirectory() as directory:
            original = relay.EF_SIGNAL_LOG_PATH
            original_events = relay.EF_POSITION_EVENT_PATH
            path = Path(directory) / "six_strategy_signal_events.csv"
            relay.EF_SIGNAL_LOG_PATH = path
            relay.EF_POSITION_EVENT_PATH = Path(directory) / "ef_position_events.csv"
            try:
                recorded = relay.record_ef_signal(
                    "群益訊號通知【6008770】【08.27 22:30:01】"
                    "《策略》CFC07m《倉位》0.0 -> 1.0",
                    datetime(2026, 8, 27, 22, 30, 2, tzinfo=ZoneInfo("Asia/Taipei")),
                    "123:456",
                )
                with path.open(newline="", encoding="utf-8") as handle:
                    row = next(csv.DictReader(handle))
                with relay.EF_POSITION_EVENT_PATH.open(newline="", encoding="utf-8") as handle:
                    event_row = next(csv.DictReader(handle))
            finally:
                relay.EF_SIGNAL_LOG_PATH = original
                relay.EF_POSITION_EVENT_PATH = original_events
        self.assertTrue(recorded)
        self.assertEqual(row["strategy_code"], "CFC07m")
        self.assertEqual(row["new_position"], "1")
        self.assertEqual(row["quantity"], "1")
        self.assertEqual(event_row["event_key"], "123:456")
        self.assertEqual(event_row["source"], "群益Telegram")

    def test_records_h_transition_using_latest_one_minute_close(self):
        with tempfile.TemporaryDirectory() as directory:
            original_trade = relay.H_TRADE_LOG_PATH
            original_price = relay.WEBHOOK_DATA_1MIN_PATH
            original_events = relay.H_POSITION_EVENT_PATH
            relay.H_TRADE_LOG_PATH = Path(directory) / "h_trade.csv"
            relay.WEBHOOK_DATA_1MIN_PATH = Path(directory) / "webhook_data_1min.csv"
            relay.H_POSITION_EVENT_PATH = Path(directory) / "h3_position_events.csv"
            relay.WEBHOOK_DATA_1MIN_PATH.write_text(
                "Record Time,Close\n2026-08-27 22:29:00,46200\n",
                encoding="utf-8",
            )
            try:
                recorded = relay.record_h_signal(
                    "浩克3V3訊號通知\n小型台指近一訊號部位為：多 2 口",
                    datetime(2026, 8, 27, 22, 30, tzinfo=ZoneInfo("Asia/Taipei")),
                    "789:1011",
                )
                with relay.H_TRADE_LOG_PATH.open(newline="", encoding="utf-8") as handle:
                    row = next(csv.DictReader(handle))
                with relay.H_POSITION_EVENT_PATH.open(newline="", encoding="utf-8") as handle:
                    event_row = next(csv.DictReader(handle))
            finally:
                relay.H_TRADE_LOG_PATH = original_trade
                relay.WEBHOOK_DATA_1MIN_PATH = original_price
                relay.H_POSITION_EVENT_PATH = original_events
        self.assertTrue(recorded)
        self.assertEqual(row["action"], "enter")
        self.assertEqual(row["side"], "bull")
        self.assertEqual(row["price"], "46200.0")
        self.assertEqual(row["quantity"], "1")
        self.assertEqual(event_row["event_key"], "789:1011")
        self.assertEqual(event_row["new_position"], "1")
        self.assertIn("多 1 口", event_row["raw_message"])


if __name__ == "__main__":
    unittest.main()
