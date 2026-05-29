"""Webhook ingestion server.

This module receives webhook payloads, persists candle rows, and runs the active
H reverse guard strategy for the second account.
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
from auto_trade_shortCycle import execute_h_profit_breakout_add_signal
from strategy_h_loss_streak_follow import evaluate_h_loss_streak_follow
from strategy_h_profit_breakout_add import evaluate_h_profit_breakout_add
from strategy_h_profit_retrace_guard import evaluate_h_profit_retrace_guard
from strategy_h_reverse_guard_draft import evaluate_h_reverse_guard

TV_DOC_DIR = os.path.join(BASE_DIR, "tv_doc")

CSV_FILE_1MIN = os.path.join(TV_DOC_DIR, "webhook_data_1min.csv")
CSV_FILE_3MIN = os.path.join(TV_DOC_DIR, "webhook_data_3min.csv")
CSV_FILE_5MIN = os.path.join(TV_DOC_DIR, "webhook_data_5min.csv")
CSV_FILE_10MIN = os.path.join(TV_DOC_DIR, "webhook_data_10min.csv")
CSV_FILE_15MIN = os.path.join(TV_DOC_DIR, "webhook_data_15min.csv")

CSV_FILE_BY_TIMEFRAME = {
    "1": CSV_FILE_1MIN,
    "3": CSV_FILE_3MIN,
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
        """Receive webhook data, persist it, and run the H reverse guard."""
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
                # 第二帳號策略優先順序：
                # 1. 先檢查 H 連輸 2 次一次性跟單，因為這是最高優先策略。
                # 2. 如果連輸跟單已經進場，這根 1 分 K 就不再跑護欄/加碼，
                #    避免第二帳號同一時間被多個策略重複進場。
                # 3. 浮盈回吐保護目前只做 Discord 觀察通知，不擋其他策略、不下單。
                # 4. 如果連輸跟單沒有進場，才回到平常的反向護欄與 H 獲利突破加碼。
                loss_streak_signal = evaluate_h_loss_streak_follow()
                if loss_streak_signal:
                    print(f"🔁 H loss-streak follow signal: {loss_streak_signal}")

                if not loss_streak_signal or loss_streak_signal.get("action") != "enter":
                    guard_signal = evaluate_h_reverse_guard()
                    if guard_signal:
                        print(f"🛡️ H reverse guard signal: {guard_signal}")

                    if not guard_signal or guard_signal.get("action") != "enter":
                        profit_retrace_signal = evaluate_h_profit_retrace_guard()
                        if profit_retrace_signal:
                            print(f"🧯 H profit retrace guard signal: {profit_retrace_signal}")

                    profit_breakout_signal = evaluate_h_profit_breakout_add()
                    if profit_breakout_signal:
                        print(f"📈 H profit breakout add signal: {profit_breakout_signal}")
                        order_sent = execute_h_profit_breakout_add_signal(profit_breakout_signal)
                        print(f"📈 H profit breakout add order sent: {order_sent}")

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
