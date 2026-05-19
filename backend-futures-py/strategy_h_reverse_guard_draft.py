"""First-account reverse guard strategy for second-account signals.

This is the active lightweight guard used by `webhook_server.py`. It only emits
CSV/state signals; order placement remains outside this module.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from threading import RLock

from strategy_common import now_str, to_float


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
H_TRADE_CSV_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_CSV_PATH = TV_DOC_DIR / "mxf_value.csv"
WEBHOOK_CSV_BY_TF = {
    "1": TV_DOC_DIR / "webhook_data_1min.csv",
    "5": TV_DOC_DIR / "webhook_data_5min.csv",
    "10": TV_DOC_DIR / "webhook_data_10min.csv",
    "15": TV_DOC_DIR / "webhook_data_15min.csv",
}
STATE_PATH = TV_DOC_DIR / "h_reverse_guard_state.json"
ALERT_PATH = TV_DOC_DIR / "h_reverse_guard_alert.csv"

MIN_H_LOSS_POINTS = 0.0
MTX_AVG_THRESHOLD = 1200.0
STOP_AVG_THRESHOLD = 0.0
REQUIRE_MXF_SIGNAL = True
MIN_TF_INVALID_SCORE = 0

ALERT_HEADER = [
    "timestamp",
    "action",
    "side",
    "entry_price",
    "close",
    "h_side",
    "h_entry_price",
    "h_unrealized_points",
    "mtx_bvav_avg",
    "mxf_signal",
    "mxf_trend",
    "tf_score",
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


def _unrealized_points(side: str, entry_price: float, close: float) -> float:
    if side == "bull":
        return close - entry_price
    return entry_price - close


def _reverse_side(side: str) -> str:
    return "bear" if side == "bull" else "bull"


def _is_tf_invalid(h_side: str, row: dict | None) -> bool:
    if not row:
        return False
    close = to_float(row.get("Close"))
    ma_p200 = to_float(row.get("MA_P200"))
    ma_n200 = to_float(row.get("MA_N200"))
    bbr = to_float(row.get("BBR"))
    if close is None or bbr is None:
        return False
    if h_side == "bull":
        return ma_n200 is not None and close < ma_n200 and bbr < 0.45
    return ma_p200 is not None and close > ma_p200 and bbr > 0.55


def _mxf_opposes_h(h_side: str, mxf: dict) -> bool:
    avg = to_float(mxf.get("mtx_bvav_avg"))
    signal = str(mxf.get("signal") or "").strip().lower()
    trend = str(mxf.get("trend") or "").strip().lower()
    if avg is None:
        return False
    if h_side == "bull":
        avg_ok = avg <= -MTX_AVG_THRESHOLD
        signal_ok = signal == "bear" and trend == "death"
    else:
        avg_ok = avg >= MTX_AVG_THRESHOLD
        signal_ok = signal == "bull" and trend == "gold"
    return avg_ok and (signal_ok if REQUIRE_MXF_SIGNAL else True)


def _stop_hit(guard_side: str, mxf: dict) -> bool:
    avg = to_float(mxf.get("mtx_bvav_avg"))
    if avg is None:
        return False
    if guard_side == "bear":
        return avg >= STOP_AVG_THRESHOLD
    return avg <= -STOP_AVG_THRESHOLD


def evaluate_h_reverse_guard() -> dict | None:
    """Return a draft signal dict and append alert CSV rows.

    The caller is responsible for sending messages or placing orders. This draft
    only writes state and alert rows.
    """
    with LOCK:
        position = _latest_h_position()
        latest_1m = _latest_csv_row(WEBHOOK_CSV_BY_TF["1"])
        mxf = _latest_csv_row(MXF_VALUE_CSV_PATH) or {}
        close = to_float(latest_1m.get("Close")) if latest_1m else None
        if not position or close is None:
            return None

        h_side = position["side"]
        h_entry = float(position["price"])
        h_unrealized = _unrealized_points(h_side, h_entry, close)
        state = _read_state()
        active = state.get("active_guard")
        avg = to_float(mxf.get("mtx_bvav_avg"))
        tf_rows = {tf: _latest_csv_row(path) for tf, path in WEBHOOK_CSV_BY_TF.items()}
        tf_score = sum(1 for row in tf_rows.values() if _is_tf_invalid(h_side, row))

        if active:
            guard_side = str(active.get("side") or "")
            h_key_changed = active.get("h_position_timestamp") != position.get("timestamp")
            if h_key_changed or _stop_hit(guard_side, mxf):
                reason = "H position ended/changed" if h_key_changed else "mtx_bvav_avg reverted"
                signal = {
                    "action": "exit",
                    "side": guard_side,
                    "close": close,
                    "reason": reason,
                }
                state["active_guard"] = None
                _write_state(state)
                _append_alert([
                    now_str(),
                    "exit",
                    guard_side,
                    active.get("entry_price", ""),
                    close,
                    h_side,
                    h_entry,
                    round(h_unrealized, 1),
                    avg,
                    mxf.get("signal", ""),
                    mxf.get("trend", ""),
                    tf_score,
                    reason,
                ])
                return signal
            return None

        if h_unrealized > MIN_H_LOSS_POINTS:
            return None
        if tf_score < MIN_TF_INVALID_SCORE:
            return None
        if not _mxf_opposes_h(h_side, mxf):
            return None

        guard_side = _reverse_side(h_side)
        reason = "H losing and MXF avg/signal confirms reverse pressure"
        state["active_guard"] = {
            "side": guard_side,
            "entry_price": close,
            "entry_time": now_str(),
            "h_position_timestamp": position.get("timestamp"),
        }
        _write_state(state)
        _append_alert([
            now_str(),
            "enter",
            guard_side,
            close,
            close,
            h_side,
            h_entry,
            round(h_unrealized, 1),
            avg,
            mxf.get("signal", ""),
            mxf.get("trend", ""),
            tf_score,
            reason,
        ])
        return {
            "action": "enter",
            "side": guard_side,
            "entry_price": close,
            "reason": reason,
        }


if __name__ == "__main__":
    signal = evaluate_h_reverse_guard()
    if signal:
        print(json.dumps(signal, ensure_ascii=False, indent=2))
