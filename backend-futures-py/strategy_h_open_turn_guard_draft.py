"""Open-turn risk guard for first-account H entries.

This draft only emits CSV/state/Discord alerts. It does not place orders.
Keep it separate from the reverse guard until the live behavior is reviewed.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, time
from pathlib import Path
from threading import RLock

from strategy_common import build_shortcycle_send_discord_message, now_str, to_float


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
H_TRADE_CSV_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_CSV_PATH = TV_DOC_DIR / "mxf_value.csv"
WEBHOOK_1M_CSV_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
STATE_PATH = TV_DOC_DIR / "h_open_turn_guard_state.json"
ALERT_PATH = TV_DOC_DIR / "h_open_turn_guard_alert.csv"
send_open_turn_guard_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))

SESSION_1500_START = time(15, 0)
SESSION_1500_END = time(15, 10)
SESSION_0845_START = time(8, 45)
SESSION_0845_END = time(8, 55)
BBR_MID_LOW = 0.35
BBR_MID_HIGH = 0.55
MAX_ENTRY_AGE_MINUTES = 20

ALERT_HEADER = [
    "timestamp",
    "action",
    "side",
    "entry_price",
    "close",
    "h_entry_time",
    "h_entry_price",
    "h_unrealized_points",
    "bbr",
    "tx_bvav",
    "mtx_bvav",
    "mtx_bvav_avg",
    "mxf_signal",
    "mxf_trend",
    "reason",
]

LOCK = RLock()


def _read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_alert(row: list[object]) -> None:
    exists = ALERT_PATH.exists()
    with ALERT_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(ALERT_HEADER)
        writer.writerow(row)


def _latest_csv_row(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    latest: dict[str, str] | None = None
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            latest = row
    return latest


def _latest_h_position() -> dict | None:
    if not H_TRADE_CSV_PATH.exists():
        return None
    current: dict | None = None
    with H_TRADE_CSV_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            action = row.get("action")
            if action == "enter":
                price = to_float(row.get("price"))
                if price is None:
                    continue
                current = {
                    "timestamp": row.get("timestamp", ""),
                    "side": row.get("side", ""),
                    "price": price,
                }
            elif action == "exiting":
                current = None
    return current


def _parse_h_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _latest_market_time(latest_1m: dict) -> datetime | None:
    return _parse_h_time(str(latest_1m.get("TradingView Time") or latest_1m.get("Record Time") or ""))


def _is_recent_entry(entry_time: datetime | None, market_time: datetime | None) -> bool:
    if entry_time is None or market_time is None:
        return False
    if entry_time.date() != market_time.date() or market_time < entry_time:
        return False
    return (market_time - entry_time).total_seconds() <= MAX_ENTRY_AGE_MINUTES * 60


def _in_time_window(moment: datetime | None, start: time, end: time) -> bool:
    if moment is None:
        return False
    current = moment.time()
    return start <= current <= end


def _unrealized_points(side: str, entry_price: float, close: float) -> float:
    if side == "bull":
        return close - entry_price
    return entry_price - close


def _side_text(side: str) -> str:
    if side == "bull":
        return "多單"
    if side == "bear":
        return "空單"
    return side


def _mxf_signal_confirms_h(side: str, mxf: dict) -> bool:
    signal = str(mxf.get("signal") or "").strip().lower()
    trend = str(mxf.get("trend") or "").strip().lower()
    if side == "bull":
        return signal == "bull" and trend == "gold"
    if side == "bear":
        return signal == "bear" and trend == "death"
    return False


def _risk_reason(position: dict, latest_1m: dict, mxf: dict) -> str | None:
    entry_time = _parse_h_time(str(position.get("timestamp") or ""))
    market_time = _latest_market_time(latest_1m)
    if not _is_recent_entry(entry_time, market_time):
        return None

    side = str(position.get("side") or "")

    if _in_time_window(entry_time, SESSION_1500_START, SESSION_1500_END):
        if not _mxf_signal_confirms_h(side, mxf):
            signal = str(mxf.get("signal") or "").strip() or "-"
            trend = str(mxf.get("trend") or "").strip() or "-"
            return f"15:00轉向單，但MXF signal/trend沒有確認H方向 ({signal}/{trend})"

    if _in_time_window(entry_time, SESSION_0845_START, SESSION_0845_END):
        bbr = to_float(latest_1m.get("BBR"))
        if bbr is not None and BBR_MID_LOW <= bbr <= BBR_MID_HIGH:
            return f"08:45開盤轉向單，BBR在中間區間 {BBR_MID_LOW:g}-{BBR_MID_HIGH:g}"

    return None


def _send_guard_message(signal: dict, *, mxf: dict, bbr: float | None, h_unrealized: float) -> None:
    side = str(signal.get("side") or "")
    message = (
        f"策略=H開盤轉向護欄(strategy_h_open_turn_guard_draft)；"
        f"H 開盤轉向護欄提醒：建議第二帳號先避開/暫不跟進 {_side_text(side)}，"
        f"H進場時間={signal.get('h_entry_time', '')}，H進場={signal.get('h_entry_price', '')}，"
        f"現價={signal.get('close', '')}，H浮動={h_unrealized:.1f}點，"
        f"BBR={bbr if bbr is not None else '-'}，"
        f"signal/trend={mxf.get('signal', '-')}/{mxf.get('trend', '-')}，"
        f"原因={signal.get('reason', '')}"
    )
    send_open_turn_guard_message(message)


def evaluate_h_open_turn_guard() -> dict | None:
    """Alert once when a fresh H entry matches an open-turn risk pattern."""
    with LOCK:
        position = _latest_h_position()
        latest_1m = _latest_csv_row(WEBHOOK_1M_CSV_PATH)
        mxf = _latest_csv_row(MXF_VALUE_CSV_PATH) or {}
        close = to_float(latest_1m.get("Close")) if latest_1m else None
        if not position or not latest_1m or close is None:
            return None

        h_key = f"{position.get('timestamp', '')}|{position.get('side', '')}|{position.get('price', '')}"
        state = _read_state()
        if state.get("last_alert_h_key") == h_key:
            return None

        reason = _risk_reason(position, latest_1m, mxf)
        if not reason:
            return None

        h_side = str(position.get("side") or "")
        h_entry = float(position["price"])
        h_unrealized = _unrealized_points(h_side, h_entry, close)
        bbr = to_float(latest_1m.get("BBR"))
        signal = {
            "action": "avoid_entry",
            "side": h_side,
            "entry_price": h_entry,
            "close": close,
            "h_entry_time": position.get("timestamp", ""),
            "h_entry_price": h_entry,
            "reason": reason,
        }

        state["last_alert_h_key"] = h_key
        state["last_alert"] = {
            "timestamp": now_str(),
            "h_key": h_key,
            "side": h_side,
            "reason": reason,
        }
        _write_state(state)
        _append_alert([
            now_str(),
            "avoid_entry",
            h_side,
            h_entry,
            close,
            position.get("timestamp", ""),
            h_entry,
            round(h_unrealized, 1),
            bbr if bbr is not None else "",
            mxf.get("tx_bvav", ""),
            mxf.get("mtx_bvav", ""),
            mxf.get("mtx_bvav_avg", ""),
            mxf.get("signal", ""),
            mxf.get("trend", ""),
            reason,
        ])
        _send_guard_message(signal, mxf=mxf, bbr=bbr, h_unrealized=h_unrealized)
        return signal


if __name__ == "__main__":
    signal = evaluate_h_open_turn_guard()
    if signal:
        print(json.dumps(signal, ensure_ascii=False, indent=2))
