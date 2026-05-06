"""Webhook ingestion server.

This module only receives webhook payloads, persists candle rows, and dispatches
to strategy modules. Strategy logic lives in separate files.
"""

from __future__ import annotations

import csv
import http.server
import json
import os
import socketserver
import sys
from datetime import datetime

PORT = 8080
BASE_DIR = os.path.dirname(__file__)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from strategy_common import TZ, ensure_csv_header
from strategy_h_follow import apply_h_follow_strategy
from strategy_tt_mxf_draft import apply_tt_mxf_draft_strategy
from strategy_tt_mxf_live import apply_tt_mxf_live_strategy

TV_DOC_DIR = os.path.join(BASE_DIR, "tv_doc")

CSV_FILE_1MIN = os.path.join(TV_DOC_DIR, "webhook_data_1min.csv")
CSV_FILE_5MIN = os.path.join(TV_DOC_DIR, "webhook_data_5min.csv")
CSV_FILE_10MIN = os.path.join(TV_DOC_DIR, "webhook_data_10min.csv")
CSV_FILE_15MIN = os.path.join(TV_DOC_DIR, "webhook_data_15min.csv")

CSV_FILE_BY_TIMEFRAME = {
    "1": CSV_FILE_1MIN,
    "5": CSV_FILE_5MIN,
    "10": CSV_FILE_10MIN,
    "15": CSV_FILE_15MIN,
}

CSV_HEADER = [
    "Record Time",
    "Symbol",
    "Timeframe",
    "TradingView Time",
    "Open",
    "High",
    "Low",
    "Close",
    "MA_960",
    "MA_P80",
    "MA_P200",
    "MA_N110",
    "MA_N200",
    "tt_short",
    "tt_long",
    "BBR",
]


def _append_webhook_row(path: str, row: list[object]) -> None:
    """Append a received webhook candle to the target CSV."""
    ensure_csv_header(path, CSV_HEADER)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(row)


class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        """Receive webhook data, persist it, and dispatch strategies."""
        if self.path != "/webhook":
            self.send_error(404, "Not Found")
            return

        body = ""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)
            print(f"Received webhook: {data}")

            if not data:
                self.send_error(400, "No Data Provided")
                return

            current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
            symbol = data.get("symbol", "Unknown")
            timeframe = str(data.get("timeframe", "")).strip()
            tv_time_ms = data.get("time", "")
            open_price = data.get("open", "")
            high_price = data.get("high", "")
            low_price = data.get("low", "")
            close_price = data.get("close", "")
            ma_960 = data.get("ma_960", "")
            ma_p80 = data.get("ma_p80", "")
            ma_p200 = data.get("ma_p200", "")
            ma_n110 = data.get("ma_n110", "")
            ma_n200 = data.get("ma_n200", "")
            tt_short = str(data.get("tt_short", "")).strip()
            tt_long = str(data.get("tt_long", "")).strip()
            bbr = data.get("bbr", "")

            tv_time = ""
            try:
                if tv_time_ms:
                    tv_time = datetime.fromtimestamp(int(tv_time_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                tv_time = str(tv_time_ms)

            target_csv = CSV_FILE_BY_TIMEFRAME.get(timeframe)
            if target_csv is None:
                self.send_error(400, f"Unsupported timeframe: {timeframe}")
                return

            webhook_row = [
                current_time,
                symbol,
                timeframe,
                tv_time,
                open_price,
                high_price,
                low_price,
                close_price,
                ma_960,
                ma_p80,
                ma_p200,
                ma_n110,
                ma_n200,
                tt_short,
                tt_long,
                bbr,
            ]
            _append_webhook_row(target_csv, webhook_row)

            if timeframe == "1":
                apply_tt_mxf_live_strategy()
                apply_h_follow_strategy()
            elif timeframe == "15":
                apply_tt_mxf_draft_strategy()

            print(f"✅ Received: {symbol} @ {close_price} (Time: {current_time}, timeframe={timeframe})")
            sys.stdout.flush()

            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Success")
        except json.JSONDecodeError as exc:
            print(f"❌ JSON Decode Error: {exc}")
            print(f"❌ Raw Body: {body}")
            sys.stdout.flush()
            self.send_error(400, f"Invalid JSON: {exc}")
        except Exception as exc:
            print(f"Error processing webhook: {exc}")
            sys.stdout.flush()
            self.send_error(500, f"Server Error: {str(exc)}")

    def do_GET(self):
        """Simple health check."""
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Webhook Server Running")
            return
        self.send_error(404, "Not Found")


def run_server():
    """Start the webhook HTTP server."""
    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    try:
        httpd = ThreadingHTTPServer(("", PORT), WebhookHandler)
        sys.stdout.flush()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    except Exception as exc:
        print(f"DTO Fatal Error: {exc}")
    finally:
        if "httpd" in locals():
            httpd.server_close()
        print("Server stopped.")
        sys.stdout.flush()


if __name__ == "__main__":
    run_server()
