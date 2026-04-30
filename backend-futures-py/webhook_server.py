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
from auto_trade import send_discord_message as _base_long_send_discord_message
from auto_trade_shortCycle import auto_trade as shortcycle_auto_trade
from auto_trade_shortCycle import closePosition as shortcycle_close_position
from auto_trade_shortCycle import send_discord_message as _base_shortcycle_send_discord_message

# Configuration
PORT = 8080
CSV_FILE_1MIN = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_1min.csv")
MXF_VALUE_CSV_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "mxf_value.csv")
H_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "h_trade.csv")
BB_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "bb_trade.csv")
BBR960_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "bbr960_trade.csv")
TT_BBR_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "tt_bbr_trade.csv")
BBR960_STATE_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "bbr960_state.json")
BBR_WAVE_STATE_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "bbr_wave_state.json")
TT_BBR_STATE_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "tt_bbr_state.json")
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
    'tt_short',
    'tt_long',
    'BBR',
]

STRATEGY_LOCK = RLock()
BBR960_ADD_THRESHOLD = 250.0
BBR960_PENDING_TIMEOUT_SECONDS = 10 * 60
BBR_WAVE_PENDING_TIMEOUT_SECONDS = 10 * 60
TT_BBR_PENDING_TIMEOUT_SECONDS = 60 * 60


def _to_float(value: str | None) -> float | None:
    """將 CSV / webhook 的字串欄位轉成 float，失敗時回傳 None。"""
    if value is None:
        return None
    try:
        cleaned = str(value).replace(",", "").strip()
        if not cleaned:
            return None
        return float(cleaned)
    except ValueError:
        return None


def _split_tt_state(raw_value: str | None) -> tuple[str, str]:
    """把舊的單欄狀態拆成 tt_short / tt_long。"""
    text = str(raw_value or "").strip()
    if not text:
        return "", ""
    if "|" in text:
        left, right = text.split("|", 1)
        return left.strip(), right.strip()
    return text, ""


def _ensure_csv_header(path: str, header: list[str]) -> None:
    """確認 CSV 檔案的表頭存在且欄位順序正確。"""
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
    if path == CSV_FILE_1MIN and current_header == [
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
    ]:
        migrated_rows: list[list[str]] = []
        for row in data_rows:
            if len(row) < 15:
                row = row + [""] * (15 - len(row))
            tt_short, tt_long = _split_tt_state(row[13])
            migrated_rows.append([
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                row[12],
                tt_short,
                tt_long,
                row[14] if len(row) > 14 else "",
            ])
        data_rows = migrated_rows

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
    """清空 CSV 內容，但保留最後幾筆資料與表頭。"""
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
    """讀取 CSV 最後 n 筆資料列。"""
    if count <= 0 or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return []
    return rows[-count:] if len(rows) >= count else rows


def _get_latest_h_trade_entry() -> tuple[str, float] | None:
    """從 h_trade.csv 取出最近一筆進場單。"""
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
    """從 mxf_value.csv 讀取最新一筆 mtx_bvav。"""
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


def _format_mxf_number(value: object) -> str:
    """把 MXF 數值格式化成適合顯示的字串。"""
    number = _to_float(value)
    if number is None:
        return ""
    if float(number).is_integer():
        return f"{int(number):,}"
    return f"{number:,.3f}".rstrip("0").rstrip(".")


def _get_latest_mxf_snapshot() -> dict[str, str]:
    """讀取 mxf_value.csv 的最新一筆原始資料。"""
    if not os.path.isfile(MXF_VALUE_CSV_PATH):
        return {"tx_bvav": "", "mtx_bvav": "", "mtx_bvav_avg": ""}

    try:
        with open(MXF_VALUE_CSV_PATH, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return {"tx_bvav": "", "mtx_bvav": "", "mtx_bvav_avg": ""}

    for row in reversed(rows):
        snapshot = {
            "tx_bvav": str(row.get("tx_bvav", "")).strip(),
            "mtx_bvav": str(row.get("mtx_bvav", "")).strip(),
            "mtx_bvav_avg": str(row.get("mtx_bvav_avg", "")).strip(),
        }
        if any(snapshot.values()):
            return snapshot

    return {"tx_bvav": "", "mtx_bvav": "", "mtx_bvav_avg": ""}


def _append_mxf_context(message: str) -> str:
    """把最新 MXF 資料附加到 Discord 訊息。"""
    snapshot = _get_latest_mxf_snapshot()
    if not any(snapshot.values()):
        return message

    context_lines = [
        "MXF最新:",
        f"坦克(tx_bvav): {_format_mxf_number(snapshot['tx_bvav']) or '-'}",
        f"游擊隊(mtx_bvav): {_format_mxf_number(snapshot['mtx_bvav']) or '-'}",
        f"游擊平均(mtx_bvav_avg): {_format_mxf_number(snapshot['mtx_bvav_avg']) or '-'}",
    ]
    if not message:
        return "\n".join(context_lines)
    return f"{message}\n" + "\n".join(context_lines)


def long_send_discord_message(content: str) -> None:
    """送出長線 Discord 訊息，並附上最新 MXF 資料。"""
    _base_long_send_discord_message(_append_mxf_context(content))


def shortcycle_send_discord_message(content: str) -> None:
    """送出短線 Discord 訊息，並附上最新 MXF 資料。"""
    _base_shortcycle_send_discord_message(_append_mxf_context(content))


def _ensure_trade_log_header(path: str) -> None:
    """確認交易紀錄檔的表頭格式。"""
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
    """寫入 shortCycle / BB 策略交易紀錄。"""
    _ensure_trade_log_header(BB_TRADE_LOG_PATH)
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(BB_TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([timestamp, action, side, price, pnl, quantity])


def _append_bbr960_trade(action: str, side: str, price: float, pnl: str = "", quantity: int = 1) -> None:
    """寫入 BBR960 策略交易紀錄。"""
    _ensure_trade_log_header(BBR960_TRADE_LOG_PATH)
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(BBR960_TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([timestamp, action, side, price, pnl, quantity])


def _ensure_tt_bbr_trade_log_header() -> None:
    """確認 TT/BBR 交易紀錄檔的表頭格式。"""
    header = ["timestamp", "action", "side", "price", "note", "tx_bvav", "mtx_bvav", "mtx_bvav_avg"]
    if not os.path.isfile(TT_BBR_TRADE_LOG_PATH):
        os.makedirs(os.path.dirname(TT_BBR_TRADE_LOG_PATH), exist_ok=True)
        with open(TT_BBR_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
        return

    try:
        with open(TT_BBR_TRADE_LOG_PATH, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except Exception:
        return

    if not rows:
        with open(TT_BBR_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
        return

    if rows[0] == header:
        return

    data_rows = rows[1:]
    with open(TT_BBR_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(data_rows)


def _append_tt_bbr_trade(action: str, side: str, price: float, note: str = "") -> None:
    """寫入 TT/BBR 策略進出場紀錄。"""
    _ensure_tt_bbr_trade_log_header()
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    snapshot = _get_latest_mxf_snapshot()
    with open(TT_BBR_TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            timestamp,
            action,
            side,
            price,
            note,
            snapshot["tx_bvav"],
            snapshot["mtx_bvav"],
            snapshot["mtx_bvav_avg"],
        ])


def _default_bbr960_state() -> dict:
    """建立 BBR960 的預設狀態。"""
    return {
        "pending_side": "",
        "pending_entry_price": "",
        "pending_since": "",
        "pending_quantity": 0,
        "position_side": "",
        "position_entry_price": "",
        "position_quantity": 0,
        "position_since": "",
    }


def _load_bbr960_state() -> dict:
    """讀取 BBR960 狀態檔。"""
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
                "pending_quantity": int(_to_float(raw_state.get("pending_quantity")) or 0),
                "position_side": str(raw_state.get("position_side", "")).strip().lower(),
                "position_entry_price": raw_state.get("position_entry_price", ""),
                "position_quantity": int(_to_float(raw_state.get("position_quantity")) or 0),
                "position_since": str(raw_state.get("position_since", "")).strip(),
            })
    except Exception:
        return state

    return state


def _save_bbr960_state(state: dict) -> None:
    """儲存 BBR960 狀態檔。"""
    os.makedirs(os.path.dirname(BBR960_STATE_PATH), exist_ok=True)
    with open(BBR960_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _clear_bbr960_pending_state() -> None:
    """清除 BBR960 的 pending 狀態。"""
    _save_bbr960_state(_default_bbr960_state())


def _mark_bbr960_pending(side: str, entry_price: float, quantity: int = 1) -> None:
    """標記 BBR960 目前正在等待下一次加碼判斷。"""
    state = {
        "pending_side": side,
        "pending_entry_price": entry_price,
        "pending_since": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "pending_quantity": int(quantity),
        "position_side": "",
        "position_entry_price": "",
        "position_quantity": 0,
        "position_since": "",
    }
    _save_bbr960_state(state)


def _mark_bbr960_position(side: str, entry_price: float, quantity: int) -> None:
    """標記 BBR960 目前已持有的加碼部位。"""
    state = _load_bbr960_state()
    state["position_side"] = side
    state["position_entry_price"] = entry_price
    state["position_quantity"] = int(quantity)
    state["position_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    state["pending_side"] = ""
    state["pending_entry_price"] = ""
    state["pending_since"] = ""
    state["pending_quantity"] = 0
    _save_bbr960_state(state)


def _clear_bbr960_position_state() -> None:
    """清除 BBR960 已持有部位的狀態。"""
    state = _load_bbr960_state()
    state["position_side"] = ""
    state["position_entry_price"] = ""
    state["position_quantity"] = 0
    state["position_since"] = ""
    _save_bbr960_state(state)


def _parse_bbr960_pending_since(raw_value: str) -> datetime | None:
    """把 BBR960 pending 時間字串轉成 datetime。"""
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except ValueError:
        return None


def _bbr960_pending_matches_latest(state: dict, latest_entry: tuple[str, float]) -> bool:
    """檢查 BBR960 pending 是否仍對應最新進場單。"""
    pending_side = str(state.get("pending_side", "")).strip().lower()
    pending_price = _to_float(state.get("pending_entry_price"))
    if not pending_side or pending_price is None:
        return False

    latest_side, latest_price = latest_entry
    return pending_side == latest_side and abs(pending_price - latest_price) < 1e-9


def _is_bbr960_add_blocked(state: dict, latest_entry: tuple[str, float]) -> bool:
    """判斷 BBR960 是否仍處於加碼冷卻中。"""
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
    """用 auto_trade.py 的 buyOne / sellOne 送出 BBR960 加碼單。"""
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
            print('bull')
            # long_buy_one(api, contract, quantity)
        else:
            print('sell')
            # long_sell_one(api, contract, quantity)

        long_send_discord_message(f"webhook_server: BBR960 已送出 {side} 加碼 {quantity} 口")
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


def _trigger_bbr960_stop(side: str, close_price: float, quantity: int, reason: str) -> None:
    """觸發 BBR960 停損，使用相同口數反向沖銷。"""
    def _runner() -> None:
        success = False
        try:
            exit_side = "bear" if side == "bull" else "bull"
            _append_bbr960_trade("exiting", side, close_price, quantity=quantity)
            long_send_discord_message(
                f"webhook_server: close={close_price}，即將觸發 BBR960 停損 {side}，口數={quantity}，原因：{reason}"
            )
            print(f"🔔 Trigger BBR960 stop loss({side}) because {reason}, quantity={quantity}")
            _submit_bbr960_order(exit_side, quantity)
            success = True
        except Exception as exc:
            print(f"❌ BBR960 stop loss({side}) failed: {exc}")
        finally:
            with STRATEGY_LOCK:
                _clear_bbr960_pending_state()
                if success:
                    _clear_bbr960_position_state()
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _default_bbr_wave_state() -> dict:
    """建立 BBR wave 策略的預設狀態。"""
    return {
        "position_side": "",
        "pending_action": "",
        "pending_side": "",
        "pending_since": "",
        "short_armed": False,
        "short_count": 0,
        "short_ready": False,
        "short_armed_since": "",
        "long_armed": False,
        "long_count": 0,
        "long_ready": False,
        "long_armed_since": "",
    }


def _load_bbr_wave_state() -> dict:
    """讀取 BBR wave 狀態檔。"""
    state = _default_bbr_wave_state()
    if not os.path.isfile(BBR_WAVE_STATE_PATH):
        return state

    try:
        with open(BBR_WAVE_STATE_PATH, "r", encoding="utf-8") as handle:
            raw_state = json.load(handle)
    except Exception:
        return state

    if not isinstance(raw_state, dict):
        return state

    state["position_side"] = str(raw_state.get("position_side", "")).strip().lower()
    state["pending_action"] = str(raw_state.get("pending_action", "")).strip().lower()
    state["pending_side"] = str(raw_state.get("pending_side", "")).strip().lower()
    state["pending_since"] = str(raw_state.get("pending_since", "")).strip()
    state["short_armed"] = bool(raw_state.get("short_armed", False))
    state["short_count"] = int(_to_float(raw_state.get("short_count")) or 0)
    state["short_ready"] = bool(raw_state.get("short_ready", False))
    state["short_armed_since"] = str(raw_state.get("short_armed_since", "")).strip()
    state["long_armed"] = bool(raw_state.get("long_armed", False))
    state["long_count"] = int(_to_float(raw_state.get("long_count")) or 0)
    state["long_ready"] = bool(raw_state.get("long_ready", False))
    state["long_armed_since"] = str(raw_state.get("long_armed_since", "")).strip()
    return state


def _save_bbr_wave_state(state: dict) -> None:
    """儲存 BBR wave 狀態檔。"""
    os.makedirs(os.path.dirname(BBR_WAVE_STATE_PATH), exist_ok=True)
    with open(BBR_WAVE_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _reset_bbr_wave_setup(state: dict, side: str) -> None:
    """重置 short / long 的三根 setup 計數。"""
    if side == "bear":
        state["short_armed"] = False
        state["short_count"] = 0
        state["short_ready"] = False
        state["short_armed_since"] = ""
    elif side == "bull":
        state["long_armed"] = False
        state["long_count"] = 0
        state["long_ready"] = False
        state["long_armed_since"] = ""


def _reset_bbr_wave_state() -> None:
    """重置 BBR wave 的整體狀態。"""
    _save_bbr_wave_state(_default_bbr_wave_state())


def _parse_bbr_wave_pending_since(raw_value: str) -> datetime | None:
    """把 BBR wave pending 時間字串轉成 datetime。"""
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except ValueError:
        return None


def _is_red_candle(row: dict) -> bool:
    """判斷當根 K 棒是否為紅 K。"""
    open_price = _to_float(row.get("Open"))
    close_price = _to_float(row.get("Close"))
    if open_price is None or close_price is None:
        return False
    return close_price > open_price


def _is_black_candle(row: dict) -> bool:
    """判斷當根 K 棒是否為黑 K。"""
    open_price = _to_float(row.get("Open"))
    close_price = _to_float(row.get("Close"))
    if open_price is None or close_price is None:
        return False
    return close_price < open_price


def _is_bbr_wave_blocked(state: dict) -> bool:
    """判斷 BBR wave 是否仍在 pending 冷卻中。"""
    pending_action = str(state.get("pending_action", "")).strip().lower()
    if not pending_action:
        return False

    pending_since = _parse_bbr_wave_pending_since(str(state.get("pending_since", "")).strip())
    if pending_since is None:
        return True

    elapsed_seconds = (datetime.now(TZ) - pending_since).total_seconds()
    if elapsed_seconds > BBR_WAVE_PENDING_TIMEOUT_SECONDS:
        state["pending_action"] = ""
        state["pending_side"] = ""
        state["pending_since"] = ""
        _save_bbr_wave_state(state)
        return False
    return True


def _mark_bbr_wave_pending(action: str, side: str) -> None:
    """標記 BBR wave 目前有待執行的動作。"""
    with STRATEGY_LOCK:
        state = _load_bbr_wave_state()
        state["pending_action"] = action
        state["pending_side"] = side
        state["pending_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        _save_bbr_wave_state(state)


def _trigger_bbr_wave_entry(side: str, close_price: float, reason: str) -> None:
    """送出 BBR wave 進場單並同步寫回狀態。"""
    def _runner() -> None:
        success = False
        try:
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，即將觸發 BBR 波段進場 {side}，原因：{reason}"
            )
            print(f"🔔 Trigger BBR wave auto_trade({side}) because {reason}")
            shortcycle_auto_trade(side)
            success = True
        except Exception as exc:
            print(f"❌ BBR wave auto_trade({side}) failed: {exc}")
        finally:
            with STRATEGY_LOCK:
                state = _load_bbr_wave_state()
                state["position_side"] = side if success else ""
                state["pending_action"] = ""
                state["pending_side"] = ""
                state["pending_since"] = ""
                _reset_bbr_wave_setup(state, "bear")
                _reset_bbr_wave_setup(state, "bull")
                _save_bbr_wave_state(state)
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _trigger_bbr_wave_exit(side: str, close_price: float, reason: str) -> None:
    """送出 BBR wave 平倉動作並同步寫回狀態。"""
    def _runner() -> None:
        success = False
        try:
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，即將觸發 BBR 波段平倉 {side}，原因：{reason}"
            )
            print(f"🔔 Trigger BBR wave closePosition({side}) because {reason}")
            shortcycle_close_position()
            success = True
        except Exception as exc:
            print(f"❌ BBR wave closePosition({side}) failed: {exc}")
        finally:
            with STRATEGY_LOCK:
                state = _load_bbr_wave_state()
                if success:
                    state["position_side"] = ""
                state["pending_action"] = ""
                state["pending_side"] = ""
                state["pending_since"] = ""
                _reset_bbr_wave_setup(state, "bear")
                _reset_bbr_wave_setup(state, "bull")
                _save_bbr_wave_state(state)
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _trigger_shortcycle_trade(side: str, close_price: float, reason: str) -> None:
    """觸發 shortCycle 進場單。"""
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
    """觸發 shortCycle 平倉。"""
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


def _trigger_bbr960_trade(side: str, close_price: float, reason: str, quantity: int = 1) -> None:
    """觸發 BBR960 加碼單。"""
    def _runner() -> None:
        try:
            _append_bbr960_trade("enter", side, close_price, quantity=quantity)
            long_send_discord_message(
                f"webhook_server: close={close_price}，即將觸發 BBR960 auto_trade({side})，口數={quantity}，原因：{reason}"
            )
            print(f"🔔 Trigger BBR960 auto_trade({side}) because {reason}, quantity={quantity}")
            _submit_bbr960_order(side, quantity)
            with STRATEGY_LOCK:
                _mark_bbr960_position(side, close_price, quantity)
        except Exception as exc:
            print(f"❌ BBR960 auto_trade({side}) failed: {exc}")
            with STRATEGY_LOCK:
                _clear_bbr960_pending_state()
                _clear_bbr960_position_state()
        finally:
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _apply_bbr_strategy() -> bool:
    """原本的 BBR + h_trade 觸發策略。"""
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
    """以已獲利部位為基礎的 BBR960 加碼策略。"""
    with STRATEGY_LOCK:
        rows = _read_last_n_rows(CSV_FILE_1MIN, 3)
        if len(rows) < 3:
            return False

        prev_row, mid_row, curr_row = rows[-3], rows[-2], rows[-1]
        prev_bbr = _to_float(prev_row.get("BBR"))
        mid_bbr = _to_float(mid_row.get("BBR"))
        curr_bbr = _to_float(curr_row.get("BBR"))
        curr_close = _to_float(curr_row.get("Close"))
        if prev_bbr is None or mid_bbr is None or curr_bbr is None or curr_close is None:
            return False

        state = _load_bbr960_state()
        position_side = str(state.get("position_side", "")).strip().lower()
        if position_side == "bull":
            position_qty = int(_to_float(state.get("position_quantity")) or 1)
            if curr_bbr < 0:
                _trigger_bbr960_stop(
                    "bull",
                    curr_close,
                    position_qty,
                    f"BBR dropped back below 0 after bull add-on: {prev_bbr} -> {curr_bbr}",
                )
                return True
            return False

        if position_side == "bear":
            position_qty = int(_to_float(state.get("position_quantity")) or 1)
            if curr_bbr > 1:
                _trigger_bbr960_stop(
                    "bear",
                    curr_close,
                    position_qty,
                    f"BBR rose back above 1 after bear add-on: {prev_bbr} -> {curr_bbr}",
                )
                return True
            return False

        latest_entry = _get_latest_h_trade_entry()
        if latest_entry is None:
            return False

        if _is_bbr960_add_blocked(state, latest_entry):
            return False

        entry_side, entry_price = latest_entry
        in_profit = (curr_close > entry_price) if entry_side == "bull" else (curr_close < entry_price)
        profit_points = (curr_close - entry_price) if entry_side == "bull" else (entry_price - curr_close)
        latest_mtx_bvav = _get_latest_mtx_bvav()
        entry_quantity = 1

        if entry_side == "bull":
            if (
                prev_bbr < 0
                and mid_bbr > 0
                and curr_bbr > 0
                and in_profit
                and profit_points >= BBR960_ADD_THRESHOLD
                and latest_mtx_bvav is not None
                and latest_mtx_bvav > -2000
            ):
                _mark_bbr960_pending(entry_side, entry_price, entry_quantity)
                _trigger_bbr960_trade(
                    "bull",
                    curr_close,
                    f"BBR confirmed above 0 for 2 bars: {prev_bbr} -> {mid_bbr} -> {curr_bbr}; bull profit is {profit_points:.2f}; mtx_bvav={latest_mtx_bvav}",
                    quantity=entry_quantity,
                )
                return True
            return False

        if entry_side == "bear":
            if (
                prev_bbr > 1
                and mid_bbr < 1
                and curr_bbr < 1
                and in_profit
                and profit_points >= BBR960_ADD_THRESHOLD
                and latest_mtx_bvav is not None
                and latest_mtx_bvav < 2000
            ):
                _mark_bbr960_pending(entry_side, entry_price, entry_quantity)
                _trigger_bbr960_trade(
                    "bear",
                    curr_close,
                    f"BBR confirmed below 1 for 2 bars: {prev_bbr} -> {mid_bbr} -> {curr_bbr}; bear profit is {profit_points:.2f}; mtx_bvav={latest_mtx_bvav}",
                    quantity=entry_quantity,
                )
                return True
            return False

        return False


def _apply_bbr_wave_strategy() -> bool:
    """只看 BBR 與 K 棒顏色的波段策略。"""
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

        state = _load_bbr_wave_state()
        if _is_bbr_wave_blocked(state):
            return False

        position_side = str(state.get("position_side", "")).strip().lower()

        if position_side == "bear":
            if curr_bbr > 1:
                _mark_bbr_wave_pending("exit", "bear")
                shortcycle_send_discord_message(
                    f"webhook_server: BBR wave 空單停損訊號，BBR={prev_bbr} -> {curr_bbr}"
                )
                _trigger_bbr_wave_exit("bear", curr_close, f"BBR flipped back above 1: {prev_bbr} -> {curr_bbr}")
                return True
            if prev_bbr < 0 < curr_bbr:
                _mark_bbr_wave_pending("exit", "bear")
                shortcycle_send_discord_message(
                    f"webhook_server: BBR wave 空單停利訊號，BBR={prev_bbr} -> {curr_bbr}"
                )
                _trigger_bbr_wave_exit("bear", curr_close, f"BBR profit target hit: {prev_bbr} -> {curr_bbr}")
                return True
            return False

        if position_side == "bull":
            if curr_bbr < 0:
                _mark_bbr_wave_pending("exit", "bull")
                shortcycle_send_discord_message(
                    f"webhook_server: BBR wave 多單停損訊號，BBR={prev_bbr} -> {curr_bbr}"
                )
                _trigger_bbr_wave_exit("bull", curr_close, f"BBR flipped back below 0: {prev_bbr} -> {curr_bbr}")
                return True
            if prev_bbr > 1 and curr_bbr < 1:
                _mark_bbr_wave_pending("exit", "bull")
                shortcycle_send_discord_message(
                    f"webhook_server: BBR wave 多單停利訊號，BBR={prev_bbr} -> {curr_bbr}"
                )
                _trigger_bbr_wave_exit("bull", curr_close, f"BBR profit target hit: {prev_bbr} -> {curr_bbr}")
                return True
            return False

        # Flat: build short setup, then wait for a red candle to enter short.
        if curr_bbr < 1:
            if not bool(state.get("short_armed", False)):
                if prev_bbr > 1:
                    state["short_armed"] = True
                    state["short_count"] = 1
                    state["short_ready"] = False
                    state["short_armed_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                    shortcycle_send_discord_message(
                        f"webhook_server: BBR wave 空單 setup 已啟動，BBR={prev_bbr} -> {curr_bbr}"
                    )
            else:
                state["short_count"] = int(state.get("short_count", 0)) + 1
                if state["short_count"] >= 3:
                    state["short_ready"] = True
                    shortcycle_send_discord_message(
                        f"webhook_server: BBR wave 空單 setup 已完成 3 根，等待紅K，BBR={prev_bbr} -> {curr_bbr}"
                    )
        else:
            _reset_bbr_wave_setup(state, "bear")

        # Flat: build long setup, then wait for a black candle to enter long.
        if curr_bbr > 0:
            if not bool(state.get("long_armed", False)):
                if prev_bbr < 0:
                    state["long_armed"] = True
                    state["long_count"] = 1
                    state["long_ready"] = False
                    state["long_armed_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                    shortcycle_send_discord_message(
                        f"webhook_server: BBR wave 多單 setup 已啟動，BBR={prev_bbr} -> {curr_bbr}"
                    )
            else:
                state["long_count"] = int(state.get("long_count", 0)) + 1
                if state["long_count"] >= 3:
                    state["long_ready"] = True
                    shortcycle_send_discord_message(
                        f"webhook_server: BBR wave 多單 setup 已完成 3 根，等待黑K，BBR={prev_bbr} -> {curr_bbr}"
                    )
        else:
            _reset_bbr_wave_setup(state, "bull")

        red_candle = _is_red_candle(curr_row)
        black_candle = _is_black_candle(curr_row)

        if bool(state.get("short_ready", False)) and red_candle and curr_bbr < 1:
            _save_bbr_wave_state(state)
            _mark_bbr_wave_pending("enter", "bear")
            shortcycle_send_discord_message(
                f"webhook_server: BBR wave 空單進場條件成立，紅K確認，BBR={prev_bbr} -> {curr_bbr}"
            )
            _trigger_bbr_wave_entry("bear", curr_close, f"BBR setup ready with red candle; prev={prev_bbr}, curr={curr_bbr}")
            return True

        if bool(state.get("long_ready", False)) and black_candle and curr_bbr > 0:
            _save_bbr_wave_state(state)
            _mark_bbr_wave_pending("enter", "bull")
            shortcycle_send_discord_message(
                f"webhook_server: BBR wave 多單進場條件成立，黑K確認，BBR={prev_bbr} -> {curr_bbr}"
            )
            _trigger_bbr_wave_entry("bull", curr_close, f"BBR setup ready with black candle; prev={prev_bbr}, curr={curr_bbr}")
            return True

        _save_bbr_wave_state(state)
        return False


def _default_tt_bbr_state() -> dict:
    """建立 TT/BBR 狀態策略的預設狀態。"""
    return {
        "position_side": "",
        "position_entry_price": "",
        "position_since": "",
        "pending_side": "",
        "pending_since": "",
    }


def _load_tt_bbr_state() -> dict:
    """讀取 TT/BBR 狀態檔。"""
    state = _default_tt_bbr_state()
    if not os.path.isfile(TT_BBR_STATE_PATH):
        return state

    try:
        with open(TT_BBR_STATE_PATH, "r", encoding="utf-8") as handle:
            raw_state = json.load(handle)
    except Exception:
        return state

    if not isinstance(raw_state, dict):
        return state

    state["position_side"] = str(raw_state.get("position_side", "")).strip().lower()
    state["position_entry_price"] = raw_state.get("position_entry_price", "")
    state["position_since"] = str(raw_state.get("position_since", "")).strip()
    state["pending_side"] = str(raw_state.get("pending_side", "")).strip().lower()
    state["pending_since"] = str(raw_state.get("pending_since", "")).strip()
    return state


def _save_tt_bbr_state(state: dict) -> None:
    """儲存 TT/BBR 狀態檔。"""
    os.makedirs(os.path.dirname(TT_BBR_STATE_PATH), exist_ok=True)
    with open(TT_BBR_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _clear_tt_bbr_pending(state: dict) -> None:
    """清除 TT/BBR 的等待確認狀態。"""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _set_tt_bbr_pending(state: dict, side: str) -> None:
    """設定 TT/BBR 等待多或空的確認狀態。"""
    state["pending_side"] = side
    state["pending_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _set_tt_bbr_position(state: dict, side: str, entry_price: float) -> None:
    """設定 TT/BBR 目前持有的方向。"""
    state["position_side"] = side
    state["position_entry_price"] = entry_price
    state["position_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    _clear_tt_bbr_pending(state)


def _clear_tt_bbr_position(state: dict) -> None:
    """清除 TT/BBR 目前持有的方向。"""
    state["position_side"] = ""
    state["position_entry_price"] = ""
    state["position_since"] = ""


def _parse_tt_bbr_pending_since(raw_value: str) -> datetime | None:
    """把 TT/BBR pending 時間字串轉成 datetime。"""
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except ValueError:
        return None


def _tt_bbr_pending_expired(state: dict) -> bool:
    """判斷 TT/BBR 等待確認是否超時。"""
    pending_since = _parse_tt_bbr_pending_since(str(state.get("pending_since", "")).strip())
    if pending_since is None:
        return False
    return (datetime.now(TZ) - pending_since).total_seconds() > TT_BBR_PENDING_TIMEOUT_SECONDS


def _apply_tt_bbr_strategy() -> bool:
    """只看 close 與 TT 區間的趨勢策略，不接下單。"""
    with STRATEGY_LOCK:
        rows = _read_last_n_rows(CSV_FILE_1MIN, 2)
        if len(rows) < 2:
            return False

        _, curr_row = rows[-2], rows[-1]
        curr_close = _to_float(curr_row.get("Close"))
        tt_short = _to_float(curr_row.get("tt_short"))
        tt_long = _to_float(curr_row.get("tt_long"))
        if curr_close is None or tt_short is None or tt_long is None:
            return False

        upper_tt = max(tt_short, tt_long)
        lower_tt = min(tt_short, tt_long)
        close_above_both = curr_close > upper_tt
        close_below_both = curr_close < lower_tt
        close_between_tt = not close_above_both and not close_below_both

        state = _load_tt_bbr_state()
        position_side = str(state.get("position_side", "")).strip().lower()
        position_entry_price = _to_float(state.get("position_entry_price"))
        position_since = str(state.get("position_since", "")).strip()

        if position_side == "bull":
            if close_between_tt:
                note = f"exit bull at {curr_close}, entry={position_entry_price}, since={position_since}, tt_short={tt_short}, tt_long={tt_long}"
                shortcycle_send_discord_message(
                    f"webhook_server: TT/BBR 多單出場，close={curr_close}, tt_short={tt_short}, tt_long={tt_long}"
                )
                _append_tt_bbr_trade("exit", "bull", curr_close, note)
                _clear_tt_bbr_position(state)
                _save_tt_bbr_state(state)
                return True
            if close_below_both:
                exit_note = f"exit bull at {curr_close}, entry={position_entry_price}, since={position_since}, tt_short={tt_short}, tt_long={tt_long}"
                enter_note = f"enter bear at {curr_close}, tt_short={tt_short}, tt_long={tt_long}"
                shortcycle_send_discord_message(
                    f"webhook_server: TT/BBR 多單反手空單，close={curr_close}, tt_short={tt_short}, tt_long={tt_long}"
                )
                _append_tt_bbr_trade("exit", "bull", curr_close, exit_note)
                _append_tt_bbr_trade("enter", "bear", curr_close, enter_note)
                _set_tt_bbr_position(state, "bear", curr_close)
                _save_tt_bbr_state(state)
                return True
            _save_tt_bbr_state(state)
            return False

        if position_side == "bear":
            if close_between_tt:
                note = f"exit bear at {curr_close}, entry={position_entry_price}, since={position_since}, tt_short={tt_short}, tt_long={tt_long}"
                shortcycle_send_discord_message(
                    f"webhook_server: TT/BBR 空單出場，close={curr_close}, tt_short={tt_short}, tt_long={tt_long}"
                )
                _append_tt_bbr_trade("exit", "bear", curr_close, note)
                _clear_tt_bbr_position(state)
                _save_tt_bbr_state(state)
                return True
            if close_above_both:
                exit_note = f"exit bear at {curr_close}, entry={position_entry_price}, since={position_since}, tt_short={tt_short}, tt_long={tt_long}"
                enter_note = f"enter bull at {curr_close}, tt_short={tt_short}, tt_long={tt_long}"
                shortcycle_send_discord_message(
                    f"webhook_server: TT/BBR 空單反手多單，close={curr_close}, tt_short={tt_short}, tt_long={tt_long}"
                )
                _append_tt_bbr_trade("exit", "bear", curr_close, exit_note)
                _append_tt_bbr_trade("enter", "bull", curr_close, enter_note)
                _set_tt_bbr_position(state, "bull", curr_close)
                _save_tt_bbr_state(state)
                return True
            _save_tt_bbr_state(state)
            return False

        if close_above_both:
            note = f"enter bull at {curr_close}, tt_short={tt_short}, tt_long={tt_long}"
            shortcycle_send_discord_message(
                f"webhook_server: TT/BBR 多單進場，close={curr_close}, tt_short={tt_short}, tt_long={tt_long}"
            )
            _append_tt_bbr_trade("enter", "bull", curr_close, note)
            _set_tt_bbr_position(state, "bull", curr_close)
            _save_tt_bbr_state(state)
            return True

        if close_below_both:
            note = f"enter bear at {curr_close}, tt_short={tt_short}, tt_long={tt_long}"
            shortcycle_send_discord_message(
                f"webhook_server: TT/BBR 空單進場，close={curr_close}, tt_short={tt_short}, tt_long={tt_long}"
            )
            _append_tt_bbr_trade("enter", "bear", curr_close, note)
            _set_tt_bbr_position(state, "bear", curr_close)
            _save_tt_bbr_state(state)
            return True

        _save_tt_bbr_state(state)
        return False


# 獲取webhook並處理
class WebhookHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        """接收 webhook，寫入 1 分資料並依序執行策略。"""
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
                    tt_short = str(data.get('tt_short', '')).strip()
                    tt_long = str(data.get('tt_long', '')).strip()
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
                            tt_short,
                            tt_long,
                            bbr,
                        ])

                    # _apply_bbr_strategy()
                    _apply_bbr960_strategy()
                    _apply_bbr_wave_strategy()
                    _apply_tt_bbr_strategy()
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
        """提供簡單 health check。"""
        # Health check
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Webhook Server Running")
        else:
            self.send_error(404, "Not Found")

def run_server():
    """啟動 webhook HTTP server 與每日清理背景執行緒。"""
    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    def _daily_clear_worker():
        """每天固定時間裁切 1 分 K CSV 只保留最後幾筆。"""
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
