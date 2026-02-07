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


VWAP_STATE_FILE = os.path.join(os.path.dirname(__file__), "tv_doc", "vwap_state.json")

def _to_int(value) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(round(number))

def _read_last_two_rows(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return rows[-2:] if len(rows) >= 2 else rows

def _load_vwap_state():
    if not os.path.exists(VWAP_STATE_FILE):
        return {}
    try:
        with open(VWAP_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_vwap_state(state):
    try:
        with open(VWAP_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass

def check_vwap_1min_strategy(csv_path: str) -> None:
    rows = _read_last_two_rows(csv_path)
    if len(rows) < 2:
        return

    prev_row, curr_row = rows[-2], rows[-1]
    timeframe = str(curr_row.get("Timeframe", "")).strip()
    if timeframe != "1":
        return

    prev_close = _to_int(prev_row.get("Close"))
    prev_vwap = _to_int(prev_row.get("VWAP"))
    prev_vwap_upper = _to_int(prev_row.get("VWAP_Upper"))
    prev_vwap_lower = _to_int(prev_row.get("VWAP_Lower"))

    curr_close = _to_int(curr_row.get("Close"))
    curr_vwap = _to_int(curr_row.get("VWAP"))
    curr_vwap_upper = _to_int(curr_row.get("VWAP_Upper"))
    curr_vwap_lower = _to_int(curr_row.get("VWAP_Lower"))

    if None in (
        prev_close,
        prev_vwap,
        prev_vwap_upper,
        prev_vwap_lower,
        curr_close,
        curr_vwap,
        curr_vwap_upper,
        curr_vwap_lower,
    ):
        return

    # Signal uses previous bar; execution uses current bar
    close_prev = prev_close
    vwap_prev = prev_vwap
    vwap_upper_prev = prev_vwap_upper
    vwap_lower_prev = prev_vwap_lower

    close_r = curr_close
    vwap_r = curr_vwap
    vwap_upper_r = curr_vwap_upper
    vwap_lower_r = curr_vwap_lower

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
                direction = getattr(positions[0], "direction", None)
                if direction == "Buy":
                    current_side = "bull"
                elif direction == "Sell":
                    current_side = "bear"
            
            print(current_side, 'current_side')

            state = _load_vwap_state()
            if current_side is None and state.get("position_side") is not None:
                state = {} # Reset state if position is gone
                _save_vwap_state(state)
            
            # --- EXIT LOGIC ---
            current_side == "bull"
            if DRY_RUN:
                send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 心跳：當前模擬持倉方向: {current_side}, 策略狀態: {state}')

            if current_side == "bull":
                strategy = state.get("strategy")
                
                should_exit = False
                exit_reason = ""
                
                # 策略A：出場條件：停利 (TP)：當收盤價接近 VWAP 上方區間的上緣 (VWAP Upper) 時，進行停利，出場價格為 VWAP 上緣 - 5 點。停損 (SL)：當收盤價跌破 VWAP 時，進行停損，出場價格為 VWAP。
                # 上區間做多出場
                if strategy == "A":
                    if close_r >= vwap_upper_r - 5:
                        should_exit = True; exit_reason = "停利：收盤價接近 VWAP 上方區間的上緣"
                    elif close_r < vwap_r:
                        should_exit = True; exit_reason = "停損：收盤價跌破 VWAP"
                
                # 策略B：出場條件：停利/停損 (TP/SL)：當收盤價跌破 VWAP 上方區間的上緣 (VWAP Upper) 時，無論是停利還是停損，都在同一個價格點位，出場價格為 VWAP 上緣。
                # 上上區間出場
                elif strategy == "B":
                    if close_r < vwap_upper_r:
                        should_exit = True; exit_reason = "收平：收盤價跌破 VWAP 上方區間的上緣"

                # 策略C：出場條件：停利 (TP)：當收盤價接近 VWAP 的下緣 (VWAP) 時，進行停利，出場價格為 VWAP - 5 點。停損 (SL)：當收盤價繼續跌下去，跌破 VWAP 的下緣 (VWAP Lower) 時，進行停損，出場價格為 VWAP 下緣。
                # 下區間做多出場
                elif strategy == "C":
                    if close_r >= vwap_r - 5:
                        should_exit = True; exit_reason = "停利：收盤價接近 VWAP 的下緣"
                    elif close_r < vwap_lower_r:
                        should_exit = True; exit_reason = "停損：收盤繼續跌下去，跌破 VWAP 下緣"

                else: 
                     pass

                if should_exit:
                    if DRY_RUN:
                        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 模擬多單出場 ({exit_reason}) @ {close_r}')
                    else:
                        _place_market_order(api, contract, "bear") # Sell to close
                        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 多單出場 ({exit_reason}) @ {close_r}')
                    _save_vwap_state({})
                    VWAP_ORDER_STATE.clear()
                    return

            if current_side == "bear":
                strategy = state.get("strategy")
                
                should_exit = False
                exit_reason = ""
                
                # 下區間做空出場
                if strategy == "A":
                    if close_r <= vwap_lower_r + 5:
                        should_exit = True; exit_reason = "停利：收盤價接近 VWAP 的下緣"
                    elif close_r > vwap_r:
                        should_exit = True; exit_reason = "停損：收盤價突破 VWAP 上方區間的上緣"
                
                # 下下區間做空出場
                elif strategy == "B":
                    if close_r > vwap_lower_r:
                        should_exit = True; exit_reason = "收平：收盤價站上 VWAP 下方區間的下緣"

                # 策略C：出場條件：停利 (TP)：當收盤價接近 VWAP 的上緣 (VWAP) 時，進行停利，出場價格為 VWAP + 5 點。停損 (SL)：當收盤價繼續漲上去，突破進場價格的高點時，進行停損，出場價格為進場價格的高點。
                # 上區間做空出場
                elif strategy == "C":
                    if close_r <= vwap_r + 5:
                        should_exit = True; exit_reason = "停利：收盤價接近 VWAP 的上緣"
                    elif close_r > vwap_upper_r:
                        should_exit = True; exit_reason = "停損：收盤價突破 VWAP 上方區間的上緣"

                if should_exit:
                    if DRY_RUN:
                        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 模擬空單出場 ({exit_reason}) @ {close_r}')
                    else:
                        _place_market_order(api, contract, "bull") # Buy to close
                        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 空單出場 ({exit_reason}) @ {close_r}')
                    _save_vwap_state({})
                    VWAP_ORDER_STATE.clear()
                    return
            print(current_side, 'after exit logic')
            # --- ENTRY LOGIC ---
            if current_side is None:
                # Determine Desired Entry
                desired_side = None
                desired_price = None
                detected_strategy = None
                
                # LONG Logic
                # 策略A：突破 (Breakout)：當收盤價站上 VWAP，且在 VWAP 上方區間內，視為突破，進場價格為 VWAP + 5 點。
                # 上區間做多
                if close_prev < vwap_prev and close_r > vwap_r:
                    detected_strategy = "A"
                    desired_side = "bull"
                    desired_price = vwap_r + 5
                
                # 策略B：追價 (Chase)：當收盤價站上 VWAP Upper，視為強勢追價訊號，進場價格為 VWAP Upper + 5 點。
                # 上上區間做多
                elif close_prev < vwap_upper_prev and close_r > vwap_upper_r:
                    detected_strategy = "B"
                    desired_side = "bull"
                    desired_price = vwap_upper_r + 5
                    
                # 策略C：反彈 (Rebound)：當收盤價由下往上穿越 VWAP Lower，視為反彈，進場價格為 VWAP Lower + 8 點。
                # 下區間做多
                elif close_prev < vwap_lower_prev and close_r > vwap_lower_r:
                    detected_strategy = "C"
                    desired_side = "bull"
                    desired_price = vwap_lower_r + 8
                
                # SHORT Logic (Overwrites Long if signals conflict, though distinct zones help)
                # 策略A：突破 (Breakdown)：當收盤價站下 VWAP，且在 VWAP 下方區間內，視為突破，進場價格為 VWAP - 5 點。
                # 下區間做空
                if close_prev > vwap_prev and close_r < vwap_r:
                    detected_strategy = "A"
                    desired_side = "bear"
                    desired_price = vwap_r - 5
                
                # 策略B：當收盤價站下 VWAP Lower，視為強勢追價訊號，進場價格為 VWAP Lower - 5 點。
                # 下下區間做空
                elif close_prev > vwap_lower_prev and close_r < vwap_lower_r:
                    detected_strategy = "B"
                    desired_side = "bear"
                    desired_price = vwap_lower_r - 5
                    
                # 策略C：反彈 (Rebound)：當收盤價由上往下穿越 VWAP Upper，視為反彈，進場價格為 VWAP Upper - 8 點。
                # 上區間做空
                elif close_prev > vwap_upper_prev and close_r < vwap_upper_r:
                    detected_strategy = "C"
                    desired_side = "bear"
                    desired_price = vwap_upper_r - 5

                # execute entry pending order update
                trade = VWAP_ORDER_STATE.get("trade")
                current_order_side = VWAP_ORDER_STATE.get("side")
                current_order_price = VWAP_ORDER_STATE.get("price")

                if desired_side and desired_price:
                   desired_price = int(desired_price)
                   if current_order_side != desired_side or current_order_price != desired_price:
                        if DRY_RUN:
                            send_discord_message_short(
                                f'[{datetime.now(TZ):%H:%M:%S}] 模擬掛單 {desired_side} @ {desired_price} (VWAP-{detected_strategy})'
                            )
                            trade = _update_or_replace_order(api, contract, trade, desired_side, desired_price)
                            VWAP_ORDER_STATE["side"] = desired_side
                            VWAP_ORDER_STATE["price"] = desired_price
                            VWAP_ORDER_STATE["trade"] = "SIMULATED_TRADE"
                        else:
                            trade = _update_or_replace_order(api, contract, trade, desired_side, desired_price)
                            VWAP_ORDER_STATE["side"] = desired_side
                            VWAP_ORDER_STATE["price"] = desired_price
                            VWAP_ORDER_STATE["trade"] = trade
                        
                        _save_vwap_state({
                            "strategy": detected_strategy, 
                            "position_side": desired_side
                        })
                   elif DRY_RUN:
                        send_discord_message_short(
                            f'[{datetime.now(TZ):%H:%M:%S}] 模擬維持委託 {desired_side} @ {desired_price} (VWAP-{detected_strategy})'
                        )
                else:
                    pass

        except Exception as exc:
            print(f"❌ VWAP 策略下單失敗: {exc}")
        finally:
            try:
                api.logout()
            except Exception:
                pass


def notify_vwap_cross_signals(csv_path: str) -> None:
    rows = _read_last_two_rows(csv_path)
    if len(rows) < 2:
        return

    prev_row, curr_row = rows[-2], rows[-1]
    timeframe = str(curr_row.get("Timeframe", "")).strip()
    if timeframe != "1":
        return

    prev_close = _to_int(prev_row.get("Close"))
    curr_close = _to_int(curr_row.get("Close"))

    prev_vwap = _to_int(prev_row.get("VWAP"))
    vwap = _to_int(curr_row.get("VWAP"))

    prev_vwap_upper = _to_int(prev_row.get("VWAP_Upper"))
    vwap_upper = _to_int(curr_row.get("VWAP_Upper"))
    
    prev_vwap_lower = _to_int(prev_row.get("VWAP_Lower"))
    vwap_lower = _to_int(curr_row.get("VWAP_Lower"))

    if None in (prev_close, curr_close, prev_vwap, vwap, prev_vwap_upper, vwap_upper, prev_vwap_lower, vwap_lower):
        return

    now_ts = datetime.now(TZ).strftime("%H:%M:%S")
    state = _load_vwap_state()
    current_strat = state.get("strategy")   

    # --- 邏輯 A：VWAP Upper 突破策略 ---
    if not current_strat:
        if prev_close < prev_vwap_upper and curr_close > vwap_upper:
            _save_vwap_state({"strategy": "A", "direction": "bull"})
            send_discord_message_short(f"[{now_ts}] 進場多單(A)：站上 VWAP Upper ({vwap_upper})")
            return # 進場後跳出，避免同根 K 線觸發多個策略

    elif current_strat == "A":
        # 跌破上軌、中軌、或下軌皆視為撤退
        if curr_close < vwap_upper or curr_close < vwap or curr_close < vwap_lower:
            _save_vwap_state({})
            send_discord_message_short(f"[{now_ts}] 多單離場(A)：跌破支撐點 ({curr_close})")

    # --- 邏輯 B：VWAP 中線突破策略 ---
    if not current_strat:
        if prev_close < prev_vwap and curr_close > vwap:
            _save_vwap_state({"strategy": "B", "direction": "bull"})
            send_discord_message_short(f"[{now_ts}] 進場多單(B)：站上 VWAP 中線 ({vwap})")
            return

    elif current_strat == "B":
        # 跌破中軌或下軌皆視為撤退
        stop_loss = curr_close < vwap or curr_close < vwap_lower
        # 停利改為：只要接近或超過 Upper - 5
        take_profit = curr_close >= (vwap_upper - 5)
        
        if stop_loss or take_profit:
            reason = "停利觸及上軌" if take_profit else "跌破中軌或下軌停損"
            _save_vwap_state({})
            send_discord_message_short(f"[{now_ts}] 多單離場(B)：{reason} ({curr_close})")

    # --- 邏輯 C：VWAP Lower 抄底策略 ---
    if not current_strat:
        if prev_close < prev_vwap_lower and curr_close > vwap_lower:
            _save_vwap_state({"strategy": "C", "direction": "bull"})
            send_discord_message_short(f"[{now_ts}] 進場多單(C)：下軌反彈 ({vwap_lower})")
            return

    elif current_strat == "C":
        stop_loss = curr_close < vwap_lower
        # 停利改為：只要接近或超過 VWAP 中線 - 5
        take_profit = curr_close >= (vwap - 5)
        
        if stop_loss or take_profit:
            reason = "停利觸及中軌" if take_profit else "跌破下軌停損"
            _save_vwap_state({})
            send_discord_message_short(f"[{now_ts}] 多單離場(C)：{reason} ({curr_close})")

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
                        # check_ha_mxf_strategy(target_csv)
                    elif timeframe == "1":
                        # check_vwap_1min_strategy(target_csv)
                        notify_vwap_cross_signals(target_csv)
                    
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
