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


def _ensure_trade_log() -> None:
    if os.path.isfile(TRADE_LOG_PATH):
        return
    os.makedirs(os.path.dirname(TRADE_LOG_PATH), exist_ok=True)
    with open(TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "action", "side", "price", "pnl"])


def _append_trade(action: str, side: str, price: float, pnl: float | None = None) -> None:
    _ensure_trade_log()
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([timestamp, action, side, price, "" if pnl is None else pnl])


def _log_entry(side: str, price: float) -> None:
    _append_trade("enter", side, price)


def _log_exit(side: str, price: float) -> None:
    last_entry = _get_last_entry()
    pnl = None
    if last_entry:
        _, entry_price = last_entry
        if side == "bull":
            pnl = (price - entry_price) * 10
        else:
            pnl = (entry_price - price) * 10
    _append_trade("exiting", side, price, pnl)


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
API_CLIENT = None

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


def _get_latest_mxf_direction() -> str | None:
    if not os.path.isfile(MXF_VALUE_PATH):
        return None
    try:
        with open(MXF_VALUE_PATH, "r", newline="", encoding="utf-8") as handle:
            last_row = None
            for row in csv.reader(handle):
                if row:
                    last_row = row
    except Exception:
        return None

    if not last_row:
        return None
    direction = str(last_row[-1]).strip().lower()
    return direction if direction in {"bull", "bear"} else None


def _get_latest_mxf_switch() -> tuple[str | None, str | None]:
    if not os.path.isfile(MXF_VALUE_PATH):
        return None, None
    try:
        with open(MXF_VALUE_PATH, "r", newline="", encoding="utf-8") as handle:
            rows = [row for row in csv.reader(handle) if row]
    except Exception:
        return None, None

    if len(rows) < 2:
        return None, None

    prev_row = rows[-2]
    curr_row = rows[-1]
    if len(prev_row) < 2 or len(curr_row) < 2:
        return None, None

    prev_dir = str(prev_row[-1]).strip().lower()
    curr_dir = str(curr_row[-1]).strip().lower()
    if prev_dir not in {"bull", "bear"} or curr_dir not in {"bull", "bear"}:
        return None, None

    if prev_dir == curr_dir:
        return None, None

    switch_time = str(curr_row[0]).strip()
    return switch_time, curr_dir

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


def _get_future_entry_price(side: str) -> float | None:
    if not os.path.isfile(FUTURE_VALUE_PATH):
        return None
    try:
        with open(FUTURE_VALUE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    key = "maxBuyValue" if side == "bull" else "maxSellValue"
    value = _to_float(payload.get(key))
    if value is None:
        return None
    return value - 50 if key == "maxBuyValue" else value + 50


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

    callback = getattr(api, "set_order_callback", None)
    if callable(callback):
        def _order_cb(stat, msg):
            try:
                state = _load_vwap_state()
                tp_id = state.get("tp_order_id")
                if not tp_id:
                    return
                order_id = getattr(msg, "order_id", None) or getattr(msg, "id", None) or getattr(msg, "seqno", None)
                status = getattr(msg, "status", None) or getattr(msg, "order_status", None) or ""
                if str(order_id) == str(tp_id) and str(status).lower() in {"filled", "fill", "filled_all"}:
                    _save_vwap_state({})
                    send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 停利單成交，清空策略狀態')
            except Exception:
                return
        try:
            callback(_order_cb)
        except Exception:
            pass

    API_CLIENT = api
    return api


def _extract_order_id(trade):
    if trade is None:
        return None
    order = getattr(trade, "order", None)
    if order is not None:
        for attr in ("id", "order_id", "seqno"):
            value = getattr(order, attr, None)
            if value:
                return str(value)
    for attr in ("id", "order_id", "seqno"):
        value = getattr(trade, attr, None)
        if value:
            return str(value)
    return None


def _place_entry_and_tp(side: str, tp_price: float | None) -> None:
    entry_price = _get_future_entry_price(side)
    if entry_price is None:
        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 找不到進場價，略過下單')
        return
    if DRY_RUN:
        send_discord_message_short(
            f'[{datetime.now(TZ):%H:%M:%S}] 模擬進場 {side} LMT @ {_round_int(entry_price)}'
        )
        if tp_price is not None:
            send_discord_message_short(
                f'[{datetime.now(TZ):%H:%M:%S}] 模擬停利單 {("sell" if side == "bull" else "buy")} LMT @ {_round_int(tp_price)}'
            )
        return

    try:
        api = _get_api_client()
        contract = api.Contracts.Futures.TMF.TMFR1
        _place_limit_order(api, contract, side, entry_price, quantity=1)

        if tp_price is None:
            return
        tp_side = "bear" if side == "bull" else "bull"
        tp_trade = _place_limit_order(api, contract, tp_side, tp_price, quantity=1)
        tp_order_id = _extract_order_id(tp_trade)
        if tp_order_id:
            state = _load_vwap_state()
            state["tp_order_id"] = tp_order_id
            _save_vwap_state(state)
    except Exception as exc:
        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 下單失敗：{exc}')


def _has_position() -> bool | None:
    if DRY_RUN:
        return None
    try:
        api = _get_api_client()
        positions = api.list_positions(api.futopt_account)
        return bool(positions)
    except Exception:
        return None


def _place_entry_order(side: str) -> None:
    entry_price = _get_future_entry_price(side)
    if entry_price is None:
        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 找不到進場價，略過下單')
        return
    if DRY_RUN:
        send_discord_message_short(
            f'[{datetime.now(TZ):%H:%M:%S}] 模擬進場 {side} LMT @ {_round_int(entry_price)}'
        )
        return
    try:
        api = _get_api_client()
        contract = api.Contracts.Futures.TMF.TMFR1
        _place_limit_order(api, contract, side, entry_price, quantity=1)
    except Exception as exc:
        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 下單失敗：{exc}')


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
                _place_limit_order(api, contract, "bear", _get_future_entry_price("bear") or 0, quantity=qty)
            elif direction == "Sell":
                _place_limit_order(api, contract, "bull", _get_future_entry_price("bull") or 0, quantity=qty)
    except Exception as exc:
        send_discord_message_short(f'[{datetime.now(TZ):%H:%M:%S}] 平倉失敗：{exc}')

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

    now = datetime.now(TZ)
    now_ts = now.strftime("%H:%M:%S")
    state = _load_vwap_state()
    current_strat = state.get("strategy")
    current_dir = state.get("direction")
    mxf_dir = _get_latest_mxf_direction()
    mxf_switch_time, _ = _get_latest_mxf_switch()
    last_mxf_switch_time = state.get("mxf_switch_time")
    
    # 如果 MXF 方向有變化，無論目前是否在策略中，都強制出場並更新狀態
    if mxf_switch_time and mxf_switch_time != last_mxf_switch_time:
        if current_dir in {"bull", "bear"}:
            _log_exit(current_dir, curr_close)
        send_discord_message_short(f"[{now_ts}] 方向轉換出場")
        state["mxf_switch_time"] = mxf_switch_time
        _save_vwap_state(state)

    # 尾盤強制出場
    force_exit_times = {(13, 44), (4, 59)}
    force_exit_key = now.strftime("%Y-%m-%d %H:%M")
    if (now.hour, now.minute) in force_exit_times:
        if state.get("force_exit_key") != force_exit_key:
            if current_dir in {"bull", "bear"}:
                _log_exit(current_dir, curr_close)
            send_discord_message_short(f"[{now_ts}] 尾盤強制出場")
            state["force_exit_key"] = force_exit_key
            _save_vwap_state(state)
            return

    # 每分鐘檢查倉位，沒倉位就清空策略狀態（未來真正策略時要打開）
    # pos_check_key = now.strftime("%Y-%m-%d %H:%M")
    # if state.get("pos_check_key") != pos_check_key:
    #     has_pos = _has_position()
    #     state["pos_check_key"] = pos_check_key
    #     if has_pos is False:
    #         _save_vwap_state({})
    #         return
    #     _save_vwap_state(state)

    # --- 邏輯 A：VWAP Upper 突破策略 ---
    if not current_strat:
        if prev_close < prev_vwap_upper and curr_close > vwap_upper:
            _save_vwap_state({"strategy": "A", "direction": "bull"})
            send_discord_message_short(f"[{now_ts}] 進場多單(A)：站上 VWAP Upper ({curr_close})")
            _log_entry("bull", curr_close)
            _place_entry_and_tp("bull", None)
            return # 進場後跳出，避免同根 K 線觸發多個策略

    elif current_strat == "A" and current_dir == "bull":
        # 跌破上軌、中軌、或下軌皆視為撤退
        if curr_close < vwap_upper or curr_close < vwap or curr_close < vwap_lower:
            tp_price = vwap + 5
            _save_vwap_state({
                "strategy": "F",
                "direction": "bear",
                "take_profit_price": tp_price,
            })
            _log_exit("bull", curr_close)
            _log_entry("bear", curr_close)
            send_discord_message_short(f"[{now_ts}] 多單離場(A)：跌破支撐點 ({curr_close})")
            send_discord_message_short(f"[{now_ts}] 進場空單(F)：上軌反轉 ({curr_close})")
            send_discord_message_short(f"[{now_ts}] 上線進場後的停利單：{tp_price}")
            _place_entry_and_tp("bear", tp_price)

    if not current_strat:
        if prev_close > prev_vwap_lower and curr_close < vwap_lower:
            _save_vwap_state({"strategy": "D", "direction": "bear"})
            send_discord_message_short(f"[{now_ts}] 進場空單(D)：跌破 VWAP Lower ({curr_close})")
            _log_entry("bear", curr_close)
            _place_entry_and_tp("bear", None)
            return

    elif current_strat == "D" and current_dir == "bear":
        # 突破下軌、中軌、或上軌皆視為撤退
        if curr_close > vwap_lower or curr_close > vwap or curr_close > vwap_upper:
            tp_price = vwap - 5
            _save_vwap_state({
                "strategy": "C",
                "direction": "bull",
                "take_profit_price": tp_price,
            })
            _log_exit("bear", curr_close)
            _log_entry("bull", curr_close)
            send_discord_message_short(f"[{now_ts}] 空單離場(D)：突破壓力點 ({curr_close})")
            send_discord_message_short(f"[{now_ts}] 進場多單(C)：下軌反彈 ({curr_close})")
            send_discord_message_short(f"[{now_ts}] 下線進場後的停利單：{tp_price}")
            _place_entry_and_tp("bull", tp_price)

    # --- 邏輯 B：VWAP 中線突破策略 ---
    if not current_strat:
        if prev_close < prev_vwap and curr_close > vwap:
            tp_price = vwap_upper - 5
            _save_vwap_state({
                "strategy": "B",
                "direction": "bull",
                "take_profit_price": tp_price,
            })
            send_discord_message_short(f"[{now_ts}] 進場多單(B)：站上 VWAP 中線 ({curr_close})")
            send_discord_message_short(f"[{now_ts}] 中線進場後的停利單：{tp_price}")
            _log_entry("bull", curr_close)
            _place_entry_and_tp("bull", tp_price)
            return

    elif current_strat == "B" and current_dir == "bull":
        # 跌破中軌或下軌皆視為撤退
        stop_loss = curr_close < vwap or curr_close < vwap_lower
        # 停利改為：只要接近或超過 Upper - 5
        take_profit = curr_close >= (vwap_upper - 5)
        
        if stop_loss or take_profit:
            if take_profit:
                _save_vwap_state({})
                _log_exit("bull", curr_close)
                send_discord_message_short(f"[{now_ts}] 多單離場(B)：停利觸及上軌 ({curr_close})")
            else:
                tp_price = vwap_lower + 5
                _save_vwap_state({
                    "strategy": "E",
                    "direction": "bear",
                    "take_profit_price": tp_price,
                })
                _log_exit("bull", curr_close)
                _log_entry("bear", curr_close)
                send_discord_message_short(f"[{now_ts}] 多單離場(B)：跌破中軌或下軌停損 ({curr_close})")
                send_discord_message_short(f"[{now_ts}] 進場空單(E)：跌破 VWAP 中線 ({curr_close})")
                send_discord_message_short(f"[{now_ts}] 中線進場後的停利單：{tp_price}")
                _place_entry_and_tp("bear", tp_price)

    if not current_strat:
        if prev_close > prev_vwap and curr_close < vwap:
            tp_price = vwap_lower + 5
            _save_vwap_state({
                "strategy": "E",
                "direction": "bear",
                "take_profit_price": tp_price,
            })
            send_discord_message_short(f"[{now_ts}] 進場空單(E)：跌破 VWAP 中線 ({curr_close})")
            send_discord_message_short(f"[{now_ts}] 中線進場後的停利單：{tp_price}")
            _log_entry("bear", curr_close)
            _place_entry_and_tp("bear", tp_price)
            return

    elif current_strat == "E" and current_dir == "bear":
        # 突破中軌或上軌皆視為撤退
        stop_loss = curr_close > vwap or curr_close > vwap_upper
        # 停利改為：只要接近或跌破 Lower + 5
        take_profit = curr_close <= (vwap_lower + 5)
        
        if stop_loss or take_profit:
            if take_profit:
                _save_vwap_state({})
                _log_exit("bear", curr_close)
                send_discord_message_short(f"[{now_ts}] 空單離場(E)：停利觸及下軌 ({curr_close})")
            else:
                tp_price = vwap_upper - 5
                _save_vwap_state({
                    "strategy": "B",
                    "direction": "bull",
                    "take_profit_price": tp_price,
                })
                _log_exit("bear", curr_close)
                _log_entry("bull", curr_close)
                send_discord_message_short(f"[{now_ts}] 空單離場(E)：突破中軌或上軌停損 ({curr_close})")
                send_discord_message_short(f"[{now_ts}] 進場多單(B)：站上 VWAP 中線 ({curr_close})")
                send_discord_message_short(f"[{now_ts}] 中線進場後的停利單：{tp_price}")
                _place_entry_and_tp("bull", tp_price)

    # --- 邏輯 C：VWAP Lower 抄底策略 ---
    if not current_strat:
        if prev_close < prev_vwap_lower and curr_close > vwap_lower:
            tp_price = vwap - 5
            _save_vwap_state({
                "strategy": "C",
                "direction": "bull",
                "take_profit_price": tp_price,
            })
            send_discord_message_short(f"[{now_ts}] 進場多單(C)：下軌反彈 ({curr_close})")
            send_discord_message_short(f"[{now_ts}] 下線進場後的停利單：{tp_price}")
            _log_entry("bull", curr_close)
            _place_entry_and_tp("bull", tp_price)
            return

    elif current_strat == "C" and current_dir == "bull":
        stop_loss = curr_close < vwap_lower
        # 停利改為：只要接近或超過 VWAP 中線 - 5
        take_profit = curr_close >= (vwap - 5)
        
        if stop_loss or take_profit:
            if take_profit:
                _save_vwap_state({})
                _log_exit("bull", curr_close)
                send_discord_message_short(f"[{now_ts}] 多單離場(C)：停利觸及中軌 ({curr_close})")
            else:
                _save_vwap_state({"strategy": "D", "direction": "bear"})
                _log_exit("bull", curr_close)
                _log_entry("bear", curr_close)
                send_discord_message_short(f"[{now_ts}] 多單離場(C)：跌破下軌停損 ({curr_close})")
                send_discord_message_short(f"[{now_ts}] 進場空單(D)：跌破 VWAP Lower ({curr_close})")
                _place_entry_and_tp("bear", None)

    if not current_strat:
        if prev_close > prev_vwap_upper and curr_close < vwap_upper:
            tp_price = vwap + 5
            _save_vwap_state({
                "strategy": "F",
                "direction": "bear",
                "take_profit_price": tp_price,
            })
            send_discord_message_short(f"[{now_ts}] 進場空單(F)：上軌反轉 ({curr_close})")
            send_discord_message_short(f"[{now_ts}] 上線進場後的停利單：{tp_price}")
            _log_entry("bear", curr_close)
            _place_entry_and_tp("bear", tp_price)
            return

    elif current_strat == "F" and current_dir == "bear":
        stop_loss = curr_close > vwap_upper
        # 停利改為：只要接近或跌破 VWAP 中線 + 5
        take_profit = curr_close <= (vwap + 5)
        
        if stop_loss or take_profit:
            if take_profit:
                _save_vwap_state({})
                _log_exit("bear", curr_close)
                send_discord_message_short(f"[{now_ts}] 空單離場(F)：停利觸及中軌 ({curr_close})")
            else:
                _save_vwap_state({"strategy": "A", "direction": "bull"})
                _log_exit("bear", curr_close)
                _log_entry("bull", curr_close)
                send_discord_message_short(f"[{now_ts}] 空單離場(F)：突破上軌停損 ({curr_close})")
                send_discord_message_short(f"[{now_ts}] 進場多單(A)：站上 VWAP Upper ({curr_close})")
                _place_entry_and_tp("bull", None)


def notify_vwap_trend_signals(csv_path: str) -> None:
    rows = _read_last_two_rows(csv_path)
    if len(rows) < 2:
        return

    curr_row = rows[-1]
    timeframe = str(curr_row.get("Timeframe", "")).strip()
    if timeframe != "1":
        return

    curr_close = _to_int(curr_row.get("Close"))
    curr_vwap = _to_int(curr_row.get("VWAP"))
    if curr_close is None or curr_vwap is None:
        return

    now = datetime.now(TZ)
    now_ts = now.strftime("%H:%M:%S")
    state = _load_vwap_state()
    trend_dir = state.get("vwap_trend_direction")

    force_exit_times = {(13, 44), (4, 59)}
    if (now.hour, now.minute) in force_exit_times:
        if trend_dir:
            _log_exit(trend_dir, curr_close)
            send_discord_message_short(f"[{now_ts}] VWAP 方向策略尾盤出場")
            state.pop("vwap_trend_direction", None)
            _save_vwap_state(state)
            _close_all_positions()
        return

    if not trend_dir:
        if curr_close > curr_vwap:
            state["vwap_trend_direction"] = "bull"
            _save_vwap_state(state)
            send_discord_message_short(f"[{now_ts}] VWAP 方向策略多單進場：站上 VWAP ({curr_vwap})")
            _log_entry("bull", curr_close)
            _place_entry_order("bull")
        elif curr_close < curr_vwap:
            state["vwap_trend_direction"] = "bear"
            _save_vwap_state(state)
            send_discord_message_short(f"[{now_ts}] VWAP 方向策略空單進場：跌破 VWAP ({curr_vwap})")
            _log_entry("bear", curr_close)
            _place_entry_order("bear")
        return

    if trend_dir == "bull" and curr_close < curr_vwap:
        state["vwap_trend_direction"] = "bear"
        _save_vwap_state(state)
        send_discord_message_short(f"[{now_ts}] VWAP 方向策略反手空單：跌破 VWAP ({curr_vwap})")
        _log_exit("bull", curr_close)
        _log_entry("bear", curr_close)
        _close_all_positions()
        _place_entry_order("bear")
        return

    if trend_dir == "bear" and curr_close > curr_vwap:
        state["vwap_trend_direction"] = "bull"
        _save_vwap_state(state)
        send_discord_message_short(f"[{now_ts}] VWAP 方向策略反手多單：站上 VWAP ({curr_vwap})")
        _log_exit("bear", curr_close)
        _log_entry("bull", curr_close)
        _close_all_positions()
        _place_entry_order("bull")
        return


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
                        # notify_vwap_cross_signals(target_csv)
                        notify_vwap_trend_signals(target_csv)
                    
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
