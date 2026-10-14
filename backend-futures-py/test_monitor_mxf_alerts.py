import os
import unittest
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
                patch.object(monitor, "GOODWAY_GUERRILLA_ALERTS") as alerts:
            monitor.poll_goodway_indicators(now)
        alerts.observe.assert_called_once_with(histories)
        alerts.flush.assert_called_once()

    def test_poll_retries_pending_even_when_fetch_fails(self):
        with patch.object(monitor, "fetch_goodway_indicators", side_effect=RuntimeError("offline")), \
                patch.object(monitor, "GOODWAY_GUERRILLA_ALERTS") as alerts, \
                patch("builtins.print"):
            monitor.poll_goodway_indicators(monitor.datetime.now(monitor.TZ))
        alerts.observe.assert_not_called()
        alerts.flush.assert_called_once()


if __name__ == "__main__":
    unittest.main()
