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

from auto_trade import _get_last_entry, send_discord_message, sellOne
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
    'MA_960',
    'MA_P80',
    'MA_P200',
    'MA_N110',
    'MA_N200',
]


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _round_int(value) -> str:
    number = _to_float(value)
    if number is None:
        return ""
    return str(int(round(number)))


def _to_int(value) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(round(number))


def _read_last_row(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        last_row = None
        for row in reader:
            last_row = row
    return last_row


def _read_last_two_rows(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return rows[-2:] if len(rows) >= 2 else rows


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

    # If header differs, rewrite file with new header and pad existing rows.
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


def check_ha_mxf_strategy(csv_path: str) -> None:
    rows = _read_last_two_rows(csv_path)
    if len(rows) < 2:
        return

    prev_row, curr_row = rows[-2], rows[-1]
    prev_ha_close = _to_int(prev_row.get("HA_Close"))
    prev_ha_open = _to_int(prev_row.get("HA_Open"))
    curr_ha_close = _to_int(curr_row.get("HA_Close"))
    curr_ha_open = _to_int(curr_row.get("HA_Open"))
    close = _to_int(curr_row.get("Close"))
    if None in (prev_ha_close, prev_ha_open, curr_ha_close, curr_ha_open):
        return

    mxf_row = _read_last_row(MXF_VALUE_PATH)
    if not mxf_row:
        return
    signal = str(mxf_row.get("signal", "")).strip().lower()

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
        positions = api.list_positions(api.futopt_account)

        current_side = None
        if positions:
            direction = positions[0].get("direction")
            if direction == "Buy":
                current_side = "bull"
            elif direction == "Sell":
                current_side = "bear"
        print(current_side, 'current_side')

        if current_side == "bull":
            if signal in {"bear", "none"} or curr_ha_close < curr_ha_open:
                if DRY_RUN:
                    send_discord_message_short(f'[{test_now:%H:%M:%S}] 模擬多單出場 (HA/MXF)[{_round_int(close)}]')
                else:
                    sell_one_short(api, contract)
                    send_discord_message_short(f'[{test_now:%H:%M:%S}] 多單出場 (HA/MXF)[{_round_int(close)}]')
            return

        if current_side == "bear":
            if signal in {"bull", "none"} or curr_ha_close > curr_ha_open:
                if DRY_RUN:
                    send_discord_message_short(f'[{test_now:%H:%M:%S}] 模擬空單出場 (HA/MXF)[{_round_int(close)}]')
                else:
                    buy_one_short(api, contract)
                    send_discord_message_short(f'[{test_now:%H:%M:%S}] 空單出場 (HA/MXF)[{_round_int(close)}]')
            return
        
        print(curr_ha_close < curr_ha_open, 'curr_ha_close < curr_ha_open')
        if curr_ha_close < curr_ha_open and signal == "bear" and len(positions) == 0:
            if DRY_RUN:
                send_discord_message_short(f'[{test_now:%H:%M:%S}] 模擬進場空單 (HA/MXF)[{_round_int(close)}]')
            else:
                sell_one_short(api, contract)
                send_discord_message_short(f'[{test_now:%H:%M:%S}] 進場空單 (HA/MXF)[{_round_int(close)}]')
            return
        
        print(curr_ha_close >= curr_ha_open, 'curr_ha_close >= curr_ha_open')
        if curr_ha_close >= curr_ha_open and signal == "bull" and len(positions) == 0:
            if DRY_RUN:
                send_discord_message_short(f'[{test_now:%H:%M:%S}] 模擬進場多單 (HA/MXF)[{_round_int(close)}]')
            else:
                buy_one_short(api, contract)
                send_discord_message_short(f'[{test_now:%H:%M:%S}] 進場多單 (HA/MXF)[{_round_int(close)}]')
            return
    except Exception as exc:
        print(f"❌ HA/MXF 策略下單失敗: {exc}")
    finally:
        try:
            api.logout()
        except Exception:
            pass


def _place_limit_order(api, contract, side: str, price: float, quantity: int = 1):
    if DRY_RUN:
        send_discord_message_short(
            f'[{datetime.now(TZ):%H:%M:%S}] 模擬委託 {side} LMT @ {_round_int(price)}'
        )
        return None
    order = api.Order(
        action=sj.constant.Action.Buy if side == "bull" else sj.constant.Action.Sell,
        price=price,
        quantity=quantity,
        price_type=sj.constant.FuturesPriceType.LMT,
        order_type=sj.constant.OrderType.ROD,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )
    return api.place_order(contract, order, timeout=0)


def _place_market_order(api, contract, side: str, quantity: int = 1):
    if DRY_RUN:
        send_discord_message_short(
            f'[{datetime.now(TZ):%H:%M:%S}] 模擬市價 {side}'
        )
        return None
    order = api.Order(
        action=sj.constant.Action.Buy if side == "bull" else sj.constant.Action.Sell,
        price=0,
        quantity=quantity,
        price_type=sj.constant.FuturesPriceType.MKT,
        order_type=sj.constant.OrderType.ROD,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )
    return api.place_order(contract, order, timeout=0)


def _update_or_replace_order(api, contract, trade, side: str, price: float):
    update_fn = getattr(api, "update_order", None)
    cancel_fn = getattr(api, "cancel_order", None)

    if DRY_RUN:
        send_discord_message_short(
            f'[{datetime.now(TZ):%H:%M:%S}] 模擬改價 {side} -> {_round_int(price)}'
        )
        return None

    if trade is not None and callable(update_fn):
        try:
            update_fn(trade, price=price)
            return trade
        except Exception:
            pass

    if trade is not None and callable(cancel_fn):
        try:
            cancel_fn(trade)
        except Exception:
            pass

    return _place_limit_order(api, contract, side, price)


def check_vwap_1min_strategy(payload: dict) -> None:
    timeframe = str(payload.get("timeframe", "")).strip()
    print(timeframe, 'timeframe')
    if timeframe != "1":
        return

    vwap = _to_float(payload.get("vwap"))
    close_price = _to_float(payload.get("close"))
    if vwap is None or close_price is None:
        return
    vwap = int(round(vwap))
    close_price = int(round(close_price))
    print(vwap, close_price)

    api_key = os.getenv("API_KEY2")
    secret_key = os.getenv("SECRET_KEY2")
    if not api_key or not secret_key:
        print("❌ 缺少 API_KEY2 或 SECRET_KEY2")
        return

    if not os.path.exists(CA_PATH):
        print(f"❌ 找不到憑證檔案，目前嘗試路徑為: {CA_PATH}")
        return

    with VWAP_LOCK:
        api = sj.Shioaji(simulation=True)
        api.login(api_key, secret_key)
        api.activate_ca(
            ca_path=CA_PATH,
            ca_passwd=os.getenv("PERSON_ID"),
            person_id=os.getenv("PERSON_ID"),
        )
        try:
            api.update_status()
            contract = api.Contracts.Futures.TMF.TMFR1
            positions = api.list_positions(api.futopt_account)

            current_side = None
            if positions:
                direction = positions[0].get("direction")
                if direction == "Buy":
                    current_side = "bull"
                elif direction == "Sell":
                    current_side = "bear"

            # If in position, check exit by VWAP cross using market order.
            if current_side == "bull" and close_price < vwap:
                if DRY_RUN:
                    send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 模擬多單出場 (VWAP)')
                else:
                    _place_market_order(api, contract, "bear")
                    VWAP_ORDER_STATE["side"] = None
                    VWAP_ORDER_STATE["price"] = None
                    VWAP_ORDER_STATE["trade"] = None
                    send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 多單出場 (VWAP)')
                return
            if current_side == "bear" and close_price > vwap:
                if DRY_RUN:
                    send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 模擬空單出場 (VWAP)')
                else:
                    _place_market_order(api, contract, "bull")
                    VWAP_ORDER_STATE["side"] = None
                    VWAP_ORDER_STATE["price"] = None
                    VWAP_ORDER_STATE["trade"] = None
                    send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 空單出場 (VWAP)')
                return

            # If no position, manage entry order.
            print(current_side, 'current_side')
            if current_side is None:
                desired_side = "bull" if close_price > vwap else "bear"
                desired_price = vwap + VWAP_OFFSET if desired_side == "bull" else vwap - VWAP_OFFSET

                trade = VWAP_ORDER_STATE.get("trade")
                current_order_side = VWAP_ORDER_STATE.get("side")
                current_order_price = VWAP_ORDER_STATE.get("price")

                if current_order_side != desired_side or current_order_price != desired_price:
                    if DRY_RUN:
                        send_discord_message_short(
                            f'[{datetime.now(TZ):%H:%M:%S}] 模擬掛單 {desired_side} @ {_round_int(desired_price)} (VWAP)'
                        )
                        VWAP_ORDER_STATE["side"] = desired_side
                        VWAP_ORDER_STATE["price"] = desired_price
                        VWAP_ORDER_STATE["trade"] = None
                    else:
                        trade = _update_or_replace_order(api, contract, trade, desired_side, desired_price)
                        VWAP_ORDER_STATE["side"] = desired_side
                        VWAP_ORDER_STATE["price"] = desired_price
                        VWAP_ORDER_STATE["trade"] = trade
                elif DRY_RUN:
                    send_discord_message_short(
                        f'[{datetime.now(TZ):%H:%M:%S}] 模擬維持委託 {desired_side} @ {_round_int(desired_price)} (VWAP)'
                    )
        except Exception as exc:
            print(f"❌ VWAP 策略下單失敗: {exc}")
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
                            _round_int(ma_960),
                            _round_int(ma_p80),
                            _round_int(ma_p200),
                            _round_int(ma_n110),
                            _round_int(ma_n200),
                        ])

                    
                    sys.stdout.flush()  # Ensure output is printed immediately
                    if timeframe == "5":
                        print(f"✅ Received: {symbol} @ {close_price} (Time: {current_time}, timeframe={timeframe})")
                        # check_ha_mxf_strategy(target_csv)
                    elif timeframe == "1":
                        check_vwap_1min_strategy(data)
                    
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
