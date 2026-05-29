"""H morning open short-to-long turn strategy.

This strategy emits signals only. It does not place orders.

Rules:
- Only reacts to H bear -> bull direction changes at the morning opening
  window: 08:46/08:47.
- The opening minute must be below MA_960 and mtx_bvav must be positive.
- Initial stop is 250 points against the entry.
- Once unrealized profit reaches 1000 points, exit on 600-point giveback.
- If H changes/exits before either rule, exit with H.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock

from strategy_common import build_shortcycle_send_discord_message, now_str, to_float


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
STRATEGY_STATE_DIR = TV_DOC_DIR / "strategy_state"
STRATEGY_ALERT_DIR = TV_DOC_DIR / "strategy_alerts"
H_TRADE_CSV_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_CSV_PATH = TV_DOC_DIR / "mxf_value.csv"
WEBHOOK_1M_CSV_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
STATE_PATH = STRATEGY_STATE_DIR / "h_open_turn_state.json"
ALERT_PATH = STRATEGY_ALERT_DIR / "h_open_turn_alert.csv"

OPENING_MINUTES = {"08:46", "08:47"}
INITIAL_STOP_POINTS = 250.0
TRAIL_START_POINTS = 1000.0
TRAIL_GIVEBACK_POINTS = 600.0
QUANTITY = 1

ALERT_HEADER = [
    "timestamp",
    "action",
    "side",
    "quantity",
    "entry_price",
    "close",
    "h_position_timestamp",
    "h_side",
    "h_entry_price",
    "open_minute",
    "ma960",
    "mtx_bvav",
    "unrealized_points",
    "max_unrealized_points",
    "giveback_points",
    "reason",
]

LOCK = RLock()
send_open_turn_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))


def _read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    state["updated_at"] = now_str()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_alert(signal: dict) -> None:
    exists = ALERT_PATH.exists()
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(ALERT_HEADER)
        writer.writerow([signal.get(key, "") for key in ALERT_HEADER])


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _minute_key(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _opening_minute_for_h_time(h_time: datetime) -> datetime:
    open_minute = h_time.replace(second=0, microsecond=0)
    if h_time.strftime("%H:%M") == "08:47":
        open_minute -= timedelta(minutes=1)
    return open_minute


def _latest_h_state() -> dict | None:
    rows = _read_csv_rows(H_TRADE_CSV_PATH)
    current: dict | None = None
    previous_exit: dict | None = None
    for row in rows:
        action = str(row.get("action") or "").strip().lower()
        timestamp = _parse_dt(row.get("timestamp"))
        side = str(row.get("side") or "").strip().lower()
        price = to_float(row.get("price"))
        if timestamp is None or price is None or side not in {"bull", "bear"}:
            continue
        if action == "exiting":
            previous_exit = {"timestamp": timestamp, "side": side, "price": price}
            current = None
        elif action == "enter":
            current = {
                "timestamp": timestamp,
                "side": side,
                "price": price,
                "previous_side": previous_exit.get("side") if previous_exit else "",
            }
    return current


def _position_key(position: dict | None) -> str:
    if not position:
        return ""
    timestamp = position.get("timestamp")
    if isinstance(timestamp, datetime):
        timestamp = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    return f"{timestamp}|{position.get('side', '')}|{position.get('price', '')}"


def _latest_1m_row() -> dict[str, str] | None:
    rows = _read_csv_rows(WEBHOOK_1M_CSV_PATH)
    return rows[-1] if rows else None


def _row_by_minute(path: Path, minute: datetime, time_column: str) -> dict[str, str] | None:
    target = _minute_key(minute)
    latest: dict[str, str] | None = None
    for row in _read_csv_rows(path):
        row_time = str(row.get(time_column) or "").strip()
        if row_time[:16] == target:
            latest = row
    return latest


def _points(side: str, entry_price: float, close: float) -> float:
    return close - entry_price if side == "bull" else entry_price - close


def _stop_hit(side: str, entry_price: float, latest_1m: dict[str, str]) -> bool:
    high = to_float(latest_1m.get("High"))
    low = to_float(latest_1m.get("Low"))
    if side == "bull":
        return low is not None and entry_price - low >= INITIAL_STOP_POINTS
    return high is not None and high - entry_price >= INITIAL_STOP_POINTS


def _entry_rule(position: dict, open_row: dict[str, str], mxf_row: dict[str, str]) -> tuple[bool, str]:
    previous_side = str(position.get("previous_side") or "")
    side = str(position.get("side") or "")
    close = to_float(open_row.get("Close"))
    ma960 = to_float(open_row.get("MA_960"))
    mtx_bvav = to_float(mxf_row.get("mtx_bvav"))
    if close is None or ma960 is None or mtx_bvav is None:
        return False, "missing close/MA_960/mtx_bvav"

    if previous_side == "bear" and side == "bull":
        if close < ma960 and mtx_bvav > 0:
            return True, "早盤 H 空轉多，開盤 close 在 MA_960 下且 mtx_bvav 為正"
        return False, "早盤 H 空轉多但 MA_960/mtx_bvav 條件不符"

    return False, "不是早盤 H 空轉多"


def _side_text(side: str) -> str:
    if side == "bull":
        return "多單"
    if side == "bear":
        return "空單"
    return side


def _send_signal_message(signal: dict) -> None:
    action_text = "進場" if signal.get("action") == "enter" else "出場"
    message = (
        "策略=H早盤空轉多(strategy_h_open_turn)；"
        f"{action_text}：{_side_text(str(signal.get('side') or ''))} {signal.get('quantity', QUANTITY)}口，"
        f"價格={signal.get('close', signal.get('entry_price', ''))}，"
        f"H方向={_side_text(str(signal.get('h_side') or ''))}，"
        f"開盤分鐘={signal.get('open_minute', '')}，"
        f"MA960={signal.get('ma960', '')}，mtx_bvav={signal.get('mtx_bvav', '')}，"
        f"浮動={signal.get('unrealized_points', '')}點，"
        f"最高浮盈={signal.get('max_unrealized_points', '')}點，"
        f"原因={signal.get('reason', '')}"
    )
    send_open_turn_message(message)


def evaluate_h_open_turn() -> dict | None:
    """Evaluate the opening-turn strategy and return an enter/exit signal."""
    with LOCK:
        latest_1m = _latest_1m_row()
        close = to_float(latest_1m.get("Close")) if latest_1m else None
        if latest_1m is None or close is None:
            return None

        position = _latest_h_state()
        h_key = _position_key(position)
        state = _read_state()
        active = state.get("active_open_turn")

        if active:
            side = str(active.get("side") or "")
            entry_price = to_float(active.get("entry_price"))
            if side not in {"bull", "bear"} or entry_price is None:
                state["active_open_turn"] = None
                _write_state(state)
                return None

            unrealized = _points(side, entry_price, close)
            max_unrealized = max(float(active.get("max_unrealized_points", 0) or 0), unrealized)
            active["max_unrealized_points"] = round(max_unrealized, 2)
            state["active_open_turn"] = active
            giveback = max(0.0, max_unrealized - unrealized)
            h_changed = str(active.get("h_position_key") or "") != h_key
            stopped = _stop_hit(side, entry_price, latest_1m)
            trailed = max_unrealized >= TRAIL_START_POINTS and giveback >= TRAIL_GIVEBACK_POINTS
            if not h_changed and not stopped and not trailed:
                _write_state(state)
                return None

            reason = "H 主單已出場/反向/換倉，開盤單跟隨出場"
            if stopped:
                reason = f"看錯停損，逆行達 {INITIAL_STOP_POINTS:g} 點"
            elif trailed:
                reason = f"浮盈達 {TRAIL_START_POINTS:g} 後回吐 {giveback:.1f} 點，保留獲利出場"
            signal = {
                "timestamp": now_str(),
                "action": "exit",
                "side": side,
                "quantity": int(active.get("quantity", QUANTITY) or QUANTITY),
                "entry_price": entry_price,
                "close": close,
                "h_position_timestamp": position.get("timestamp", "").strftime("%Y-%m-%d %H:%M:%S") if position else "",
                "h_side": position.get("side", "") if position else "",
                "h_entry_price": position.get("price", "") if position else "",
                "open_minute": active.get("open_minute", ""),
                "ma960": active.get("ma960", ""),
                "mtx_bvav": active.get("mtx_bvav", ""),
                "unrealized_points": round(unrealized, 1),
                "max_unrealized_points": round(max_unrealized, 1),
                "giveback_points": round(giveback, 1),
                "reason": reason,
            }
            state["active_open_turn"] = None
            state["last_exit"] = signal
            _write_state(state)
            _append_alert(signal)
            _send_signal_message(signal)
            return signal

        if not position:
            return None
        h_time = position.get("timestamp")
        if not isinstance(h_time, datetime) or h_time.strftime("%H:%M") not in OPENING_MINUTES:
            return None
        if state.get("last_entry_h_key") == h_key:
            return None

        open_minute = _opening_minute_for_h_time(h_time)
        open_row = _row_by_minute(WEBHOOK_1M_CSV_PATH, open_minute, "Record Time")
        mxf_row = _row_by_minute(MXF_VALUE_CSV_PATH, open_minute, "time")
        if open_row is None or mxf_row is None:
            return None

        ok, reason = _entry_rule(position, open_row, mxf_row)
        if not ok:
            state["last_checked_h_key"] = h_key
            state["last_reject_reason"] = reason
            _write_state(state)
            return None

        side = str(position["side"])
        h_entry = float(position["price"])
        open_close = to_float(open_row.get("Close"))
        ma960 = to_float(open_row.get("MA_960"))
        mtx_bvav = to_float(mxf_row.get("mtx_bvav"))
        signal = {
            "timestamp": now_str(),
            "action": "enter",
            "side": side,
            "quantity": QUANTITY,
            "entry_price": h_entry,
            "close": h_entry,
            "h_position_timestamp": h_time.strftime("%Y-%m-%d %H:%M:%S"),
            "h_side": side,
            "h_entry_price": h_entry,
            "open_minute": open_minute.strftime("%Y-%m-%d %H:%M:%S"),
            "ma960": "" if ma960 is None else round(ma960, 2),
            "mtx_bvav": "" if mtx_bvav is None else round(mtx_bvav, 2),
            "unrealized_points": 0,
            "max_unrealized_points": 0,
            "giveback_points": 0,
            "reason": reason,
        }
        state["active_open_turn"] = {
            "side": side,
            "quantity": QUANTITY,
            "entry_time": now_str(),
            "entry_price": h_entry,
            "h_position_key": h_key,
            "open_minute": signal["open_minute"],
            "ma960": signal["ma960"],
            "mtx_bvav": signal["mtx_bvav"],
            "max_unrealized_points": 0,
        }
        state["last_entry_h_key"] = h_key
        state["last_signal"] = signal
        _write_state(state)
        _append_alert(signal)
        _send_signal_message(signal)
        return signal


if __name__ == "__main__":
    result = evaluate_h_open_turn()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
