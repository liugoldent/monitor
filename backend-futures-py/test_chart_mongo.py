import sys
import unittest
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chart_mongo
import webhook_server


class ChartMongoTest(unittest.TestCase):
    def test_webhook_retry_uses_same_document(self):
        row = {
            "Symbol": "MXF1!", "Timeframe": "1",
            "TradingView Time": "2026-11-24 08:45:00", "Close": 28000,
        }
        changed = {**row, "Close": 28001, "Record Time": "2026-11-24 08:45:30"}
        self.assertEqual(
            chart_mongo.document_id("webhook_data_1min.csv", row),
            chart_mongo.document_id("webhook_data_1min.csv", changed),
        )

    def test_mirror_upserts_without_network(self):
        collection = MagicMock()
        row = {"time": "2026-11-24 08:45:00", "mtx_bvav_avg": 12.5}
        with patch.object(chart_mongo, "_collection", return_value=collection), patch.object(chart_mongo, "_maybe_prune"):
            chart_mongo.mirror_row("mxf_value.csv", row)
        collection.update_one.assert_called_once_with(
            {"_id": chart_mongo.document_id("mxf_value.csv", row)},
            {"$set": {**row, "date": "2026-11-24"}},
            upsert=True,
        )

    def test_strategy_event_keys_are_distinct(self):
        a = {"event_key": "chat:1", "new_position": 1}
        b = {"event_key": "chat:2", "new_position": 1}
        self.assertNotEqual(
            chart_mongo.document_id("h_position_events.csv", a),
            chart_mongo.document_id("h_position_events.csv", b),
        )

    def test_retention_uses_ten_weekdays_and_removes_older_chart_rows(self):
        from datetime import date, timedelta
        candle_collection = MagicMock()
        start = date(2026, 11, 9)
        candle_collection.distinct.return_value = [
            (start + timedelta(days=i)).isoformat() for i in range(16)
        ]
        other_collection = MagicMock()
        def collection(source):
            return candle_collection if source == "webhook_data_1min.csv" else other_collection
        with patch.object(chart_mongo, "_collection", side_effect=collection):
            cutoff = chart_mongo.prune_old_chart_data()
        self.assertEqual(cutoff, "2026-11-11")
        candle_collection.delete_many.assert_called_with({"date": {"$lt": cutoff}})
        self.assertEqual(candle_collection.delete_many.call_count, 1)
        self.assertEqual(other_collection.delete_many.call_count, 1)

    def test_strategy_history_keeps_old_and_duplicate_events(self):
        first = {"source_order": 1, "strategy_code": "A", "signal": "same"}
        second = {"source_order": 2, "strategy_code": "A", "signal": "same"}
        self.assertNotEqual(
            chart_mongo.document_id("six_strategy_signal_events.csv", first),
            chart_mongo.document_id("six_strategy_signal_events.csv", second),
        )

    def test_webhook_stays_successful_when_mongo_fails(self):
        body = json.dumps({
            "symbol": "MXF1!", "timeframe": "1", "time": 1795452300000,
            "open": 28000, "high": 28010, "low": 27990, "close": 28005,
        }).encode()
        handler = object.__new__(webhook_server.WebhookHandler)
        handler.path = "/webhook"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.send_error = MagicMock()
        with patch.object(webhook_server, "_append_webhook_row"), patch.object(
            webhook_server, "mirror_row", side_effect=RuntimeError("database unavailable")
        ):
            handler.do_POST()
        handler.send_response.assert_called_once_with(200)
        handler.send_error.assert_not_called()
        self.assertEqual(handler.wfile.getvalue(), b"Success")


if __name__ == "__main__":
    unittest.main()
