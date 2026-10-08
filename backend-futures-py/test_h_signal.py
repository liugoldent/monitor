import unittest

from h_signal import parse_h_direction, parse_h_event_direction


class HSignalTests(unittest.TestCase):
    def test_legacy_and_bs_directions_ignore_notification_size(self):
        for suffix, direction in (
            ("小型台指近一訊號部位為：多 2 口", 1),
            ("空3口", -1),
            ("(B=1 S=0)", 1),
            ("(B=0 S=1)", -1),
            ("( B = 9\n S = 0 )", 1),
            ("（B=0 S=20）", -1),
            ("多2口 (B=8 S=0)", 1),
        ):
            with self.subTest(suffix=suffix):
                self.assertEqual(parse_h_direction(f"[浩克3V3 交易訊號通知 {suffix}]"), direction)

    def test_invalid_or_conflicting_direction_is_rejected(self):
        for suffix in (
            "多0口", "多1口 空1口", "多1口 多2口", "(B=0 S=0)",
            "(B=1 S=1)", "(B=-1 S=0)", "(B=1 S=0.5)", "(B=1)",
            "(B=1 S=0) (B=0 S=1)", "多1口 (B=0 S=1)",
        ):
            with self.subTest(suffix=suffix):
                self.assertIsNone(parse_h_direction(f"浩克3V3 交易訊號通知 {suffix}"))
        for text in (None, 123, "其他策略 訊號通知 (B=1 S=0)", "浩克3 (B=1 S=0)"):
            with self.subTest(text=text):
                self.assertIsNone(parse_h_direction(text))

    def test_account_event_requires_received_h_and_trusted_sender_metadata(self):
        event = dict(event="received", route="h", sender_username="Taiwan_MXF_Bot",
                     text="自動交易\n浩克3V3 交易訊號通知 (B=0 S=1)\nFrom:")
        self.assertEqual(parse_h_event_direction(event), -1)
        for overrides in (
            dict(event="csv_record"), dict(event="discord_delivery"), dict(route="ef"),
            dict(sender_username="fake"), dict(sender_username=None),
            dict(sender_username=123), dict(sender_username="taiwan_mxf_bot_fake"),
        ):
            with self.subTest(overrides=overrides):
                self.assertIsNone(parse_h_event_direction({**event, **overrides}))


if __name__ == "__main__":
    unittest.main()
