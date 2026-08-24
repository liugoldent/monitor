import unittest

from webhook_server import CSV_HEADER, _build_webhook_row, _parse_webhook_json


class ParseWebhookJsonTests(unittest.TestCase):
    def test_accepts_trailing_comma_at_end_of_object(self):
        body = '{"symbol":"MXF1!","timeframe":"1","ma_n200":45011.37,}'

        payload = _parse_webhook_json(body)

        self.assertEqual(payload["symbol"], "MXF1!")
        self.assertEqual(payload["ma_n200"], 45011.37)

    def test_rejects_other_invalid_json(self):
        with self.assertRaises(Exception):
            _parse_webhook_json('{"symbol":}')


class BuildWebhookRowTests(unittest.TestCase):
    def test_current_960ma_payload_does_not_require_legacy_fields(self):
        payload = {
            "symbol": "MXF1!",
            "timeframe": "1",
            "time": 1787533200000,
            "open": 24000,
            "high": 24010,
            "low": 23990,
            "close": 24005,
            "ma_960": 23950,
            "ma_p80": 24030,
            "ma_p200": 24150,
            "ma_n110": 23840,
            "ma_n200": 23750,
        }

        timeframe, row = _build_webhook_row(payload, "2026-08-24 09:01:00")

        self.assertEqual(timeframe, "1")
        self.assertEqual(len(row), len(CSV_HEADER))
        self.assertEqual(row[-3:], ["", "", ""])
        self.assertEqual(dict(zip(CSV_HEADER, row))["Close"], 24005)


if __name__ == "__main__":
    unittest.main()
