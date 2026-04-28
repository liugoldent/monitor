import http.server
import json
import csv
import os
import sys
from datetime import datetime
import socketserver
import time
from threading import RLock, Thread
from zoneinfo import ZoneInfo

import shioaji as sj

from auto_trade import buyOne as long_buy_one
from auto_trade import sellOne as long_sell_one
from auto_trade import send_discord_message as long_send_discord_message
from auto_trade_shortCycle import auto_trade as shortcycle_auto_trade
from auto_trade_shortCycle import closePosition as shortcycle_close_position
from auto_trade_shortCycle import send_discord_message as shortcycle_send_discord_message

# Configuration
PORT = 8080
CSV_FILE_1MIN = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_1min.csv")
MXF_VALUE_CSV_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "mxf_value.csv")
H_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "h_trade.csv")
BB_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "bb_trade.csv")
BBR960_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "bbr960_trade.csv")
BBR960_STATE_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "bbr960_state.json")
CLEAR_TIME = (14, 0)
CLEAR_KEEP_ROWS = 5
TZ = ZoneInfo("Asia/Taipei")
LONG_CA_PATH = os.getenv("CA_PATH") or os.path.join(os.path.dirname(__file__), "Sinopac.pfx")

CSV_HEADER = [
    'Record Time',
    'Symbol',
    'Timeframe',
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
    'A1_State',
    'BBR',
]

STRATEGY_LOCK = RLock()
BBR960_ADD_THRESHOLD = 250.0
BBR960_PENDING_TIMEOUT_SECONDS = 10 * 60


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
    rows_to_keep: list[list[str]] = []
    if os.path.isfile(path):
        try:
            with open(path, "r", newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                rows = list(reader)
            if rows and rows[0]:
                header_to_write = rows[0]
                rows_to_keep = rows[1:][-CLEAR_KEEP_ROWS:] if CLEAR_KEEP_ROWS > 0 else []
        except Exception:
            header_to_write = header
            rows_to_keep = []

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header_to_write)
        writer.writerows(rows_to_keep)


def _read_last_n_rows(path: str, count: int) -> list[dict]:
    if count <= 0 or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return []
    return rows[-count:] if len(rows) >= count else rows


def _get_latest_h_trade_entry() -> tuple[str, float] | None:
    if not os.path.isfile(H_TRADE_LOG_PATH):
        return None
    try:
        with open(H_TRADE_LOG_PATH, "r", newline="", encoding="utf-8") as handle:
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


def _get_latest_mtx_bvav() -> float | None:
    if not os.path.isfile(MXF_VALUE_CSV_PATH):
        return None
    try:
        with open(MXF_VALUE_CSV_PATH, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return None

    for row in reversed(rows):
        value = _to_float(row.get("mtx_bvav"))
        if value is not None:
            return value
    return None


def _ensure_trade_log_header(path: str) -> None:
    header = ["timestamp", "action", "side", "price", "pnl", "quantity"]
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
        return

    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except Exception:
        return

    if not rows:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
        return

    if rows[0] == header:
        return

    data_rows = rows[1:]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(data_rows)


def _append_bb_trade(action: str, side: str, price: float, pnl: str = "", quantity: int = 1) -> None:
    _ensure_trade_log_header(BB_TRADE_LOG_PATH)
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(BB_TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([timestamp, action, side, price, pnl, quantity])


def _append_bbr960_trade(action: str, side: str, price: float, pnl: str = "", quantity: int = 1) -> None:
    _ensure_trade_log_header(BBR960_TRADE_LOG_PATH)
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(BBR960_TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([timestamp, action, side, price, pnl, quantity])


def _default_bbr960_state() -> dict:
    return {
        "pending_side": "",
        "pending_entry_price": "",
        "pending_since": "",
    }


def _load_bbr960_state() -> dict:
    state = _default_bbr960_state()
    if not os.path.isfile(BBR960_STATE_PATH):
        return state

    try:
        with open(BBR960_STATE_PATH, "r", encoding="utf-8") as handle:
            raw_state = json.load(handle)
        if isinstance(raw_state, dict):
            state.update({
                "pending_side": str(raw_state.get("pending_side", "")).strip().lower(),
                "pending_entry_price": raw_state.get("pending_entry_price", ""),
                "pending_since": str(raw_state.get("pending_since", "")).strip(),
            })
    except Exception:
        return state

    return state


def _save_bbr960_state(state: dict) -> None:
    os.makedirs(os.path.dirname(BBR960_STATE_PATH), exist_ok=True)
    with open(BBR960_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _clear_bbr960_pending_state() -> None:
    _save_bbr960_state(_default_bbr960_state())


def _mark_bbr960_pending(side: str, entry_price: float) -> None:
    state = {
        "pending_side": side,
        "pending_entry_price": entry_price,
        "pending_since": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_bbr960_state(state)


def _parse_bbr960_pending_since(raw_value: str) -> datetime | None:
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except ValueError:
        return None


def _bbr960_pending_matches_latest(state: dict, latest_entry: tuple[str, float]) -> bool:
    pending_side = str(state.get("pending_side", "")).strip().lower()
    pending_price = _to_float(state.get("pending_entry_price"))
    if not pending_side or pending_price is None:
        return False

    latest_side, latest_price = latest_entry
    return pending_side == latest_side and abs(pending_price - latest_price) < 1e-9


def _is_bbr960_add_blocked(state: dict, latest_entry: tuple[str, float]) -> bool:
    if not _bbr960_pending_matches_latest(state, latest_entry):
        if state.get("pending_side") or state.get("pending_entry_price"):
            _clear_bbr960_pending_state()
        return False

    pending_since = _parse_bbr960_pending_since(str(state.get("pending_since", "")).strip())
    if pending_since is None:
        return True

    elapsed_seconds = (datetime.now(TZ) - pending_since).total_seconds()
    if elapsed_seconds > BBR960_PENDING_TIMEOUT_SECONDS:
        _clear_bbr960_pending_state()
        return False
    return True


def _submit_bbr960_order(side: str, quantity: int = 1) -> None:
    api = None
    try:
        if not os.path.exists(LONG_CA_PATH):
            raise FileNotFoundError(f"找不到憑證檔案，目前嘗試路徑為: {LONG_CA_PATH}")

        api = sj.Shioaji(simulation=False)
        api.login(os.getenv("API_KEY"), os.getenv("SECRET_KEY"))
        api.activate_ca(
            ca_path=LONG_CA_PATH,
            ca_passwd=os.getenv("PERSON_ID"),
            person_id=os.getenv("PERSON_ID"),
        )

        contract = api.Contracts.Futures.TMF.TMFR1
        if side == "bull":
            long_buy_one(api, contract, quantity)
        else:
            long_sell_one(api, contract, quantity)

        long_send_discord_message(f"webhook_server: BBR960 已送出 {side} 加碼 1 口")
        print(f"🔔 BBR960 sent {side} order, quantity={quantity}")
    except Exception as exc:
        print(f"❌ BBR960 {side} order failed: {exc}")
        with STRATEGY_LOCK:
            _clear_bbr960_pending_state()
        raise
    finally:
        try:
            if api is not None:
                api.logout()
        except Exception:
            pass
        sys.stdout.flush()


def _trigger_shortcycle_trade(side: str, close_price: float, reason: str) -> None:
    def _runner() -> None:
        try:
            _append_bb_trade("enter", side, close_price)
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，即將觸發 shortCycle auto_trade({side})，原因：{reason}"
            )
            print(f"🔔 Trigger shortCycle auto_trade({side}) because {reason}")
            shortcycle_auto_trade(side)
        except Exception as exc:
            print(f"❌ shortCycle auto_trade({side}) failed: {exc}")
        finally:
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _trigger_shortcycle_close(side: str, close_price: float, reason: str) -> None:
    def _runner() -> None:
        try:
            _append_bb_trade("exiting", side, close_price)
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，即將觸發 shortCycle closePosition，原因：{reason}"
            )
            print(f"🔔 Trigger shortCycle closePosition because {reason}")
            shortcycle_close_position()
        except Exception as exc:
            print(f"❌ shortCycle closePosition failed: {exc}")
        finally:
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _trigger_bbr960_trade(side: str, close_price: float, reason: str) -> None:
    def _runner() -> None:
        try:
            _append_bbr960_trade("enter", side, close_price)
            long_send_discord_message(
                f"webhook_server: close={close_price}，即將觸發 BBR960 auto_trade({side})，原因：{reason}"
            )
            print(f"🔔 Trigger BBR960 auto_trade({side}) because {reason}")
            _submit_bbr960_order(side, 1)
        except Exception as exc:
            print(f"❌ BBR960 auto_trade({side}) failed: {exc}")
            with STRATEGY_LOCK:
                _clear_bbr960_pending_state()
        finally:
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _apply_bbr_strategy() -> bool:
    with STRATEGY_LOCK:
        rows = _read_last_n_rows(CSV_FILE_1MIN, 2)
        if len(rows) < 2:
            return False

        prev_row, curr_row = rows[-2], rows[-1]
        prev_bbr = _to_float(prev_row.get("BBR"))
        curr_bbr = _to_float(curr_row.get("BBR"))
        curr_close = _to_float(curr_row.get("Close"))
        if prev_bbr is None or curr_bbr is None or curr_close is None:
            return False

        latest_entry = _get_latest_h_trade_entry()
        if latest_entry is None:
            return False

        entry_side, entry_price = latest_entry
        in_profit = (curr_close > entry_price) if entry_side == "bull" else (curr_close < entry_price)

        if entry_side == "bull":
            if prev_bbr < 0 < curr_bbr and in_profit:
                _trigger_shortcycle_trade("bull", curr_close, f"BBR crossed {prev_bbr} -> {curr_bbr} and bull is profitable")
                return True
            if prev_bbr > 1 and curr_bbr < 1:
                _trigger_shortcycle_close("bull", curr_close, f"BBR pulled back {prev_bbr} -> {curr_bbr} after bull run")
                return True
            if prev_bbr > 0 > curr_bbr:
                _trigger_shortcycle_close("bull", curr_close, f"BBR flipped negative {prev_bbr} -> {curr_bbr} for bull stop loss")
                return True
            return False

        if entry_side == "bear":
            if prev_bbr > 0 > curr_bbr and in_profit:
                _trigger_shortcycle_trade("bear", curr_close, f"BBR crossed {prev_bbr} -> {curr_bbr} and bear is profitable")
                return True
            if prev_bbr < -1 and curr_bbr > -1:
                _trigger_shortcycle_close("bear", curr_close, f"BBR pulled back {prev_bbr} -> {curr_bbr} after bear run")
                return True
            if prev_bbr < 0 < curr_bbr:
                _trigger_shortcycle_close("bear", curr_close, f"BBR flipped positive {prev_bbr} -> {curr_bbr} for bear stop loss")
                return True
            return False

        return False


def _apply_bbr960_strategy() -> bool:
    with STRATEGY_LOCK:
        rows = _read_last_n_rows(CSV_FILE_1MIN, 2)
        if len(rows) < 2:
            return False

        prev_row, curr_row = rows[-2], rows[-1]
        prev_bbr = _to_float(prev_row.get("BBR"))
        curr_bbr = _to_float(curr_row.get("BBR"))
        curr_close = _to_float(curr_row.get("Close"))
        if prev_bbr is None or curr_bbr is None or curr_close is None:
            return False

        latest_entry = _get_latest_h_trade_entry()
        if latest_entry is None:
            return False

        state = _load_bbr960_state()
        if _is_bbr960_add_blocked(state, latest_entry):
            return False

        entry_side, entry_price = latest_entry
        in_profit = (curr_close > entry_price) if entry_side == "bull" else (curr_close < entry_price)
        profit_points = (curr_close - entry_price) if entry_side == "bull" else (entry_price - curr_close)
        latest_mtx_bvav = _get_latest_mtx_bvav()

        if entry_side == "bull":
            if (
                curr_bbr < 0
                and curr_bbr > prev_bbr
                and in_profit
                and profit_points >= BBR960_ADD_THRESHOLD
                and latest_mtx_bvav is not None
                and latest_mtx_bvav > -2000
            ):
                _mark_bbr960_pending(entry_side, entry_price)
                _trigger_bbr960_trade(
                    "bull",
                    curr_close,
                    f"BBR is still below 0 and rebounding {prev_bbr} -> {curr_bbr}; bull profit is {profit_points:.2f}; mtx_bvav={latest_mtx_bvav}",
                )
                return True
            return False

        if entry_side == "bear":
            if (
                curr_bbr > 0
                and curr_bbr < prev_bbr
                and in_profit
                and profit_points >= BBR960_ADD_THRESHOLD
                and latest_mtx_bvav is not None
                and latest_mtx_bvav < 2000
            ):
                _mark_bbr960_pending(entry_side, entry_price)
                _trigger_bbr960_trade(
                    "bear",
                    curr_close,
                    f"BBR is still above 0 and rolling over {prev_bbr} -> {curr_bbr}; bear profit is {profit_points:.2f}; mtx_bvav={latest_mtx_bvav}",
                )
                return True
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
                print(f"Received webhook: {data}")

                if data:
                    current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
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
                    a1_state = data.get('a1_state', '')
                    bbr = data.get('bbr', '')

                    tv_time = ""
                    try:
                        if tv_time_ms:
                            tv_time = datetime.fromtimestamp(int(tv_time_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        tv_time = str(tv_time_ms)

                    if timeframe != "1":
                        self.send_error(400, f"Unsupported timeframe: {timeframe}")
                        return

                    target_csv = CSV_FILE_1MIN

                    _ensure_csv_header(target_csv, CSV_HEADER)

                    with open(target_csv, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        writer.writerow([
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
                            a1_state,
                            bbr,
                        ])

                    _apply_bbr_strategy()
                    _apply_bbr960_strategy()
                    sys.stdout.flush()  # Ensure output is printed immediately
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
                    print(
                        f"🧹 Trimmed CSV to last {CLEAR_KEEP_ROWS} rows at "
                        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
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
