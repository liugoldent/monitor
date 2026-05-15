"""H scale-follow draft strategy.

This is a draft Discord-signal strategy for a second account. It follows the H1
trade only after the H1 position is already profitable and short timeframes
confirm the same direction. It can then emit a scale-in signal after the second
account position has profit.

Backtest on the currently available 1-minute data is weak, so this module is not
wired into `webhook_server.py` by default.
"""

from __future__ import annotations

import csv
import json
import os
from threading import RLock

from strategy_common import (
    append_csv_row,
    build_shortcycle_send_discord_message,
    ensure_csv_header,
    now_str,
    read_last_n_rows,
    to_float,
)

BASE_DIR = os.path.dirname(__file__)
TV_DOC_DIR = os.path.join(BASE_DIR, "tv_doc")

H_TRADE_CSV_PATH = os.path.join(TV_DOC_DIR, "h_trade.csv")
MXF_VALUE_CSV_PATH = os.path.join(TV_DOC_DIR, "mxf_value.csv")
H_SCALE_FOLLOW_LOG_PATH = os.path.join(TV_DOC_DIR, "h_scale_follow_signal.csv")
H_SCALE_FOLLOW_STATE_PATH = os.path.join(TV_DOC_DIR, "h_scale_follow_state.json")

WEBHOOK_CSV_BY_TF = {
    "1": os.path.join(TV_DOC_DIR, "webhook_data_1min.csv"),
    "3": os.path.join(TV_DOC_DIR, "webhook_data_3min.csv"),
    "5": os.path.join(TV_DOC_DIR, "webhook_data_5min.csv"),
}

SCALE_FOLLOW_LOCK = RLock()
send_scale_follow_message = build_shortcycle_send_discord_message(MXF_VALUE_CSV_PATH)

SCALE_FOLLOW_HEADER = [
    "timestamp",
    "signal",
    "side",
    "entry_price",
    "close",
    "unrealized_points",
    "reason",
    "confirm_score",
]

H_PROFIT_ENTRY_POINTS = 180.0
ENTRY_CONFIRM_SCORE = 2
FIRST_STOP_LOSS_POINTS = -180.0
ADD_PROFIT_POINTS = 180.0
ADD_CONFIRM_SCORE = 2
AFTER_ADD_PROTECT_POINTS = 80.0
TAKE_PROFIT_POINTS = 450.0
TRAIL_ARM_POINTS = 300.0
TRAIL_GIVEBACK_POINTS = 160.0


def _load_state() -> dict:
    if not os.path.isfile(H_SCALE_FOLLOW_STATE_PATH):
        return {}
    try:
        with open(H_SCALE_FOLLOW_STATE_PATH, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(H_SCALE_FOLLOW_STATE_PATH), exist_ok=True)
    with open(H_SCALE_FOLLOW_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _get_latest_h_position() -> dict | None:
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
        price = to_float(row.get("price"))
        if action == "enter" and side in {"bull", "bear"} and price is not None:
            return {"timestamp": str(row.get("timestamp", "")).strip(), "side": side, "price": price}
        if action == "exiting":
            return None
    return None


def _position_key(position: dict) -> str:
    return f"{position['timestamp']}|{position['side']}|{position['price']}"


def _latest_row(path: str) -> dict | None:
    rows = read_last_n_rows(path, 1)
    return rows[-1] if rows else None


def _latest_close() -> float | None:
    row = _latest_row(WEBHOOK_CSV_BY_TF["1"])
    return to_float(row.get("Close")) if row else None


def _unrealized(side: str, entry_price: float, close: float) -> float:
    if side == "bull":
        return close - entry_price
    return entry_price - close


def _side_supported(side: str, row: dict | None) -> bool:
    if not row:
        return False
    close = to_float(row.get("Close"))
    ma_p200 = to_float(row.get("MA_P200"))
    ma_n200 = to_float(row.get("MA_N200"))
    bbr = to_float(row.get("BBR"))
    if close is None or bbr is None:
        return False
    if side == "bull":
        return ma_n200 is not None and close > ma_n200 and bbr > 0.50
    return ma_p200 is not None and close < ma_p200 and bbr < 0.50


def _side_invalid(side: str, row: dict | None) -> bool:
    if not row:
        return False
    close = to_float(row.get("Close"))
    ma_p200 = to_float(row.get("MA_P200"))
    ma_n200 = to_float(row.get("MA_N200"))
    bbr = to_float(row.get("BBR"))
    if close is None or bbr is None:
        return False
    if side == "bull":
        return ma_n200 is not None and close < ma_n200 and bbr < 0.45
    return ma_p200 is not None and close > ma_p200 and bbr > 0.55


def _confirm_score(side: str) -> int:
    return sum(1 for tf, path in WEBHOOK_CSV_BY_TF.items() if _side_supported(side, _latest_row(path)))


def _invalid_score(side: str) -> int:
    return sum(1 for tf, path in WEBHOOK_CSV_BY_TF.items() if _side_invalid(side, _latest_row(path)))


def _append_signal(signal: str, side: str, entry_price: float, close: float, unrealized: float, reason: str, confirm_score: int) -> None:
    ensure_csv_header(H_SCALE_FOLLOW_LOG_PATH, SCALE_FOLLOW_HEADER)
    append_csv_row(
        H_SCALE_FOLLOW_LOG_PATH,
        [now_str(), signal, side, entry_price, close, round(unrealized, 1), reason, confirm_score],
    )


def _get_position(state: dict) -> dict | None:
    side = str(state.get("position_side", "")).strip().lower()
    if side not in {"bull", "bear"}:
        return None
    entry_price = to_float(state.get("position_entry_price"))
    if entry_price is None:
        return None
    return {
        "side": side,
        "entry_price": entry_price,
        "add_entry_price": to_float(state.get("add_entry_price")),
        "h_position_key": str(state.get("reference_h_key", "")).strip(),
        "max_favorable_points": float(state.get("max_favorable_points", 0) or 0),
        "quantity": int(float(state.get("position_quantity", 1) or 1)),
    }


def _clear_position(state: dict) -> None:
    state["position_side"] = ""
    state["position_entry_price"] = ""
    state["add_entry_price"] = ""
    state["position_quantity"] = ""
    state["reference_h_key"] = ""
    state["max_favorable_points"] = ""


def apply_h_scale_follow_strategy() -> bool:
    """Emit draft H scale-follow Discord signals."""
    with SCALE_FOLLOW_LOCK:
        h_position = _get_latest_h_position()
        close = _latest_close()
        if close is None:
            return False

        state = _load_state()
        position = _get_position(state)
        if position is not None:
            side = position["side"]
            first_unrealized = _unrealized(side, position["entry_price"], close)
            max_favorable = max(position["max_favorable_points"], first_unrealized)
            state["max_favorable_points"] = max_favorable

            if position["quantity"] < 2 and first_unrealized >= ADD_PROFIT_POINTS and _confirm_score(side) >= ADD_CONFIRM_SCORE:
                _append_signal("add", side, close, close, first_unrealized, "scale follow add", _confirm_score(side))
                state["add_entry_price"] = close
                state["position_quantity"] = 2
                _save_state(state)
                send_scale_follow_message(f"H scale follow 加碼訊號：{side}，加碼價={close}，第一口浮盈={first_unrealized:.1f}")
                return True

            reason = ""
            h_key = _position_key(h_position) if h_position else ""
            if h_key and h_key != position["h_position_key"]:
                reason = "H position changed"
            elif position["quantity"] >= 2 and first_unrealized <= AFTER_ADD_PROTECT_POINTS:
                reason = "after-add protect"
            elif position["quantity"] < 2 and (first_unrealized <= FIRST_STOP_LOSS_POINTS or _invalid_score(side) >= 2):
                reason = "stop or invalid"
            elif first_unrealized >= TAKE_PROFIT_POINTS:
                reason = "take profit"
            elif max_favorable >= TRAIL_ARM_POINTS and first_unrealized <= max_favorable - TRAIL_GIVEBACK_POINTS:
                reason = "trailing giveback"

            if reason:
                entries = [position["entry_price"]]
                if position["add_entry_price"] is not None:
                    entries.append(position["add_entry_price"])
                total_points = sum(_unrealized(side, entry, close) for entry in entries)
                _append_signal("exit", side, position["entry_price"], close, total_points, reason, _confirm_score(side))
                _clear_position(state)
                _save_state(state)
                send_scale_follow_message(f"H scale follow 出場訊號：{side}，現價={close}，總浮動={total_points:.1f}，原因={reason}")
                return True

            _save_state(state)
            return False

        if not h_position:
            return False

        side = h_position["side"]
        h_unrealized = _unrealized(side, h_position["price"], close)
        score = _confirm_score(side)
        h_key = _position_key(h_position)
        if (
            h_unrealized >= H_PROFIT_ENTRY_POINTS
            and score >= ENTRY_CONFIRM_SCORE
            and state.get("last_entry_h_key") != h_key
        ):
            _append_signal("entry", side, close, close, 0.0, "H profitable follow entry", score)
            state["position_side"] = side
            state["position_entry_price"] = close
            state["add_entry_price"] = ""
            state["position_quantity"] = 1
            state["reference_h_key"] = h_key
            state["max_favorable_points"] = 0.0
            state["last_entry_h_key"] = h_key
            _save_state(state)
            send_scale_follow_message(f"H scale follow 進場訊號：{side}，進場價={close}，H浮盈={h_unrealized:.1f}，確認={score}/3")
            return True

        _save_state(state)
        return False
