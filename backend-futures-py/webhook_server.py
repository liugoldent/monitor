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
from auto_trade_shortCycle import _close_position_with_api as close_position_with_api_short
from auto_trade_shortCycle import buyOne as buy_one_short
from auto_trade_shortCycle import sellOne as sell_one_short
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
SQZMOM_SHORTCYCLE_STATE_FILE = os.path.join(os.path.dirname(__file__), "tv_doc", "sqzmom_shortCycle.json")
SHORTCYCLE_STATE_FILE = os.path.join(os.path.dirname(__file__), "tv_doc", "shortCycle.json")
H_TRADE_FLATTEN_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "h_trade_flatten.json")
API_CLIENT = None

DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}
H_TRADE_ADD_ON_LOSS_POINTS = 400.0
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
    'MA_960',
    'MA_P80',
    'MA_P200',
    'MA_N110',
    'MA_N200',
    'MA_230',
    'SQZ_POWER'
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


def _get_latest_1min_close(path: str = CSV_FILE_1MIN) -> float | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return None
    if not rows:
        return None
    return _to_float(rows[-1].get("Close"))


def _load_h_trade_flatten_state(path: str = H_TRADE_FLATTEN_PATH) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception:
        return {}


def _save_h_trade_flatten_state(state: dict, path: str = H_TRADE_FLATTEN_PATH) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _mark_h_trade_add_on(
    side: str,
    entry_price: float,
    trigger_close: float,
    loss_points: float,
) -> None:
    payload = {
        "side": side,
        "entry_price": float(entry_price),
        "add_on_done": True,
        "add_on_quantity": 1,
        "loss_points": float(loss_points),
        "trigger_close": float(trigger_close),
        "updated_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_h_trade_flatten_state(payload)


def _run_h_trade_loss_add_on() -> bool:
    latest_close_1min = _get_latest_1min_close()
    latest_trade_entry = _get_latest_trade_entry()
    if latest_close_1min is None or latest_trade_entry is None:
        return False

    trade_side, trade_entry_price = latest_trade_entry
    if trade_side == "bull":
        loss_points = trade_entry_price - latest_close_1min
    elif trade_side == "bear":
        loss_points = latest_close_1min - trade_entry_price
    else:
        return False

    if loss_points < H_TRADE_ADD_ON_LOSS_POINTS:
        return False

    flatten_state = _load_h_trade_flatten_state()
    added_side = str(flatten_state.get("side", "")).strip().lower()
    added_entry_price = _to_float(flatten_state.get("entry_price"))
    add_on_done = bool(flatten_state.get("add_on_done", False))
    if (
        add_on_done
        and added_side == trade_side
        and added_entry_price is not None
        and abs(added_entry_price - trade_entry_price) < 1e-9
    ):
        return False

    now_ts = datetime.now(TZ).strftime("%H:%M:%S")
    try:
        api = _get_api_client()
        contract = api.Contracts.Futures.TMF.TMFR1
        if trade_side == "bull":
            buy_one_short(api, contract, quantity=1)
            side_text = "多單"
        else:
            sell_one_short(api, contract, quantity=1)
            side_text = "空單"

        _mark_h_trade_add_on(
            side=trade_side,
            entry_price=trade_entry_price,
            trigger_close=latest_close_1min,
            loss_points=loss_points,
        )
        send_discord_message_short(
            f"[{now_ts}]：{int(latest_close_1min)} / h_trade 浮虧 {int(loss_points)} 點，加碼同向{side_text} 1 口"
        )
        return True
    except Exception as exc:
        send_discord_message_short(
            f"[{now_ts}] h_trade 浮虧加碼失敗：{exc}"
        )
        return False


def _has_shortcycle_position(path: str = SHORTCYCLE_STATE_FILE) -> bool:
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return False

    direction = str(payload.get("direction", "")).strip().lower()
    quantity = _to_float(payload.get("quantity"))
    return direction in {"bull", "bear", "buy", "sell"} and (quantity or 0) > 0


def _load_sqzmom_shortcycle_state():
    if not os.path.exists(SQZMOM_SHORTCYCLE_STATE_FILE):
        return {}
    try:
        with open(SQZMOM_SHORTCYCLE_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_sqzmom_shortcycle_state(state):
    try:
        with open(SQZMOM_SHORTCYCLE_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def _get_api_client():
    global API_CLIENT
    if API_CLIENT is not None:
        try:
            # 隨便測試一個不耗資源的請求，確認連線還活著
            API_CLIENT.list_positions(API_CLIENT.futopt_account)
            return API_CLIENT
        except:
            print("⚠️ API 連線已失效，嘗試重新登入...")
            API_CLIENT = None

    api = sj.Shioaji(simulation=False)
    api.login(os.getenv("API_KEY2"), os.getenv("SECRET_KEY2"))
    api.activate_ca(
        ca_path=CA_PATH,
        ca_passwd=os.getenv("PERSON_ID"),
        person_id=os.getenv("PERSON_ID"),
    )
    API_CLIENT = api
    return API_CLIENT


def _close_all_positions() -> None:
    if DRY_RUN:
        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 模擬平倉全部')
        return
    try:
        api = _get_api_client()
        close_position_with_api_short(api, datetime.now(TZ))
    except Exception as exc:
        send_discord_message_short(f"[{datetime.now(TZ):%H:%M:%S}] 平倉失敗：{exc}")


def _set_sqzmom_shortcycle_state(entry_side: str, action: str) -> None:
    state = _load_sqzmom_shortcycle_state()
    state["entry_side"] = entry_side
    state["last_action"] = action
    state["updated_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    _save_sqzmom_shortcycle_state(state)


def _clear_sqzmom_shortcycle_state() -> None:
    state = _load_sqzmom_shortcycle_state()
    state.pop("entry_side", None)
    state.pop("last_action", None)
    state["updated_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    _save_sqzmom_shortcycle_state(state)


def run_tx_mtx_heikin_strategy(csv_path: str) -> bool:
    rows = _read_last_two_rows(csv_path)
    if len(rows) < 1:
        return False

    curr_row = rows[-1]
    timeframe = str(curr_row.get("Timeframe", "")).strip()
    if timeframe != "5":
        return False

    curr_open = _to_float(curr_row.get("Open"))
    curr_close = _to_float(curr_row.get("Close"))
    ha_open = _to_float(curr_row.get("HA_Open"))
    ha_close = _to_float(curr_row.get("HA_Close"))
    if None in (curr_open, curr_close, ha_open, ha_close):
        return False

    now_ts = datetime.now(TZ).strftime("%H:%M:%S")
    state = _load_sqzmom_shortcycle_state()
    entry_side = str(state.get("entry_side", "")).strip().lower()

    # 出場條件：多單 HA 轉空、空單 HA 轉多
    if entry_side == "bull" and ha_close < ha_open:
        _clear_sqzmom_shortcycle_state()
        send_discord_message_short(
            f"[{now_ts}]：{int(curr_close)} / 對沖 多單出場"
        )
        _close_all_positions()
        return True

    if entry_side == "bear" and ha_close > ha_open:
        _clear_sqzmom_shortcycle_state()
        send_discord_message_short(
            f"[{now_ts}]：{int(curr_close)} / 對沖 空單出場"
        )
        _close_all_positions()
        return True

    # 已有持倉狀態就不再進場
    if entry_side in {"bull", "bear"}:
        return False

    # shortCycle.json 已有倉位就不進場
    if _has_shortcycle_position():
        return False

    # h策略賠錢訊號：多單賠錢訊號是當前價 < 多單進場價；空單賠錢訊號是當前價 > 空單進場價
    reverse_long_signal = False
    reverse_short_signal = False

    latest_close_1min = _get_latest_1min_close()
    latest_trade_entry = _get_latest_trade_entry()
    if latest_close_1min is not None and latest_trade_entry is not None:
        trade_side, trade_entry_price = latest_trade_entry
        if trade_side == "bull" and latest_close_1min < trade_entry_price:
            reverse_short_signal = True
        elif trade_side == "bear" and latest_close_1min > trade_entry_price:
            reverse_long_signal = True

    # 現在h多單賠錢，故進空單；
    if reverse_long_signal:
        reason = "h多單賠錢，故進空單"
        _set_sqzmom_shortcycle_state("bear", "enter")
        send_discord_message_short(
            f"[{now_ts}]：{int(curr_close)} / {reason}"
        )
        return True

    # 現在h空單賠錢，故進多單
    if reverse_short_signal:
        reason = "h空單賠錢，故進多單"
        _set_sqzmom_shortcycle_state("bull", "enter")
        send_discord_message_short(
            f"[{now_ts}]：{int(curr_close)} / {reason}"
        )
        return True

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
                print(f"Received webhook: {data}")

                if data:
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    symbol = data.get('symbol', 'Unknown')
                    timeframe = str(data.get('timeframe', '')).strip()
                    tv_time_ms = data.get('time', '')
                    open_price = data.get('open', '')
                    high_price = data.get('high', '')
                    low_price = data.get('low', '')
                    close_price = data.get('close', '')
                    ha_open = data.get('ha_open', '')
                    ha_close = data.get('ha_close', '')
                    ma_960 = data.get('ma_960', '')
                    ma_p80 = data.get('ma_p80', '')
                    ma_p200 = data.get('ma_p200', '')
                    ma_n110 = data.get('ma_n110', '')
                    ma_n200 = data.get('ma_n200', '')
                    ma_230 = data.get('ma_230', '')
                    sqz_power = data.get('sqz_power', '')

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
                            _round_int(ma_960),
                            _round_int(ma_p80),
                            _round_int(ma_p200),
                            _round_int(ma_n110),
                            _round_int(ma_n200),
                            _round_int(ma_230),
                            _round_int(sqz_power),
                        ])

                    
                    sys.stdout.flush()  # Ensure output is printed immediately
                    if timeframe == "5":
                        print(f"✅ Received: {symbol} @ {close_price} (Time: {current_time}, timeframe={timeframe})")
                        # run_tx_mtx_heikin_strategy(target_csv)
                    elif timeframe == "1":
                        _run_h_trade_loss_add_on()
                        print(f"✅ Received: {symbol} @ {close_price} (Time: {current_time}, timeframe={timeframe})")
                    
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
