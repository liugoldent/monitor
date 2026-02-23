import http.server
import json
import csv
import os
import sys
from datetime import datetime
from threading import Thread
import socketserver
import time
import threading
from zoneinfo import ZoneInfo

from auto_trade_shortCycle import send_discord_message as send_discord_message_short
from auto_trade_shortCycle import sellOne as sell_one_short
from auto_trade_shortCycle import buyOne as buy_one_short
import shioaji as sj

# Configuration
PORT = 8080
CSV_FILE = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_5min.csv")
CSV_FILE_1MIN = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_1min.csv")
CSV_FILE_5MIN = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_5min.csv")
CLEAR_TIME = (14, 0)
TZ = ZoneInfo("Asia/Taipei")
TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "h_trade.csv")
CA_PATH = os.getenv("CA_PATH") or os.path.join(os.path.dirname(__file__), "Sinopac.pfx")
MXF_VALUE_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "mxf_value.csv")
FUTURE_VALUE_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "future_max_values.json")

VWAP_OFFSET = 5
VWAP_ORDER_STATE = {"side": None, "price": None, "trade": None}
VWAP_LOCK = threading.Lock()
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}
CSV_HEADER = [
    'Record Time',
    'Symbol',
    'Timeframe',
    'TradingView Time',
    'Open',
    'High',
    'Low',
    'Close',
    'HA_Open',
    'HA_Close',
    'VWAP',
    'VWAP_Upper',
    'VWAP_Lower',
    'MA_960',
    'MA_P80',
    'MA_P200',
    'MA_N110',
    'MA_N200',
]


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        cleaned = str(value).replace(",", "").strip()
        if not cleaned:
            return None
        return float(cleaned)
    except ValueError:
        return None
    
def _parse_number(raw: str) -> float | None:
    if raw is None:
        return None
    text = str(raw).replace(",", "").strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None

def _get_future_max_values() -> tuple[float | None, float | None]:
    if not FUTURE_VALUE_PATH.exists():
        return None, None
    try:
        payload = json.loads(FUTURE_VALUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    max_buy = _parse_number(payload.get("maxBuyValue"))
    max_sell = _parse_number(payload.get("maxSellValue"))
    return max_buy, max_sell


def _round_int(value) -> str:
    number = _to_float(value)
    if number is None:
        return ""
    return str(int(round(number)))


def _ensure_csv_header(path: str, header: list[str]) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
    except Exception:
        return

    if not rows:
        return

    current_header = rows[0]
    if current_header == header:
        return

    data_rows = rows[1:]
    normalized_rows: list[list[str]] = []
    for row in data_rows:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        elif len(row) > len(header):
            row = row[:len(header)]
        normalized_rows.append(row)

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(normalized_rows)


def _clear_csv_keep_header(path: str, header: list[str]) -> None:
    header_to_write = header
    if os.path.isfile(path):
        try:
            with open(path, "r", newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                rows = list(reader)
            if rows and rows[0]:
                header_to_write = rows[0]
        except Exception:
            header_to_write = header

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header_to_write)


MA960_STATE_FILE = os.path.join(os.path.dirname(__file__), "tv_doc", "ma960_state.json")
API_CLIENT = None


def _read_last_two_rows(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return rows[-2:] if len(rows) >= 2 else rows


def _get_latest_trade_entry() -> tuple[str, float] | None:
    if not os.path.isfile(TRADE_LOG_PATH):
        return None
    try:
        with open(TRADE_LOG_PATH, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return None

    for row in reversed(rows):
        action = str(row.get("action", "")).strip().lower()
        side = str(row.get("side", "")).strip().lower()
        price = _to_float(row.get("price"))
        if action == "enter" and side in {"bull", "bear"} and price is not None:
            return side, price
    return None


def _load_ma960_state():
    if not os.path.exists(MA960_STATE_FILE):
        return {}
    try:
        with open(MA960_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_ma960_state(state):
    try:
        with open(MA960_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def _get_api_client():
    global API_CLIENT
    if API_CLIENT is not None:
        return API_CLIENT
    api = sj.Shioaji(simulation=False)
    api.login(os.getenv("API_KEY2"), os.getenv("SECRET_KEY2"))
    api.activate_ca(
        ca_path=CA_PATH,
        ca_passwd=os.getenv("PERSON_ID"),
        person_id=os.getenv("PERSON_ID"),
    )
    try:
        api.update_status()
    except Exception:
        pass

    return api


def _has_position() -> bool | None:
    try:
        api = _get_api_client()
        positions = api.list_positions(api.futopt_account)
        return bool(positions)
    except Exception:
        return None


def _set_ma960_state(side: str, action: str, quantity: int | None = None) -> None:
    state = _load_ma960_state()
    state["ma960_side"] = side
    state["ma960_last_action"] = action
    if quantity is not None:
        try:
            state["ma960_order_quantity"] = int(quantity)
        except Exception:
            state["ma960_order_quantity"] = quantity
    _save_ma960_state(state)


def _clear_ma960_state() -> None:
    state = _load_ma960_state()
    state.pop("ma960_side", None)
    state.pop("ma960_last_action", None)
    state.pop("ma960_order_quantity", None)
    _save_ma960_state(state)


def _close_all_positions() -> None:
    if DRY_RUN:
        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 模擬平倉全部')
        return
    try:
        api = _get_api_client()
        positions = api.list_positions(api.futopt_account)
        if not positions:
            return
        contract = api.Contracts.Futures.TMF.TMFR1
        for pos in positions:
            direction = pos.get("direction")
            qty = pos.get("quantity") or pos.get("qty") or pos.get("amount") or 1
            try:
                qty = int(qty)
            except Exception:
                qty = 1

            if direction == "Buy":
                sell_one_short(api, contract, quantity=qty)
            elif direction == "Sell":
                buy_one_short(api, contract, quantity=qty)
    except Exception as exc:
        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 平倉失敗：{exc}')


def run_h_trade_ma960_strategy(csv_path: str) -> bool:
    rows = _read_last_two_rows(csv_path)
    if len(rows) < 2:
        return False

    prev_row, curr_row = rows[-2], rows[-1]
    timeframe = str(curr_row.get("Timeframe", "")).strip()
    if timeframe != "1":
        return False

    prev_close = _to_float(prev_row.get("Close"))
    curr_close = _to_float(curr_row.get("Close"))
    prev_ma_960 = _to_float(prev_row.get("MA_960"))
    curr_ma_960 = _to_float(curr_row.get("MA_960"))
    print(f"檢查 MA960 策略：prev_close={prev_close}, curr_close={curr_close}, prev_ma_960={prev_ma_960}, curr_ma_960={curr_ma_960}")
    if None in (prev_close, curr_close, prev_ma_960, curr_ma_960):
        return False

    latest_entry = _get_latest_trade_entry()
    if latest_entry is None:
        return False
    trade_side, entry_price = latest_entry
    print(f"最新交易紀錄：side={trade_side}, entry_price={entry_price}")

    now_ts = datetime.now(TZ).strftime("%H:%M:%S")
    state = _load_ma960_state()
    ma960_side = str(state.get("ma960_side", "")).strip().lower()
    has_position = _has_position()
    print(ma960_side, 'ma960_side')
    print(has_position, 'has_position')
    print(trade_side, 'trade_side')

    # if ma960_side not in {"bull", "bear"} and trade_side in {"bull", "bear"}:
    #     _set_ma960_state(trade_side, "sync")
    #     ma960_side = trade_side

    # 先檢查停損：多單跌破 MA960 / 空單站上 MA960
    if ma960_side == "bull" and curr_close < curr_ma_960:
        _close_all_positions()
        _clear_ma960_state()
        send_discord_message_short(
            f"[{now_ts}] MA960 出場：多單跌破 MA960 ({int(curr_close)} < {int(curr_ma_960)})"
        )

    if ma960_side == "bear" and curr_close > curr_ma_960:
        _close_all_positions()
        _clear_ma960_state()
        send_discord_message_short(
            f"[{now_ts}] MA960 出場：空單站上 MA960 ({int(curr_close)} > {int(curr_ma_960)})"
        )

    # 若已無倉位，清掉 ma960 狀態
    # if has_position is False and ma960_side in {"bull", "bear"}:
    #     print(33)
    #     _clear_ma960_state()

    # 平倉後，再檢查一次，以免出現停損後立刻加碼的情況
    ma960_side = str(state.get("ma960_side", "")).strip().lower()
    has_position = _has_position()
    print(ma960_side not in {"bull", "bear"}, 'ma960_side not in {"bull", "bear"}')
    print(has_position, 'has_position')

    if trade_side == "bull" and ma960_side not in {"bull", "bear"}:
        is_profit = curr_close > entry_price
        add_signal = prev_close < prev_ma_960 and curr_close > curr_ma_960 # 加碼單
        reverse_signal = prev_close > prev_ma_960 and curr_close < curr_ma_960 # 反向單

        print(f'現在H策略：{"bull"}')
        print(f'賺錢嗎？{is_profit}')
        print(f'加碼訊號？{add_signal}')
        print(f'反向訊號？{reverse_signal}')

        if is_profit and add_signal:
            try:
                send_discord_message_short(
                    f"[{now_ts}] MA960 加碼多單：close({int(curr_close)}) > entry({int(entry_price)}) 且上穿 MA960"
                )
                api = _get_api_client()
                contract = api.Contracts.Futures.TMF.TMFR1
                buy_one_short(api, contract, quantity=1)
                _set_ma960_state("bull", "add", quantity=1)
                return True
            except Exception as exc:
                send_discord_message_short(f"[{now_ts}] MA960 加碼多單失敗：{exc}")
                return False

        if (not is_profit) and reverse_signal:
            reverse_qty = 2 if has_position else 1
            try:
                send_discord_message_short(
                    f"[{now_ts}] MA960 反向空單：close({int(curr_close)}) < entry({int(entry_price)}) 且下破 MA960"
                )
                api = _get_api_client()
                contract = api.Contracts.Futures.TMF.TMFR1
                sell_one_short(api, contract, quantity=reverse_qty)
                _set_ma960_state("bear", "reverse", quantity=reverse_qty)
                return True
            except Exception as exc:
                send_discord_message_short(f"[{now_ts}] MA960 反向空單失敗：{exc}")
                return False

    if trade_side == "bear" and ma960_side not in {"bull", "bear"}:
        is_profit = curr_close < entry_price
        add_signal = prev_close > prev_ma_960 and curr_close < curr_ma_960
        reverse_signal = prev_close < prev_ma_960 and curr_close > curr_ma_960

        print(f'現在H策略：{"bear"}')
        print(f'賺錢嗎？{is_profit}')
        print(f'加碼訊號？{add_signal}')
        print(f'反向訊號？{reverse_signal}')

        if is_profit and add_signal:
            try:
                send_discord_message_short(
                    f"[{now_ts}] MA960 加碼空單：close({int(curr_close)}) < entry({int(entry_price)}) 且下破 MA960"
                )
                api = _get_api_client()
                contract = api.Contracts.Futures.TMF.TMFR1
                sell_one_short(api, contract, quantity=1)
                _set_ma960_state("bear", "add", quantity=1)
                return True
            except Exception as exc:
                send_discord_message_short(f"[{now_ts}] MA960 加碼空單失敗：{exc}")
                return False

        if (not is_profit) and reverse_signal:
            reverse_qty = 2 if has_position else 1
            try:
                send_discord_message_short(
                    f"[{now_ts}] MA960 反向多單：close({int(curr_close)}) > entry({int(entry_price)}) 且上穿 MA960"
                )
                api = _get_api_client()
                contract = api.Contracts.Futures.TMF.TMFR1
                buy_one_short(api, contract, quantity=reverse_qty)
                _set_ma960_state("bull", "reverse", quantity=reverse_qty)
                return True
            except Exception as exc:
                send_discord_message_short(f"[{now_ts}] MA960 反向多單失敗：{exc}")
                return False

    return False


# 獲取webhook並處理
class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/webhook':
            try:
                # Get content length
                content_length = int(self.headers.get('Content-Length', 0))
                
                # Read body
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)

                if data:
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    symbol = data.get('symbol', 'Unknown')
                    timeframe = str(data.get('timeframe', '')).strip()
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
                    ha_open = data.get('ha_open', '')
                    ha_close = data.get('ha_close', '')
                    vwap = data.get('vwap', '')
                    vwap_upper = data.get('vwap_upper', '')
                    vwap_lower = data.get('vwap_lower', '')

                    tv_time = ""
                    try:
                        if tv_time_ms:
                            tv_time = datetime.fromtimestamp(int(tv_time_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        tv_time = str(tv_time_ms)

                    if timeframe == "5":
                        target_csv = CSV_FILE_5MIN
                    elif timeframe == "1":
                        target_csv = CSV_FILE_1MIN
                    else:
                        target_csv = CSV_FILE

                    _ensure_csv_header(target_csv, CSV_HEADER)

                    file_exists = os.path.isfile(target_csv)
                    with open(target_csv, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        if not file_exists:
                            writer.writerow(CSV_HEADER)
                        writer.writerow([
                            current_time,
                            symbol,
                            timeframe,
                            tv_time,
                            _round_int(open_price),
                            _round_int(high_price),
                            _round_int(low_price),
                            _round_int(close_price),
                            _round_int(ha_open),
                            _round_int(ha_close),
                            _round_int(vwap),
                            _round_int(vwap_upper),
                            _round_int(vwap_lower),
                            _round_int(ma_960),
                            _round_int(ma_p80),
                            _round_int(ma_p200),
                            _round_int(ma_n110),
                            _round_int(ma_n200),
                        ])

                    
                    sys.stdout.flush()  # Ensure output is printed immediately
                    if timeframe == "5":
                        print(f"✅ Received: {symbol} @ {close_price} (Time: {current_time}, timeframe={timeframe})")
                    elif timeframe == "1":
                        run_h_trade_ma960_strategy(target_csv)
                    
                    # Respond success
                    self.send_response(200)
                    self.send_header('Content-type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b"Success")
                else:
                    self.send_error(400, "No Data Provided")

            except json.JSONDecodeError as e:
                print(f"❌ JSON Decode Error: {e}")
                print(f"❌ Raw Body: {body}")
                sys.stdout.flush()
                self.send_error(400, f"Invalid JSON: {e}")
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
                    _clear_csv_keep_header(CSV_FILE_1MIN, CSV_HEADER)
                    _clear_csv_keep_header(CSV_FILE_5MIN, CSV_HEADER)
                    print(f"🧹 Cleared CSV at {now.strftime('%Y-%m-%d %H:%M:%S')}")
                    sys.stdout.flush()
                except Exception as exc:
                    print(f"❌ Failed to clear CSV: {exc}")
                    sys.stdout.flush()
                last_clear_date = now.date()
            time.sleep(30)

    try:
        server_address = ('', PORT)
        httpd = ThreadingHTTPServer(server_address, WebhookHandler)
        Thread(target=_daily_clear_worker, daemon=True).start()
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
