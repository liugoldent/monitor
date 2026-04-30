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

from auto_trade_shortCycle import send_discord_message as _base_shortcycle_send_discord_message

# Configuration
PORT = 8080
CSV_FILE_1MIN = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_1min.csv")
CSV_FILE_5MIN = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_5min.csv")
CSV_FILE_10MIN = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_10min.csv")
CSV_FILE_15MIN = os.path.join(os.path.dirname(__file__), "tv_doc", "webhook_data_15min.csv")
MXF_VALUE_CSV_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "mxf_value.csv")
H_TRADE_CSV_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "h_trade.csv")
H_FOLLOW_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "h_follow_trade.csv")
H_FOLLOW_STATE_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "h_follow_state.json")
TT_MXF_TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "tt_mxf_trade.csv")
TT_MXF_STATE_PATH = os.path.join(os.path.dirname(__file__), "tv_doc", "tt_mxf_state.json")
CLEAR_TIME = (8, 0)
CLEAR_WEEKDAY = 5
CLEAR_KEEP_ROWS = 5
TZ = ZoneInfo("Asia/Taipei")
LONG_CA_PATH = os.getenv("CA_PATH") or os.path.join(os.path.dirname(__file__), "Sinopac.pfx")
TT_MXF_ENABLE_LONG = False
TT_MXF_LONG_MAX_BREAKOUT_POINTS = 30.0
TT_MXF_SHORT_BBR_MAX = 0.5
TT_MXF_ENTRY_BREAKOUT_BUFFER_POINTS = 10.0
TT_MXF_STOP_LOSS_POINTS = 10.0
TT_MXF_TAKE_PROFIT_POINTS = 10.0
H_FOLLOW_NEAR_TOUCH_POINTS = 25.0

CSV_FILE_BY_TIMEFRAME = {
    "1": CSV_FILE_1MIN,
    "5": CSV_FILE_5MIN,
    "10": CSV_FILE_10MIN,
    "15": CSV_FILE_15MIN,
}

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
TT_MXF_PENDING_TIMEOUT_SECONDS = 60 * 60


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


def _append_webhook_row(path: str, row: list[object]) -> None:
    """把 webhook K 棒資料追加到指定 CSV。"""
    _ensure_csv_header(path, CSV_HEADER)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(row)


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


def shortcycle_send_discord_message(content: str) -> None:
    """送出短線 Discord 訊息，並附上最新 MXF 資料。"""
    _base_shortcycle_send_discord_message(_append_mxf_context(content))


def _default_tt_mxf_state() -> dict:
    """建立 TT/MXF 保守策略的預設狀態。"""
    return {
        "position_side": "",
        "position_entry_price": "",
        "position_since": "",
        "pending_action": "",
        "pending_side": "",
        "pending_since": "",
    }


def _load_tt_mxf_state() -> dict:
    """讀取 TT/MXF 保守策略的狀態檔。"""
    state = _default_tt_mxf_state()
    if not os.path.isfile(TT_MXF_STATE_PATH):
        return state

    try:
        with open(TT_MXF_STATE_PATH, "r", encoding="utf-8") as handle:
            raw_state = json.load(handle)
    except Exception:
        return state

    if not isinstance(raw_state, dict):
        return state

    state["position_side"] = str(raw_state.get("position_side", "")).strip().lower()
    state["position_entry_price"] = raw_state.get("position_entry_price", "")
    state["position_since"] = str(raw_state.get("position_since", "")).strip()
    state["pending_action"] = str(raw_state.get("pending_action", "")).strip().lower()
    state["pending_side"] = str(raw_state.get("pending_side", "")).strip().lower()
    state["pending_since"] = str(raw_state.get("pending_since", "")).strip()
    return state


def _save_tt_mxf_state(state: dict) -> None:
    """儲存 TT/MXF 保守策略的狀態檔。"""
    os.makedirs(os.path.dirname(TT_MXF_STATE_PATH), exist_ok=True)
    with open(TT_MXF_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _clear_tt_mxf_position(state: dict) -> None:
    """清除 TT/MXF 的持倉狀態。"""
    state["position_side"] = ""
    state["position_entry_price"] = ""
    state["position_since"] = ""


def _clear_tt_mxf_pending(state: dict) -> None:
    """清除 TT/MXF 的 pending 狀態。"""
    state["pending_action"] = ""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _mark_tt_mxf_pending(state: dict, action: str, side: str) -> None:
    """標記 TT/MXF 目前有待執行的動作。"""
    state["pending_action"] = action
    state["pending_side"] = side
    state["pending_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _parse_tt_mxf_pending_since(raw_value: str) -> datetime | None:
    """把 TT/MXF pending 時間字串轉成 datetime。"""
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except ValueError:
        return None


def _is_tt_mxf_pending_expired(state: dict) -> bool:
    """判斷 TT/MXF pending 是否已經超時。"""
    pending_action = str(state.get("pending_action", "")).strip().lower()
    if not pending_action:
        return False

    pending_since = _parse_tt_mxf_pending_since(str(state.get("pending_since", "")).strip())
    if pending_since is None:
        return True

    return (datetime.now(TZ) - pending_since).total_seconds() > TT_MXF_PENDING_TIMEOUT_SECONDS


def _set_tt_mxf_position(state: dict, side: str, entry_price: float) -> None:
    """設定 TT/MXF 的持倉狀態。"""
    state["position_side"] = side
    state["position_entry_price"] = entry_price
    state["position_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_tt_mxf_trade_log_header() -> None:
    """確認 TT/MXF 交易紀錄檔的表頭格式。"""
    header = [
        "timestamp",
        "action",
        "side",
        "price",
        "note",
        "signal",
        "trend",
        "tx_bvav",
        "mtx_bvav",
        "mtx_bvav_avg",
        "bbr",
    ]
    if not os.path.isfile(TT_MXF_TRADE_LOG_PATH):
        os.makedirs(os.path.dirname(TT_MXF_TRADE_LOG_PATH), exist_ok=True)
        with open(TT_MXF_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
        return

    try:
        with open(TT_MXF_TRADE_LOG_PATH, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except Exception:
        return

    if not rows:
        with open(TT_MXF_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
        return

    if rows[0] == header:
        return

    data_rows = rows[1:]
    with open(TT_MXF_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(data_rows)


def _append_tt_mxf_trade(action: str, side: str, price: float, note: str = "", mxf_row: dict | None = None, bbr: float | None = None) -> None:
    """寫入 TT/MXF 策略進出場紀錄。"""
    _ensure_tt_mxf_trade_log_header()
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    snapshot = mxf_row or {}
    with open(TT_MXF_TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            timestamp,
            action,
            side,
            price,
            note,
            str(snapshot.get("signal", "")).strip(),
            str(snapshot.get("trend", "")).strip(),
            snapshot.get("tx_bvav", ""),
            snapshot.get("mtx_bvav", ""),
            snapshot.get("mtx_bvav_avg", ""),
            "" if bbr is None else _format_mxf_number(bbr),
        ])


def _is_mxf_bull(row: dict) -> bool:
    """判斷 MXF 是否偏多。"""
    signal = str(row.get("signal", "")).strip().lower()
    trend = str(row.get("trend", "")).strip().lower()
    return signal == "bull" and trend == "gold"


def _is_mxf_bear(row: dict) -> bool:
    """判斷 MXF 是否偏空。"""
    signal = str(row.get("signal", "")).strip().lower()
    trend = str(row.get("trend", "")).strip().lower()
    return signal == "bear" and trend == "death"


def _get_tt_mxf_unrealized_pnl(side: str, entry_price: float, close_price: float) -> float | None:
    """依持倉方向計算目前未實現損益。"""
    if entry_price is None or close_price is None:
        return None
    if side == "bull":
        return close_price - entry_price
    if side == "bear":
        return entry_price - close_price
    return None


def _get_latest_h_trade_entry() -> dict | None:
    """讀取 `h_trade.csv` 最新一筆 `enter` 記錄。"""
    if not os.path.isfile(H_TRADE_CSV_PATH):
        return None

    try:
        with open(H_TRADE_CSV_PATH, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except Exception:
        return None

    for row in reversed(rows):
        action = str(row.get("action", "")).strip().lower()
        side = str(row.get("side", "")).strip().lower()
        if action != "enter" or side not in {"bull", "bear"}:
            continue
        entry_price = _to_float(row.get("price"))
        if entry_price is None:
            continue
        return {
            "timestamp": str(row.get("timestamp", "")).strip(),
            "side": side,
            "price": entry_price,
        }
    return None


def _default_h_follow_state() -> dict:
    """建立 H follow 策略的預設狀態。"""
    return {
        "position_side": "",
        "position_entry_price": "",
        "position_since": "",
        "take_profit_armed": "",
        "pending_action": "",
        "pending_side": "",
        "pending_since": "",
    }


def _load_h_follow_state() -> dict:
    """讀取 H follow 策略狀態檔。"""
    state = _default_h_follow_state()
    if not os.path.isfile(H_FOLLOW_STATE_PATH):
        return state

    try:
        with open(H_FOLLOW_STATE_PATH, "r", encoding="utf-8") as handle:
            raw_state = json.load(handle)
    except Exception:
        return state

    if not isinstance(raw_state, dict):
        return state

    state["position_side"] = str(raw_state.get("position_side", "")).strip().lower()
    state["position_entry_price"] = raw_state.get("position_entry_price", "")
    state["position_since"] = str(raw_state.get("position_since", "")).strip()
    state["take_profit_armed"] = str(raw_state.get("take_profit_armed", "")).strip().lower()
    state["pending_action"] = str(raw_state.get("pending_action", "")).strip().lower()
    state["pending_side"] = str(raw_state.get("pending_side", "")).strip().lower()
    state["pending_since"] = str(raw_state.get("pending_since", "")).strip()
    return state


def _save_h_follow_state(state: dict) -> None:
    """儲存 H follow 策略狀態檔。"""
    os.makedirs(os.path.dirname(H_FOLLOW_STATE_PATH), exist_ok=True)
    with open(H_FOLLOW_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _clear_h_follow_position(state: dict) -> None:
    """清除 H follow 的持倉狀態。"""
    state["position_side"] = ""
    state["position_entry_price"] = ""
    state["position_since"] = ""
    state["take_profit_armed"] = ""
    state["pending_action"] = ""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _set_h_follow_position(state: dict, side: str, entry_price: float) -> None:
    """設定 H follow 的持倉狀態。"""
    state["position_side"] = side
    state["position_entry_price"] = entry_price
    state["position_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    state["take_profit_armed"] = ""
    state["pending_action"] = ""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _mark_h_follow_pending(state: dict, action: str, side: str) -> None:
    """標記 H follow 目前有待執行的動作。"""
    state["pending_action"] = action
    state["pending_side"] = side
    state["pending_since"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _clear_h_follow_pending(state: dict) -> None:
    """清除 H follow pending 狀態。"""
    state["pending_action"] = ""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _ensure_h_follow_trade_log_header() -> None:
    """確認 H follow 交易紀錄檔的表頭格式。"""
    header = [
        "timestamp",
        "action",
        "side",
        "price",
        "note",
        "reference_side",
        "reference_entry_price",
        "reference_close",
        "reference_pnl",
        "ma_n200",
        "ma_p200",
    ]
    if not os.path.isfile(H_FOLLOW_TRADE_LOG_PATH):
        os.makedirs(os.path.dirname(H_FOLLOW_TRADE_LOG_PATH), exist_ok=True)
        with open(H_FOLLOW_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(header)
        return

    try:
        with open(H_FOLLOW_TRADE_LOG_PATH, "r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except Exception:
        with open(H_FOLLOW_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(header)
        return

    if not rows or rows[0] != header:
        with open(H_FOLLOW_TRADE_LOG_PATH, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows[1:] if rows else [])


def _append_h_follow_trade(
    action: str,
    side: str,
    price: float,
    note: str = "",
    reference_side: str = "",
    reference_entry_price: float | None = None,
    reference_close: float | None = None,
    reference_pnl: float | None = None,
    ma_n200: float | None = None,
    ma_p200: float | None = None,
) -> None:
    """寫入 H follow 策略的進出場紀錄。"""
    _ensure_h_follow_trade_log_header()
    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(H_FOLLOW_TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow([
            timestamp,
            action,
            side,
            price,
            note,
            reference_side,
            "" if reference_entry_price is None else reference_entry_price,
            "" if reference_close is None else reference_close,
            "" if reference_pnl is None else reference_pnl,
            "" if ma_n200 is None else ma_n200,
            "" if ma_p200 is None else ma_p200,
        ])


def _get_h_follow_unrealized_pnl(side: str, entry_price: float, close_price: float) -> float | None:
    """依 H follow 持倉方向計算未實現損益。"""
    return _get_tt_mxf_unrealized_pnl(side, entry_price, close_price)


def _is_close_above_level(close_price: float, level_price: float) -> bool:
    return close_price > level_price


def _is_close_below_level(close_price: float, level_price: float) -> bool:
    return close_price < level_price


def _is_within_near_touch(close_price: float, level_price: float, points: float) -> bool:
    return abs(close_price - level_price) <= points


def _trigger_tt_mxf_entry(side: str, close_price: float, reason: str, mxf_row: dict, bbr: float) -> None:
    """觸發 TT/MXF 進場通知，並只更新紀錄與狀態。"""
    def _runner() -> None:
        try:
            _append_tt_mxf_trade("enter", side, close_price, reason, mxf_row=mxf_row, bbr=bbr)
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，TT/MXF 進場訊號 {side}，原因：{reason}（僅通知，不下單）"
            )
            print(f"🔔 TT/MXF entry alert({side}) because {reason}")
        except Exception as exc:
            print(f"❌ TT/MXF entry alert({side}) failed: {exc}")
        finally:
            with STRATEGY_LOCK:
                state = _load_tt_mxf_state()
                _clear_tt_mxf_pending(state)
                _set_tt_mxf_position(state, side, close_price)
                _save_tt_mxf_state(state)
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _trigger_tt_mxf_exit(side: str, close_price: float, reason: str, mxf_row: dict, bbr: float) -> None:
    """觸發 TT/MXF 平倉通知，並只更新紀錄與狀態。"""
    def _runner() -> None:
        try:
            _append_tt_mxf_trade("exit", side, close_price, reason, mxf_row=mxf_row, bbr=bbr)
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，TT/MXF 平倉訊號 {side}，原因：{reason}（僅通知，不下單）"
            )
            print(f"🔔 TT/MXF exit alert because {reason}")
        except Exception as exc:
            print(f"❌ TT/MXF exit alert failed: {exc}")
        finally:
            with STRATEGY_LOCK:
                state = _load_tt_mxf_state()
                _clear_tt_mxf_pending(state)
                _clear_tt_mxf_position(state)
                _save_tt_mxf_state(state)
            sys.stdout.flush()

    Thread(target=_runner, daemon=True).start()


def _apply_tt_mxf_strategy() -> bool:
    """TT/MXF 保守策略。

    進場規則:
    - 多單: 連續 2 根 K 都收在 TT 區間上方
    - 多單: MXF 連續 2 筆維持 bull + gold
    - 空單: 連續 2 根 K 都收在 TT 區間下方
    - 空單: MXF 連續 2 筆維持 bear + death
    - BBR 只當作順勢濾網，不拿來追高殺低

    出場規則:
    - 多單: 價格回到 TT 區間內，或 MXF 轉反向
    - 空單: 價格回到 TT 區間內，或 MXF 轉反向

    風控設計:
    - 每次只允許一個 position
    - 任何下單/平倉流程都會寫入 pending，避免重複觸發
    - pending 超過 60 分鐘會自動解除
    """
    with STRATEGY_LOCK:
        price_rows = _read_last_n_rows(CSV_FILE_1MIN, 2)
        mxf_rows = _read_last_n_rows(MXF_VALUE_CSV_PATH, 2)
        if len(price_rows) < 2 or len(mxf_rows) < 2:
            return False

        prev_price_row, curr_price_row = price_rows[-2], price_rows[-1]
        prev_mxf_row, curr_mxf_row = mxf_rows[-2], mxf_rows[-1]

        prev_close = _to_float(prev_price_row.get("Close"))
        curr_close = _to_float(curr_price_row.get("Close"))
        prev_tt_short = _to_float(prev_price_row.get("tt_short"))
        prev_tt_long = _to_float(prev_price_row.get("tt_long"))
        curr_tt_short = _to_float(curr_price_row.get("tt_short"))
        curr_tt_long = _to_float(curr_price_row.get("tt_long"))
        prev_bbr = _to_float(prev_price_row.get("BBR"))
        curr_bbr = _to_float(curr_price_row.get("BBR"))

        if (
            prev_close is None
            or curr_close is None
            or prev_tt_short is None
            or prev_tt_long is None
            or curr_tt_short is None
            or curr_tt_long is None
            or prev_bbr is None
            or curr_bbr is None
        ):
            return False

        prev_upper_tt = max(prev_tt_short, prev_tt_long)
        prev_lower_tt = min(prev_tt_short, prev_tt_long)
        curr_upper_tt = max(curr_tt_short, curr_tt_long)
        curr_lower_tt = min(curr_tt_short, curr_tt_long)

        # 先看 TT 方向，只有連續 2 根同側才視為有效趨勢。
        prev_close_above_tt = prev_close > prev_upper_tt
        curr_close_above_tt = curr_close > curr_upper_tt
        prev_close_below_tt = prev_close < prev_lower_tt
        curr_close_below_tt = curr_close < curr_lower_tt
        curr_close_inside_tt = not curr_close_above_tt and not curr_close_below_tt

        # MXF 只接受「同方向連續兩筆」的力道確認。
        mxf_bull = _is_mxf_bull(prev_mxf_row) and _is_mxf_bull(curr_mxf_row)
        mxf_bear = _is_mxf_bear(prev_mxf_row) and _is_mxf_bear(curr_mxf_row)

        # BBR 只是順勢濾網，不單獨當進場訊號。
        long_momentum_ok = curr_bbr >= prev_bbr and curr_bbr >= 0
        short_momentum_ok = curr_bbr <= prev_bbr and curr_bbr <= 1

        state = _load_tt_mxf_state()
        position_side = str(state.get("position_side", "")).strip().lower()
        position_entry_price = _to_float(state.get("position_entry_price"))
        position_since = str(state.get("position_since", "")).strip()
        pending_action = str(state.get("pending_action", "")).strip().lower()
        if pending_action:
            if _is_tt_mxf_pending_expired(state):
                _clear_tt_mxf_pending(state)
                _save_tt_mxf_state(state)
            else:
                return False

        if position_side == "bull":
            # 多單持有時，只要回到 TT 區間內或 MXF 翻空，就先保守出場。
            bull_unrealized_pnl = _get_tt_mxf_unrealized_pnl("bull", position_entry_price, curr_close)
            if (
                (bull_unrealized_pnl is not None and bull_unrealized_pnl <= -TT_MXF_STOP_LOSS_POINTS)
                or (bull_unrealized_pnl is not None and bull_unrealized_pnl >= TT_MXF_TAKE_PROFIT_POINTS)
                or curr_close_inside_tt
                or mxf_bear
                or curr_close_below_tt
            ):
                note = (
                    f"exit bull at {curr_close}, entry={position_entry_price}, since={position_since}, "
                    f"tt_short={curr_tt_short}, tt_long={curr_tt_long}, signal={curr_mxf_row.get('signal', '')}, trend={curr_mxf_row.get('trend', '')}"
                )
                _mark_tt_mxf_pending(state, "exit", "bull")
                _save_tt_mxf_state(state)
                _trigger_tt_mxf_exit("bull", curr_close, note, curr_mxf_row, curr_bbr)
                return True
            _save_tt_mxf_state(state)
            return False

        if position_side == "bear":
            # 空單持有時，只要回到 TT 區間內或 MXF 翻多，就先保守出場。
            bear_unrealized_pnl = _get_tt_mxf_unrealized_pnl("bear", position_entry_price, curr_close)
            if (
                (bear_unrealized_pnl is not None and bear_unrealized_pnl <= -TT_MXF_STOP_LOSS_POINTS)
                or (bear_unrealized_pnl is not None and bear_unrealized_pnl >= TT_MXF_TAKE_PROFIT_POINTS)
                or curr_close_inside_tt
                or mxf_bull
                or curr_close_above_tt
            ):
                note = (
                    f"exit bear at {curr_close}, entry={position_entry_price}, since={position_since}, "
                    f"tt_short={curr_tt_short}, tt_long={curr_tt_long}, signal={curr_mxf_row.get('signal', '')}, trend={curr_mxf_row.get('trend', '')}"
                )
                _mark_tt_mxf_pending(state, "exit", "bear")
                _save_tt_mxf_state(state)
                _trigger_tt_mxf_exit("bear", curr_close, note, curr_mxf_row, curr_bbr)
                return True
            _save_tt_mxf_state(state)
            return False

        if (
            prev_close_above_tt
            and curr_close_above_tt
            and mxf_bull
            and long_momentum_ok
            and TT_MXF_ENABLE_LONG
            and (curr_close - curr_upper_tt) <= TT_MXF_LONG_MAX_BREAKOUT_POINTS
        ):
            # 多單只做順勢突破，不做區間內追價。
            note = (
                f"enter bull at {curr_close}, prev_close={prev_close}, tt_short={curr_tt_short}, tt_long={curr_tt_long}, "
                f"signal={curr_mxf_row.get('signal', '')}, trend={curr_mxf_row.get('trend', '')}"
            )
            _mark_tt_mxf_pending(state, "enter", "bull")
            _save_tt_mxf_state(state)
            _trigger_tt_mxf_entry("bull", curr_close, note, curr_mxf_row, curr_bbr)
            return True

        if (
            prev_close_below_tt
            and curr_close_below_tt
            and mxf_bear
            and short_momentum_ok
            and curr_close <= curr_lower_tt - TT_MXF_ENTRY_BREAKOUT_BUFFER_POINTS
            and curr_bbr <= TT_MXF_SHORT_BBR_MAX
        ):
            # 空單同樣只做順勢跌破，不在 TT 中間亂追。
            note = (
                f"enter bear at {curr_close}, prev_close={prev_close}, tt_short={curr_tt_short}, tt_long={curr_tt_long}, "
                f"signal={curr_mxf_row.get('signal', '')}, trend={curr_mxf_row.get('trend', '')}"
            )
            _mark_tt_mxf_pending(state, "enter", "bear")
            _save_tt_mxf_state(state)
            _trigger_tt_mxf_entry("bear", curr_close, note, curr_mxf_row, curr_bbr)
            return True

        _save_tt_mxf_state(state)
        return False


def _apply_h_follow_strategy() -> bool:
    """依 `h_trade.csv` 最新持倉方向，追蹤 1 分 K 的 MA_N200 / MA_P200 交叉。

    規則：
    - 只看最新一筆 `enter`
    - 若最新 `enter` 是 bull 且目前 close 對該筆 entry 仍為獲利，才允許做多
    - 若最新 `enter` 是 bear 且目前 close 對該筆 entry 仍為獲利，才允許做空
    - 多單進場會吃三種型態：
      1. close 正式上穿 MA_N200
      2. 影線先打到 MA_N200，但 close 收回 MA_N200 上方
      3. close 跟 MA_N200 很接近，視為打腳確認
    - 多單停損是 close 再次跌破 MA_N200
    - 多單停利是先突破 MA_P200，之後再跌破 MA_P200
    - 空單進場則相反，吃三種型態：
      1. close 正式下穿 MA_P200
      2. 影線先插到 MA_P200 上方，但 close 收回 MA_P200 下方
      3. close 跟 MA_P200 很接近，視為反彈壓力確認
    - 空單停損是 close 再次站上 MA_P200
    - 空單停利是先跌破 MA_N200，之後再站回 MA_N200
    """
    with STRATEGY_LOCK:
        price_rows = _read_last_n_rows(CSV_FILE_1MIN, 2)
        if len(price_rows) < 2:
            return False

        latest_h_trade = _get_latest_h_trade_entry()
        if latest_h_trade is None:
            return False

        prev_price_row, curr_price_row = price_rows[-2], price_rows[-1]
        prev_close = _to_float(prev_price_row.get("Close"))
        curr_close = _to_float(curr_price_row.get("Close"))
        curr_low = _to_float(curr_price_row.get("Low"))
        curr_high = _to_float(curr_price_row.get("High"))
        prev_ma_p200 = _to_float(prev_price_row.get("MA_P200"))
        curr_ma_p200 = _to_float(curr_price_row.get("MA_P200"))
        prev_ma_n200 = _to_float(prev_price_row.get("MA_N200"))
        curr_ma_n200 = _to_float(curr_price_row.get("MA_N200"))

        if (
            prev_close is None
            or curr_close is None
            or curr_low is None
            or curr_high is None
            or prev_ma_p200 is None
            or curr_ma_p200 is None
            or prev_ma_n200 is None
            or curr_ma_n200 is None
        ):
            return False

        latest_side = str(latest_h_trade.get("side", "")).strip().lower()
        latest_entry_price = _to_float(latest_h_trade.get("price"))
        if latest_side not in {"bull", "bear"} or latest_entry_price is None:
            return False

        reference_pnl = _get_h_follow_unrealized_pnl(latest_side, latest_entry_price, curr_close)
        if reference_pnl is None:
            return False

        state = _load_h_follow_state()
        position_side = str(state.get("position_side", "")).strip().lower()
        position_entry_price = _to_float(state.get("position_entry_price"))
        position_since = str(state.get("position_since", "")).strip()
        # `take_profit_armed` 用來表示：已經先碰過遠端目標線，接下來只等反向穿回來才出場。
        take_profit_armed = str(state.get("take_profit_armed", "")).strip().lower() == "true"
        pending_action = str(state.get("pending_action", "")).strip().lower()
        if pending_action:
            if _is_tt_mxf_pending_expired(state):
                _clear_h_follow_pending(state)
                _save_h_follow_state(state)
            else:
                return False

        # 多單進場訊號：
        # cross = close 正式上穿 MA_N200
        # wick = 影線打到 MA_N200，但收盤收回上方
        # near = 收盤接近 MA_N200，當作打腳或貼線確認
        long_cross_signal = prev_close <= prev_ma_n200 and curr_close > curr_ma_n200
        long_wick_signal = curr_low <= curr_ma_n200 and curr_close > curr_ma_n200
        long_near_signal = _is_close_above_level(curr_close, curr_ma_n200) and _is_within_near_touch(
            curr_close, curr_ma_n200, H_FOLLOW_NEAR_TOUCH_POINTS
        )

        # 空單進場訊號：
        # cross = close 正式下穿 MA_P200
        # wick = 影線插到 MA_P200 上方，但收盤收回下方
        # near = 收盤接近 MA_P200，當作壓力測試確認
        short_cross_signal = prev_close >= prev_ma_p200 and curr_close < curr_ma_p200
        short_wick_signal = curr_high >= curr_ma_p200 and curr_close < curr_ma_p200
        short_near_signal = _is_close_below_level(curr_close, curr_ma_p200) and _is_within_near_touch(
            curr_close, curr_ma_p200, H_FOLLOW_NEAR_TOUCH_POINTS
        )

        prev_long_cross = long_cross_signal or long_wick_signal or long_near_signal
        prev_short_cross = short_cross_signal or short_wick_signal or short_near_signal
        long_stop_loss = prev_close >= prev_ma_n200 and curr_close < curr_ma_n200
        short_stop_loss = prev_close <= prev_ma_p200 and curr_close > curr_ma_p200
        long_take_profit_arm = curr_close > curr_ma_p200
        short_take_profit_arm = curr_close < curr_ma_n200
        long_take_profit_exit = take_profit_armed and prev_close >= prev_ma_p200 and curr_close < curr_ma_p200
        short_take_profit_exit = take_profit_armed and prev_close <= prev_ma_n200 and curr_close > curr_ma_n200

        if position_side == "bull":
            # 多單持有中：先看停損，再看是否已經啟動停利條件。
            if long_stop_loss or long_take_profit_exit:
                reason = "stop loss" if long_stop_loss else "take profit"
                note = (
                    f"exit bull at {curr_close}, entry={position_entry_price}, since={position_since}, "
                    f"reason={reason}, latest_h_side={latest_side}, latest_h_entry={latest_entry_price}, ref_pnl={reference_pnl}, "
                    f"ma_n200={curr_ma_n200}, ma_p200={curr_ma_p200}"
                )
                _mark_h_follow_pending(state, "exit", "bull")
                _save_h_follow_state(state)
                _append_h_follow_trade(
                    "exit",
                    "bull",
                    curr_close,
                    note,
                    reference_side=latest_side,
                    reference_entry_price=latest_entry_price,
                    reference_close=curr_close,
                    reference_pnl=reference_pnl,
                    ma_n200=curr_ma_n200,
                    ma_p200=curr_ma_p200,
                )
                _clear_h_follow_position(state)
                _save_h_follow_state(state)
                return True

            if not take_profit_armed and long_take_profit_arm:
                state["take_profit_armed"] = "true"
                _save_h_follow_state(state)
            else:
                _save_h_follow_state(state)
            return False

        if position_side == "bear":
            # 空單持有中：先看停損，再看是否已經啟動停利條件。
            if short_stop_loss or short_take_profit_exit:
                reason = "stop loss" if short_stop_loss else "take profit"
                note = (
                    f"exit bear at {curr_close}, entry={position_entry_price}, since={position_since}, "
                    f"reason={reason}, latest_h_side={latest_side}, latest_h_entry={latest_entry_price}, ref_pnl={reference_pnl}, "
                    f"ma_n200={curr_ma_n200}, ma_p200={curr_ma_p200}"
                )
                _mark_h_follow_pending(state, "exit", "bear")
                _save_h_follow_state(state)
                _append_h_follow_trade(
                    "exit",
                    "bear",
                    curr_close,
                    note,
                    reference_side=latest_side,
                    reference_entry_price=latest_entry_price,
                    reference_close=curr_close,
                    reference_pnl=reference_pnl,
                    ma_n200=curr_ma_n200,
                    ma_p200=curr_ma_p200,
                )
                _clear_h_follow_position(state)
                _save_h_follow_state(state)
                return True

            if not take_profit_armed and short_take_profit_arm:
                state["take_profit_armed"] = "true"
                _save_h_follow_state(state)
            else:
                _save_h_follow_state(state)
            return False

        # 只有當 h_trade 最新方向是 bull，且目前這筆 bull 是賺錢時，才允許啟動多單跟隨。
        if latest_side == "bull" and reference_pnl > 0 and prev_long_cross:
            entry_reason = "cross" if long_cross_signal else "wick" if long_wick_signal else "near"
            note = (
                f"enter bull at {curr_close}, reason={entry_reason}, latest_h_entry={latest_entry_price}, latest_h_pnl={reference_pnl}, "
                f"ref_side={latest_side}, ma_n200={curr_ma_n200}, ma_p200={curr_ma_p200}"
            )
            _mark_h_follow_pending(state, "enter", "bull")
            _save_h_follow_state(state)
            _append_h_follow_trade(
                "enter",
                "bull",
                curr_close,
                note,
                reference_side=latest_side,
                reference_entry_price=latest_entry_price,
                reference_close=curr_close,
                reference_pnl=reference_pnl,
                ma_n200=curr_ma_n200,
                ma_p200=curr_ma_p200,
            )
            _set_h_follow_position(state, "bull", curr_close)
            _save_h_follow_state(state)
            return True

        # 只有當 h_trade 最新方向是 bear，且目前這筆 bear 是賺錢時，才允許啟動空單跟隨。
        if latest_side == "bear" and reference_pnl > 0 and prev_short_cross:
            entry_reason = "cross" if short_cross_signal else "wick" if short_wick_signal else "near"
            note = (
                f"enter bear at {curr_close}, reason={entry_reason}, latest_h_entry={latest_entry_price}, latest_h_pnl={reference_pnl}, "
                f"ref_side={latest_side}, ma_n200={curr_ma_n200}, ma_p200={curr_ma_p200}"
            )
            _mark_h_follow_pending(state, "enter", "bear")
            _save_h_follow_state(state)
            _append_h_follow_trade(
                "enter",
                "bear",
                curr_close,
                note,
                reference_side=latest_side,
                reference_entry_price=latest_entry_price,
                reference_close=curr_close,
                reference_pnl=reference_pnl,
                ma_n200=curr_ma_n200,
                ma_p200=curr_ma_p200,
            )
            _set_h_follow_position(state, "bear", curr_close)
            _save_h_follow_state(state)
            return True

        _save_h_follow_state(state)
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
                        _apply_tt_mxf_strategy()
                        _apply_h_follow_strategy()
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
    """啟動 webhook HTTP server。

    保留 `webhook_data_1min.csv` 的完整歷史資料，不再自動裁切。
    """
    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    try:
        server_address = ('', PORT)
        httpd = ThreadingHTTPServer(server_address, WebhookHandler)
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
