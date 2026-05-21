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

from strategy_common import build_shortcycle_send_discord_message, now_str, to_float


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
send_reverse_guard_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))

MIN_H_LOSS_POINTS = 100.0
FLOW_CONFIRM_THRESHOLD = 150.0
TAKE_PROFIT_POINTS = 160.0
MTX_BVAV_STOP_THRESHOLD = 0.0
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


def _side_text(side: str) -> str:
    if side == "bull":
        return "多單"
    if side == "bear":
        return "空單"
    return side


def _send_guard_message(
    signal: dict,
    *,
    h_side: str,
    h_entry: float,
    h_unrealized: float,
    mtx_bvav: float | None,
    avg: float | None,
    tf_score: int,
) -> None:
    action = str(signal.get("action") or "")
    guard_side = str(signal.get("side") or "")
    close = signal.get("entry_price", signal.get("close", ""))
    action_text = "進場" if action == "enter" else "出場"
    message = (
        f"H 反向護欄{action_text}：{_side_text(guard_side)}，"
        f"價格={close}，H方向={_side_text(h_side)}，H進場={h_entry}，"
        f"H浮動={h_unrealized:.1f}點，mtx_bvav={mtx_bvav if mtx_bvav is not None else '-'}，"
        f"mtx_bvav_avg={avg if avg is not None else '-'}，"
        f"tf_score={tf_score}，原因={signal.get('reason', '')}"
    )
    send_reverse_guard_message(message)


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


def _pressure_against_h(h_side: str, value: float | None) -> float | None:
    if value is None:
        return None
    return -value if h_side == "bull" else value


def _mxf_opposes_h(h_side: str, mxf: dict) -> bool:
    tx_bvav = to_float(mxf.get("tx_bvav"))
    mtx_bvav = to_float(mxf.get("mtx_bvav"))
    avg = to_float(mxf.get("mtx_bvav_avg"))
    signal = str(mxf.get("signal") or "").strip().lower()
    trend = str(mxf.get("trend") or "").strip().lower()
    pressures = [
        _pressure_against_h(h_side, tx_bvav),
        _pressure_against_h(h_side, mtx_bvav),
        _pressure_against_h(h_side, avg),
    ]
    if any(pressure is None for pressure in pressures):
        return False
    if h_side == "bull":
        signal_ok = signal == "bear" and trend == "death"
    else:
        signal_ok = signal == "bull" and trend == "gold"
    flow_ok = all(float(pressure) >= FLOW_CONFIRM_THRESHOLD for pressure in pressures)
    return flow_ok and (signal_ok if REQUIRE_MXF_SIGNAL else True)


def _guard_points(guard_side: str, entry_price: float, close_price: float) -> float:
    if guard_side == "bull":
        return close_price - entry_price
    return entry_price - close_price


def _stop_hit(h_side: str, guard_side: str, entry_price: float | None, close_price: float, mxf: dict) -> tuple[bool, str]:
    if entry_price is not None and _guard_points(guard_side, entry_price, close_price) >= TAKE_PROFIT_POINTS:
        return True, f"guard reached {TAKE_PROFIT_POINTS:g} points"

    mtx_bvav = to_float(mxf.get("mtx_bvav"))
    avg = to_float(mxf.get("mtx_bvav_avg"))
    mtx_pressure = _pressure_against_h(h_side, mtx_bvav)
    avg_pressure = _pressure_against_h(h_side, avg)
    if mtx_pressure is not None and mtx_pressure <= MTX_BVAV_STOP_THRESHOLD:
        return True, "mtx_bvav pressure reverted"
    if avg_pressure is not None and avg_pressure <= STOP_AVG_THRESHOLD:
        return True, "mtx_bvav_avg pressure reverted"
    return False, ""


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
        mtx_bvav = to_float(mxf.get("mtx_bvav"))
        avg = to_float(mxf.get("mtx_bvav_avg"))
        tf_rows = {tf: _latest_csv_row(path) for tf, path in WEBHOOK_CSV_BY_TF.items()}
        tf_score = sum(1 for row in tf_rows.values() if _is_tf_invalid(h_side, row))

        if active:
            guard_side = str(active.get("side") or "")
            h_key_changed = active.get("h_position_timestamp") != position.get("timestamp")
            guard_entry = to_float(active.get("entry_price"))
            stop_hit, stop_reason = _stop_hit(h_side, guard_side, guard_entry, close, mxf)
            if h_key_changed or stop_hit:
                reason = "H position ended/changed" if h_key_changed else stop_reason
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
                _send_guard_message(
                    signal,
                    h_side=h_side,
                    h_entry=h_entry,
                    h_unrealized=h_unrealized,
                    mtx_bvav=mtx_bvav,
                    avg=avg,
                    tf_score=tf_score,
                )
                return signal
            return None

        if h_unrealized > -MIN_H_LOSS_POINTS:
            return None
        if tf_score < MIN_TF_INVALID_SCORE:
            return None
        if not _mxf_opposes_h(h_side, mxf):
            return None

        guard_side = _reverse_side(h_side)
        reason = "H losing and mtx_bvav/signal confirms reverse pressure"
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
        signal = {
            "action": "enter",
            "side": guard_side,
            "entry_price": close,
            "reason": reason,
        }
        _send_guard_message(
            signal,
            h_side=h_side,
            h_entry=h_entry,
            h_unrealized=h_unrealized,
            mtx_bvav=mtx_bvav,
            avg=avg,
            tf_score=tf_score,
        )
        return signal


if __name__ == "__main__":
    signal = evaluate_h_reverse_guard()
    if signal:
        print(json.dumps(signal, ensure_ascii=False, indent=2))
