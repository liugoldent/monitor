import os
import csv
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")

import monitor_mxf as monitor


NEAR, NEXT = monitor.GOODWAY_GUERRILLA_SYMBOLS
START = 1791954000000


def history(value, stamp=START, ticker=NEAR, **fields):
    return {ticker: {stamp: {"time": stamp, "high": value, **fields}}}


class GoodwayGuerrillaAlertTests(unittest.TestCase):
    def setUp(self):
        self.alerts = monitor.GoodwayGuerrillaAlerts()

    def observe(self, value, minute, ticker=NEAR, **fields):
        self.alerts.observe(history(value, START + minute * 60000, ticker, **fields))

    def test_startup_outside_threshold_only_sets_baseline(self):
        for value in (1700, -1700):
            alerts = monitor.GoodwayGuerrillaAlerts()
            alerts.observe(history(value))
            self.assertFalse(alerts.pending)

    def test_positive_cross_and_staying_above_sends_once(self):
        for minute, value in enumerate((1499, 1501, 1600, 1700)):
            self.observe(value, minute)
        self.assertEqual(len(self.alerts.pending), 1)
        self.assertIn("向上突破 +1500", self.alerts.pending[0])
        self.assertIn("1499 → 1501", self.alerts.pending[0])
        self.assertIn("資料時間", self.alerts.pending[0])

    def test_1000_crosses_and_recrosses_but_equality_and_staying_outside_are_silent(self):
        for sign in (1, -1):
            with self.subTest(sign=sign):
                alerts = monitor.GoodwayGuerrillaAlerts()
                for minute, value in enumerate((999, 1000, 1000)):
                    alerts.observe(history(sign * value, START + minute * 60000))
                self.assertFalse(alerts.pending)
                for minute, value in enumerate((1001, 1100, 900, 1001), 3):
                    alerts.observe(history(sign * value, START + minute * 60000))
                self.assertEqual(len(alerts.pending), 2)
                label = "向上突破 +1000" if sign == 1 else "向下跌破 -1000"
                self.assertTrue(all(label in message for message in alerts.pending))

    def test_1000_same_minute_revision_is_detected_once(self):
        for sign in (1, -1):
            alerts = monitor.GoodwayGuerrillaAlerts()
            for value in (999, 1001, 1001):
                alerts.observe(history(sign * value))
            self.assertEqual(len(alerts.pending), 1)

    def test_jump_crosses_both_levels_and_merges_identical_contracts_at_each_level(self):
        for sign in (1, -1):
            alerts = monitor.GoodwayGuerrillaAlerts()
            for minute, value in enumerate((900, 1600)):
                stamp = START + minute * 60000
                alerts.observe({
                    **history(sign * value, stamp, NEAR),
                    **history(sign * value, stamp, NEXT),
                })
            self.assertEqual(len(alerts.pending), 2)
            messages = list(alerts.pending)
            self.assertIn("1000", messages[0])
            self.assertIn("1500", messages[1])
            self.assertTrue(all(NEAR in message and NEXT in message for message in messages))

    def test_each_threshold_rearms_independently(self):
        for sign in (1, -1):
            alerts = monitor.GoodwayGuerrillaAlerts()
            for minute, value in enumerate((900, 1600, 1200, 1600, 900, 1100)):
                alerts.observe(history(sign * value, START + minute * 60000))
            self.assertEqual(len(alerts.pending), 4)
            self.assertEqual(sum("1000" in message for message in alerts.pending), 2)
            self.assertEqual(sum("1500" in message for message in alerts.pending), 2)

    def test_negative_cross_and_staying_below_sends_once(self):
        for minute, value in enumerate((-1499, -1501, -1600, -1700)):
            self.observe(value, minute, NEXT)
        self.assertEqual(len(self.alerts.pending), 1)
        self.assertIn("向下跌破 -1500", self.alerts.pending[0])
        self.assertIn("次月", self.alerts.pending[0])

    def test_equal_threshold_waits_until_strictly_outside(self):
        for sign in (1, -1):
            alerts = monitor.GoodwayGuerrillaAlerts()
            for minute, value in enumerate((1499, 1500, 1500)):
                alerts.observe(history(sign * value, START + minute * 60000))
                self.assertFalse(alerts.pending)
            alerts.observe(history(sign * 1501, START + 3 * 60000))
            self.assertEqual(len(alerts.pending), 1)

    def test_return_inside_is_silent_and_recross_alerts(self):
        for minute, value in enumerate((1499, 1501, 1490)):
            self.observe(value, minute)
        self.assertEqual(len(self.alerts.pending), 1)
        self.observe(1600, 3)
        self.assertEqual(len(self.alerts.pending), 2)

    def test_unrelated_fields_and_indicators_do_not_alert(self):
        self.observe(0, 0)
        self.observe(1, 1, open=9999, low=-9999, close=9999, volume=-9999)
        self.alerts.observe(history(9000, START, "MTX-2#trade_info"))
        self.assertFalse(self.alerts.pending)

    def test_identical_prefix_crossings_merge_into_one_message(self):
        for minute, value in enumerate((1400, 1600)):
            self.alerts.observe({
                **history(value, START + minute * 60000, NEAR),
                **history(value, START + minute * 60000, NEXT),
            })
        self.assertEqual(len(self.alerts.pending), 1)
        self.assertIn(NEAR, self.alerts.pending[0])
        self.assertIn(NEXT, self.alerts.pending[0])

    def test_contracts_cross_independently(self):
        self.observe(1400, 0, NEAR)
        self.observe(-1400, 0, NEXT)
        self.observe(1600, 1, NEAR)
        self.observe(-1600, 2, NEXT)
        self.assertEqual(len(self.alerts.pending), 2)

    def test_invalid_or_missing_data_preserves_baseline(self):
        self.observe(1499, 0)
        for minute, value in enumerate((None, "bad", float("nan"), float("inf")), 1):
            self.observe(value, minute)
        self.alerts.observe({})
        self.observe("1,501", 5)
        self.assertEqual(len(self.alerts.pending), 1)
        self.assertIn("1499 → 1501", self.alerts.pending[0])

    def test_old_and_repeated_samples_do_not_retrigger(self):
        self.observe(1400, 1)
        self.observe(1600, 2)
        self.observe(1400, 0)
        self.observe(1600, 2)
        self.observe(1700, 3)
        self.assertEqual(len(self.alerts.pending), 1)

    def test_same_minute_revision_can_cross(self):
        self.observe(1400, 0)
        self.observe(1600, 0)
        self.observe(1600, 0)
        self.assertEqual(len(self.alerts.pending), 1)

    def test_catchup_processes_unseen_minutes_in_order(self):
        self.observe(1400, 0)
        self.alerts.observe({NEAR: {
            START + 3 * 60000: {"high": 1600},
            START + 1 * 60000: {"high": 1600},
            START + 2 * 60000: {"high": 1400},
        }})
        self.assertEqual(len(self.alerts.pending), 2)

    def test_failed_delivery_retries_without_losing_recross(self):
        self.observe(1400, 0)
        self.observe(1600, 1)
        first = self.alerts.pending[0]
        with patch.object(monitor, "send_discord_message", side_effect=RuntimeError("secret")):
            with patch("builtins.print") as output:
                self.alerts.flush()
                self.assertNotIn("secret", str(output.call_args))
        self.observe(1400, 2)
        self.observe(1600, 3)
        second = self.alerts.pending[-1]
        with patch.object(monitor, "send_discord_message") as sender:
            with patch("builtins.print"):
                self.alerts.flush()
                self.alerts.flush()
        self.assertEqual(sender.call_args_list, [
            unittest.mock.call(first, monitor.WEBHOOK_URL),
            unittest.mock.call(second, monitor.WEBHOOK_URL),
        ])
        self.assertFalse(self.alerts.pending)

    def test_delivery_posts_to_alert_webhook(self):
        with patch.object(monitor.requests, "post") as post:
            monitor.send_discord_message("crossing", "https://example.invalid/webhook")
        post.assert_called_once_with(
            "https://example.invalid/webhook", json={"content": "crossing"}, timeout=20,
        )
        post.return_value.raise_for_status.assert_called_once()

    def test_poll_observes_successful_data_and_flushes(self):
        now = monitor.datetime.now(monitor.TZ)
        histories = history(1600)
        with patch.object(monitor, "fetch_goodway_indicators", return_value=(histories, {})), \
                patch.object(monitor, "print_goodway_indicators"), \
                patch.object(monitor, "GOODWAY_OPENING_ALERTS") as opening, \
                patch.object(monitor, "GOODWAY_GUERRILLA_ALERTS") as alerts:
            monitor.poll_goodway_indicators(now)
        alerts.observe.assert_called_once_with(histories)
        alerts.flush.assert_called_once()
        opening.observe.assert_called_once_with(histories, now)
        opening.flush.assert_called_once()

    def test_poll_retries_pending_even_when_fetch_fails(self):
        with patch.object(monitor, "fetch_goodway_indicators", side_effect=RuntimeError("offline")), \
                patch.object(monitor, "GOODWAY_GUERRILLA_ALERTS") as alerts, \
                patch.object(monitor, "GOODWAY_OPENING_ALERTS") as opening, \
                patch("builtins.print"):
            monitor.poll_goodway_indicators(monitor.datetime.now(monitor.TZ))
        alerts.observe.assert_not_called()
        alerts.flush.assert_called_once()
        opening.observe.assert_not_called()
        opening.flush.assert_called_once()


class GoodwayOpeningAlertTests(unittest.TestCase):
    def setUp(self):
        self.alerts = monitor.GoodwayOpeningAlerts()
        self.bar_time = datetime(2026, 10, 14, 8, 45, tzinfo=monitor.TZ)

    def observe(self, value, seconds=60, price=(49000, 49010), bar_time=None,
                alerts=None, ticker=NEAR):
        bar_time = bar_time or self.bar_time
        with patch.object(monitor, "read_opening_price_bar", return_value=price):
            (alerts or self.alerts).observe(
                history(value, int(bar_time.timestamp() * 1000), ticker),
                bar_time + timedelta(seconds=seconds),
            )

    def test_positive_below_ma_turns_long_only_after_day_bar_closes(self):
        self.observe(1, seconds=59)
        self.assertFalse(self.alerts.pending)
        self.observe(1)
        self.assertEqual(len(self.alerts.pending), 1)
        message = self.alerts.pending[0]
        self.assertIn("960下游擊正｜開盤轉多", message)
        self.assertIn("08:45（08:46 收棒）", message)
        self.assertIn("收盤價 49000｜MA_960 49010", message)

    def test_negative_above_ma_turns_short_after_night_bar_closes(self):
        bar_time = self.bar_time.replace(hour=15, minute=0)
        self.observe(-1, seconds=59, price=(49020, 49010), bar_time=bar_time)
        self.assertFalse(self.alerts.pending)
        self.observe(-1, price=(49020, 49010), bar_time=bar_time, ticker=NEXT)
        message = self.alerts.pending[0]
        self.assertIn("960上游擊負數｜開盤轉空", message)
        self.assertIn("夜盤", message)
        self.assertIn("15:00（15:01 收棒）", message)
        self.assertIn(NEXT, message)

    def test_both_conditions_required_and_equalities_are_silent(self):
        for value, price in (
            (1, (49020, 49010)), (-1, (49000, 49010)),
            (1, (49010, 49010)), (-1, (49010, 49010)),
            (0, (49000, 49010)), (0, (49020, 49010)),
        ):
            with self.subTest(value=value, price=price):
                alerts = monitor.GoodwayOpeningAlerts()
                self.observe(value, price=price, alerts=alerts)
                self.assertFalse(alerts.pending)
                self.assertEqual(len(alerts.processed), 1)

    def test_missing_prices_waits_and_missing_chip_preserves_opportunity(self):
        self.observe(10, price=None)
        for value in (None, "invalid", float("nan"), float("inf")):
            self.observe(value)
        self.assertFalse(self.alerts.processed)
        self.observe(10, seconds=120)
        self.assertEqual(len(self.alerts.pending), 1)

    def test_uses_opening_chip_not_latest_minute(self):
        stamp = int(self.bar_time.timestamp() * 1000)
        with patch.object(monitor, "read_opening_price_bar", return_value=(49000, 49010)):
            self.alerts.observe({NEAR: {
                stamp: {"high": 10}, stamp + 60000: {"high": -100},
            }}, self.bar_time + timedelta(minutes=2))
        self.assertIn("開盤轉多", self.alerts.pending[0])
        self.assertIn("游擊隊（high）：10", self.alerts.pending[0])

    def test_other_minutes_weekends_and_expired_openings_are_silent(self):
        self.observe(10, bar_time=self.bar_time.replace(minute=46))
        self.observe(10, bar_time=self.bar_time.replace(day=17))
        self.observe(10, seconds=901)
        self.assertFalse(self.alerts.pending)
        self.assertFalse(self.alerts.processed)

    def test_identical_contracts_merge_and_duplicate_polls_are_silent(self):
        stamp = int(self.bar_time.timestamp() * 1000)
        histories = {**history(10, stamp, NEAR), **history(10, stamp, NEXT)}
        with patch.object(monitor, "read_opening_price_bar", return_value=(49000, 49010)):
            for seconds in (60, 120, 180):
                self.alerts.observe(histories, self.bar_time + timedelta(seconds=seconds))
        self.assertEqual(len(self.alerts.pending), 1)
        self.assertIn(NEAR, self.alerts.pending[0])
        self.assertIn(NEXT, self.alerts.pending[0])

    def test_non_signal_opening_is_not_reinterpreted_after_revision(self):
        self.observe(0)
        self.observe(10, seconds=120)
        self.assertFalse(self.alerts.pending)

    def test_day_night_and_next_date_have_separate_openings(self):
        self.observe(10)
        self.observe(-10, price=(49020, 49010),
                     bar_time=self.bar_time.replace(hour=15, minute=0))
        self.observe(10, bar_time=self.bar_time + timedelta(days=1))
        self.assertEqual(len(self.alerts.pending), 3)

    def test_pending_survives_restart_and_sent_opening_is_not_resent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original = monitor.GoodwayOpeningAlerts(path)
            self.observe(10, alerts=original)
            message = original.pending[0]
            with patch.object(monitor, "send_discord_message", side_effect=RuntimeError("secret")), \
                    patch("builtins.print") as output:
                original.flush()
                self.assertNotIn("secret", str(output.call_args))
            restarted = monitor.GoodwayOpeningAlerts(path)
            self.observe(10, seconds=120, alerts=restarted)
            self.assertEqual(list(restarted.pending), [message])
            with patch.object(monitor, "send_discord_message") as sender, patch("builtins.print"):
                restarted.flush()
            sender.assert_called_once_with(message, monitor.WEBHOOK_URL)
            delivered = monitor.GoodwayOpeningAlerts(path)
            self.observe(10, seconds=180, alerts=delivered)
            self.assertFalse(delivered.pending)


class OpeningPriceBarTests(unittest.TestCase):
    def setUp(self):
        self.bar_time = datetime(2026, 10, 14, 8, 45, tzinfo=monitor.TZ)
        self.header = ["Symbol", "Timeframe", "TradingView Time", "Close", "MA_960"]

    def read_rows(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(self.header)
                writer.writerows(rows)
            with patch.object(monitor, "WEBHOOK_DATA_1MIN_PATH", path):
                return monitor.read_opening_price_bar(self.bar_time)

    def test_exact_minute_symbol_and_timeframe_are_required(self):
        timestamp = "2026-10-14 08:45:00"
        rows = [
            ["MXF1!", "1", timestamp, 49000, 49010],
            ["TXF1!", "1", timestamp, 50000, 50100],
            ["MXF1!", "5", timestamp, 50000, 50100],
            ["MXF1!", "1", "2026-10-14 08:46:00", 50000, 50100],
        ]
        self.assertEqual(self.read_rows(rows), (49000, 49010))
        self.assertIsNone(self.read_rows(rows[1:]))

    def test_missing_and_nonfinite_price_values_are_rejected(self):
        for close, ma in (("", 10), (10, ""), ("nan", 10), (10, "inf")):
            self.assertIsNone(self.read_rows([
                ["MXF1!", "1", "2026-10-14 08:45:00", close, ma],
            ]))

    def test_large_archive_tail_and_latest_matching_row(self):
        old = ["MXF1!", "1", "2026-10-13 08:45:00", 40000, 40100]
        rows = [old] * 2000 + [
            ["MXF1!", "1", "2026-10-14 08:45:00", 49000, 49010],
            ["MXF1!", "1", "2026-10-14 08:45:00", 49001, 49011],
        ]
        self.assertEqual(self.read_rows(rows), (49001, 49011))

    def test_incomplete_csv_append_is_not_treated_as_a_closed_bar(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.csv"
            path.write_text(
                ",".join(self.header) + "\nMXF1!,1,2026-10-14 08:45:00,49000,4901",
                encoding="utf-8",
            )
            with patch.object(monitor, "WEBHOOK_DATA_1MIN_PATH", path):
                self.assertIsNone(monitor.read_opening_price_bar(self.bar_time))


if __name__ == "__main__":
    unittest.main()
