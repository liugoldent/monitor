import http.server
import json
import csv
import os
import sys
from datetime import datetime
from threading import Thread
import socketserver
import time
from zoneinfo import ZoneInfo

from auto_trade import _get_last_entry, send_discord_message, sellOne
from auto_trade_shortCycle import _get_last_entry as _get_last_entry_short
from auto_trade_shortCycle import send_discord_message as send_discord_message_short
from auto_trade_shortCycle import sellOne as sell_one_short
import shioaji as sj

# Configuration
PORT = 8080
CSV_FILE = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data.csv")
CLEAR_TIME = (14, 30)
TZ = ZoneInfo("Asia/Taipei")
TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "h_trade.csv")
CA_PATH = os.getenv("CA_PATH") or os.path.join(os.path.dirname(__file__), "Sinopac.pfx")


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = str(value).replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None

# 長週期加空
def check_ma_p80_reentry_and_short() -> None:
    if not os.path.isfile(CSV_FILE):
        return

    with open(CSV_FILE, "r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if len(rows) < 2:
        return

    prev_row, curr_row = rows[-2], rows[-1]
    prev_close = _to_float(prev_row.get("Close"))
    prev_ma_p80 = _to_float(prev_row.get("MA_P80"))
    curr_close = _to_float(curr_row.get("Close"))
    curr_ma_p80 = _to_float(curr_row.get("MA_P80"))

    if None in (prev_close, prev_ma_p80, curr_close, curr_ma_p80):
        return

    if not (prev_close > prev_ma_p80 and curr_close < curr_ma_p80):
        return

    last_entry = _get_last_entry()
    if not last_entry or last_entry[0] != "bear":
        return

    api_key = os.getenv("API_KEY")
    secret_key = os.getenv("SECRET_KEY")
    if not api_key or not secret_key:
        print("❌ 缺少 API_KEY 或 SECRET_KEY")
        return

    if not os.path.exists(CA_PATH):
        print(f"❌ 找不到憑證檔案，目前嘗試路徑為: {CA_PATH}")
        return

    test_now = datetime.now(TZ)
    api = sj.Shioaji(simulation=False)
    api.login(api_key, secret_key)
    api.activate_ca(
        ca_path=CA_PATH,
        ca_passwd=os.getenv("PERSON_ID"),
        person_id=os.getenv("PERSON_ID"),
    )

    try:
        contract = api.Contracts.Futures.TMF.TMFR1
        sellOne(api, contract)
        send_discord_message(f'[{test_now:%H:%M:%S}] MA_P80 回檔，續空進場')
    except Exception as exc:
        print(f"❌ MA_P80 續空下單失敗: {exc}")
    finally:
        try:
            api.logout()
        except Exception:
            pass


# 短週期加空
def check_shortcycle_ma_reentry_and_short() -> None:
    if not os.path.isfile(CSV_FILE):
        return

    with open(CSV_FILE, "r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if len(rows) < 2:
        return

    prev_row, curr_row = rows[-2], rows[-1]
    prev_close = _to_float(prev_row.get("Close"))
    curr_close = _to_float(curr_row.get("Close"))
    prev_ma_n110 = _to_float(prev_row.get("MA_N110"))
    curr_ma_n110 = _to_float(curr_row.get("MA_N110"))
    prev_ma_960 = _to_float(prev_row.get("MA_960"))
    curr_ma_960 = _to_float(curr_row.get("MA_960"))
    prev_ma_p80 = _to_float(prev_row.get("MA_P80"))
    curr_ma_p80 = _to_float(curr_row.get("MA_P80"))

    if None in (
        prev_close,
        curr_close,
        prev_ma_n110,
        curr_ma_n110,
        prev_ma_960,
        curr_ma_960,
        prev_ma_p80,
        curr_ma_p80,
    ):
        return

    triggers = []
    if prev_close > prev_ma_n110 and curr_close < curr_ma_n110:
        triggers.append("MA_N110")
    if prev_close > prev_ma_960 and curr_close < curr_ma_960:
        triggers.append("MA_960")
    if prev_close > prev_ma_p80 and curr_close < curr_ma_p80:
        triggers.append("MA_P80")

    if not triggers:
        return

    last_entry = _get_last_entry_short()
    if not last_entry or last_entry[0] != "bear":
        return

    api_key = os.getenv("API_KEY2")
    secret_key = os.getenv("SECRET_KEY2")
    if not api_key or not secret_key:
        print("❌ 缺少 API_KEY2 或 SECRET_KEY2")
        return

    if not os.path.exists(CA_PATH):
        print(f"❌ 找不到憑證檔案，目前嘗試路徑為: {CA_PATH}")
        return

    test_now = datetime.now(TZ)
    api = sj.Shioaji(simulation=False)
    api.login(api_key, secret_key)
    api.activate_ca(
        ca_path=CA_PATH,
        ca_passwd=os.getenv("PERSON_ID"),
        person_id=os.getenv("PERSON_ID"),
    )

    try:
        contract = api.Contracts.Futures.TMF.TMFR1
        sell_one_short(api, contract)
        send_discord_message_short(
            f'[{test_now:%H:%M:%S}] 短週期續空進場 ({", ".join(triggers)})'
        )
    except Exception as exc:
        print(f"❌ 短週期續空下單失敗: {exc}")
    finally:
        try:
            api.logout()
        except Exception:
            pass

# 獲取webhook並處理
class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/webhook':
            try:
                # Get content length

                content_length = int(self.headers.get('Content-Length', 0))
                print(content_length)
                
                # Read body
                body = self.rfile.read(content_length).decode('utf-8')
                print(body)
                data = json.loads(body)

                if data:
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    symbol = data.get('symbol', 'Unknown')
                    tv_time_ms = data.get('time', '')
                    open_price = data.get('open', '')
                    high_price = data.get('high', '')
                    low_price = data.get('low', '')
                    close_price = data.get('close', '')
                    ma_960 = data.get('ma_960', '')
                    ma_p80 = data.get('ma_p80', '')
                    ma_p200 = data.get('ma_p200', '')
                    ma_n110 = data.get('ma_n110', '')
                    ma_n200 = data.get('ma_n200', '')

                    tv_time = ""
                    try:
                        if tv_time_ms:
                            tv_time = datetime.fromtimestamp(int(tv_time_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        tv_time = str(tv_time_ms)

                    file_exists = os.path.isfile(CSV_FILE)
                    with open(CSV_FILE, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        if not file_exists:
                            writer.writerow([
                                'Record Time',
                                'Symbol',
                                'TradingView Time',
                                'Open',
                                'High',
                                'Low',
                                'Close',
                                'MA_960',
                                'MA_P80',
                                'MA_P200',
                                'MA_N110',
                                'MA_N200',
                            ])
                        writer.writerow([
                            current_time,
                            symbol,
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
                        ])

                    print(f"✅ Received: {symbol} @ {close_price} (Time: {current_time})")
                    sys.stdout.flush()  # Ensure output is printed immediately
                    # check_ma_p80_reentry_and_short()
                    # check_shortcycle_ma_reentry_and_short()
                    
                    # Respond success
                    self.send_response(200)
                    self.send_header('Content-type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b"Success")
                else:
                    self.send_error(400, "No Data Provided")

            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
            except Exception as e:
                print(f"Error processing webhook: {e}")
                sys.stdout.flush()
                self.send_error(500, f"Server Error: {str(e)}")
        else:
            self.send_error(404, "Not Found")

    def do_GET(self):
        # Health check
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Webhook Server Running")
        else:
            self.send_error(404, "Not Found")

def run_server():
    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    def _daily_clear_worker():
        last_clear_date = None
        while True:
            now = datetime.now(TZ)
            if (now.hour, now.minute) == CLEAR_TIME and last_clear_date != now.date():
                try:
                    if os.path.isfile(CSV_FILE):
                        os.remove(CSV_FILE)
                    print(f"🧹 Cleared CSV at {now.strftime('%Y-%m-%d %H:%M:%S')}")
                    sys.stdout.flush()
                except Exception as exc:
                    print(f"❌ Failed to clear CSV: {exc}")
                    sys.stdout.flush()
                last_clear_date = now.date()
            time.sleep(30)

    try:
        if os.path.isfile(CSV_FILE):
            os.remove(CSV_FILE)
        server_address = ('', PORT)
        httpd = ThreadingHTTPServer(server_address, WebhookHandler)
        Thread(target=_daily_clear_worker, daemon=True).start()
        print(f"🚀 Webhook server started on port {PORT}")
        print(f"📂 Saving data to: {CSV_FILE}")
        sys.stdout.flush()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    except Exception as e:
        print(f"DTO Fatal Error: {e}")
    finally:
        if 'httpd' in locals():
            httpd.server_close()
        print("Server stopped.")
        sys.stdout.flush()

if __name__ == '__main__':
    run_server()
