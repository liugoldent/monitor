"""H1 reverse guard signal strategy.

This module outputs Discord entry/exit signals for a second account. It monitors
the latest H1 position from `h_trade.csv`. When the H1 position is losing and
short timeframes confirm the opposite side, the second account enters the
opposite direction. The second-account position then has its own stop-loss,
take-profit, and giveback exits.

All PnL values are single-contract points. Quantity is not used to scale MDD or
guard thresholds.
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
LOSS_GUARD_LOG_PATH = os.path.join(TV_DOC_DIR, "h_loss_guard_alert.csv")
LOSS_GUARD_STATE_PATH = os.path.join(TV_DOC_DIR, "h_loss_guard_state.json")

WEBHOOK_CSV_BY_TF = {
    "1": os.path.join(TV_DOC_DIR, "webhook_data_1min.csv"),
    "3": os.path.join(TV_DOC_DIR, "webhook_data_3min.csv"),
    "5": os.path.join(TV_DOC_DIR, "webhook_data_5min.csv"),
    "10": os.path.join(TV_DOC_DIR, "webhook_data_10min.csv"),
    "15": os.path.join(TV_DOC_DIR, "webhook_data_15min.csv"),
}

LOSS_GUARD_LOCK = RLock()
send_loss_guard_message = build_shortcycle_send_discord_message(MXF_VALUE_CSV_PATH)

LOSS_GUARD_HEADER = [
    "timestamp",
    "signal",
    "side",
    "entry_price",
    "close",
    "unrealized_points",
    "reason",
    "tf_score",
    "mxf_signal",
    "mxf_trend",
]

SOFT_LOSS_POINTS = -180.0
HARD_LOSS_POINTS = -480.0
INVALIDATION_SCORE = 3
SECOND_STOP_LOSS_POINTS = -220.0
SECOND_TAKE_PROFIT_POINTS = 450.0
SECOND_TRAIL_ARM_POINTS = 220.0
SECOND_TRAIL_FLOOR_POINTS = 120.0
SECOND_ADD_PROFIT_POINTS = 180.0
SECOND_ADD_CONFIRM_SCORE = 2
SECOND_AFTER_ADD_PROTECT_POINTS = 80.0


def _load_state() -> dict:
    if not os.path.isfile(LOSS_GUARD_STATE_PATH):
        return {}
    try:
        with open(LOSS_GUARD_STATE_PATH, "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(LOSS_GUARD_STATE_PATH), exist_ok=True)
    with open(LOSS_GUARD_STATE_PATH, "w", encoding="utf-8") as handle:
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
            return {
                "timestamp": str(row.get("timestamp", "")).strip(),
                "side": side,
                "price": price,
                "quantity": str(row.get("quantity", "")).strip(),
            }
        if action == "exiting":
            return None
    return None


def _get_latest_row(path: str) -> dict | None:
    rows = read_last_n_rows(path, 1)
    return rows[-1] if rows else None


def _get_latest_mxf() -> dict:
    row = _get_latest_row(MXF_VALUE_CSV_PATH) or {}
    return {
        "signal": str(row.get("signal", "")).strip().lower(),
        "trend": str(row.get("trend", "")).strip().lower(),
    }


def _row_close(row: dict | None) -> float | None:
    return to_float(row.get("Close")) if row else None


def _unrealized_points(side: str, entry_price: float, close_price: float) -> float:
    if side == "bull":
        return close_price - entry_price
    return entry_price - close_price


def _is_tf_invalid(side: str, row: dict | None) -> bool:
    if not row:
        return False
    close = to_float(row.get("Close"))
    ma_p200 = to_float(row.get("MA_P200"))
    ma_n200 = to_float(row.get("MA_N200"))
    bbr = to_float(row.get("BBR"))
    if close is None or bbr is None:
        return False

    if side == "bull":
        below_support = ma_n200 is not None and close < ma_n200
        weak_bbr = bbr < 0.45
        return below_support and weak_bbr

    above_resistance = ma_p200 is not None and close > ma_p200
    strong_bbr = bbr > 0.55
    return above_resistance and strong_bbr


def _is_mxf_invalid(side: str, mxf: dict) -> bool:
    signal = mxf.get("signal", "")
    trend = mxf.get("trend", "")
    if side == "bull":
        return signal == "bear" and trend == "death"
    return signal == "bull" and trend == "gold"


def _build_guard_signal(position: dict) -> dict | None:
    side = position["side"]
    entry_price = position["price"]
    latest_1m = _get_latest_row(WEBHOOK_CSV_BY_TF["1"])
    close = _row_close(latest_1m)
    if close is None:
        return None

    unrealized = _unrealized_points(side, entry_price, close)
    tf_rows = {tf: _get_latest_row(path) for tf, path in WEBHOOK_CSV_BY_TF.items()}
    invalid_tfs = [tf for tf, row in tf_rows.items() if _is_tf_invalid(side, row)]
    mxf = _get_latest_mxf()
    mxf_invalid = _is_mxf_invalid(side, mxf)

    reasons: list[str] = []
    if unrealized <= HARD_LOSS_POINTS:
        reasons.append(f"hard loss cap {HARD_LOSS_POINTS:.0f}pts")

    if unrealized <= SOFT_LOSS_POINTS and len(invalid_tfs) >= INVALIDATION_SCORE:
        reasons.append(f"{len(invalid_tfs)} timeframe invalidation: {','.join(invalid_tfs)}")

    if unrealized <= SOFT_LOSS_POINTS and mxf_invalid and len(invalid_tfs) >= 2:
        reasons.append("MXF confirms opposite pressure")

    if not reasons:
        return None

    return {
        "side": side,
        "entry_price": entry_price,
        "close": close,
        "unrealized_points": unrealized,
        "reason": "; ".join(reasons),
        "tf_score": len(invalid_tfs),
        "mxf_signal": mxf.get("signal", ""),
        "mxf_trend": mxf.get("trend", ""),
        "position_timestamp": position["timestamp"],
    }


def _append_guard_signal(signal_name: str, signal: dict) -> None:
    ensure_csv_header(LOSS_GUARD_LOG_PATH, LOSS_GUARD_HEADER)
    append_csv_row(
        LOSS_GUARD_LOG_PATH,
        [
            now_str(),
            signal_name,
            signal["side"],
            signal["entry_price"],
            signal["close"],
            round(signal["unrealized_points"], 1),
            signal["reason"],
            signal["tf_score"],
            signal["mxf_signal"],
            signal["mxf_trend"],
        ],
    )


def _alert_key(signal: dict) -> str:
    loss_bucket = int(abs(signal["unrealized_points"]) // 100)
    return f"{signal['position_timestamp']}|{signal['side']}|{loss_bucket}|{signal['tf_score']}|{signal['reason']}"


def _position_key(position: dict) -> str:
    return f"{position['timestamp']}|{position['side']}|{position['price']}"


def _opposite_side(side: str) -> str:
    return "bear" if side == "bull" else "bull"


def _build_second_entry_signal(h_position: dict, guard_signal: dict) -> dict:
    side = _opposite_side(h_position["side"])
    mxf = _get_latest_mxf()
    return {
        "side": side,
        "entry_price": guard_signal["close"],
        "close": guard_signal["close"],
        "unrealized_points": 0.0,
        "reason": f"reverse H1 loss: {guard_signal['reason']}",
        "tf_score": guard_signal["tf_score"],
        "mxf_signal": mxf.get("signal", ""),
        "mxf_trend": mxf.get("trend", ""),
        "position_timestamp": h_position["timestamp"],
    }


def _get_second_position(state: dict) -> dict | None:
    side = str(state.get("second_position_side", "")).strip().lower()
    if side not in {"bull", "bear"}:
        return None
    try:
        entry_price = float(state.get("second_position_entry_price"))
    except (TypeError, ValueError):
        return None
    return {
        "side": side,
        "entry_price": entry_price,
        "add_entry_price": to_float(state.get("second_add_entry_price")),
        "h_position_key": str(state.get("second_reference_h_key", "")).strip(),
        "max_favorable_points": float(state.get("second_max_favorable_points", 0) or 0),
        "quantity": int(float(state.get("second_position_quantity", 1) or 1)),
    }


def _clear_second_position(state: dict) -> None:
    state["second_position_side"] = ""
    state["second_position_entry_price"] = ""
    state["second_add_entry_price"] = ""
    state["second_position_quantity"] = ""
    state["second_reference_h_key"] = ""
    state["second_max_favorable_points"] = ""


def _build_exit_signal(side: str, entry_price: float, close: float, reason: str, h_position_key: str = "") -> dict:
    mxf = _get_latest_mxf()
    return {
        "side": side,
        "entry_price": entry_price,
        "close": close,
        "unrealized_points": _unrealized_points(side, entry_price, close),
        "reason": reason,
        "tf_score": 0,
        "mxf_signal": mxf.get("signal", ""),
        "mxf_trend": mxf.get("trend", ""),
        "position_timestamp": h_position_key,
    }


def _build_add_signal(side: str, add_price: float, first_entry_price: float, close: float, reason: str, h_position_key: str = "") -> dict:
    mxf = _get_latest_mxf()
    return {
        "side": side,
        "entry_price": add_price,
        "close": close,
        "unrealized_points": _unrealized_points(side, first_entry_price, close),
        "reason": reason,
        "tf_score": _second_side_confirm_score(side),
        "mxf_signal": mxf.get("signal", ""),
        "mxf_trend": mxf.get("trend", ""),
        "position_timestamp": h_position_key,
    }


def _second_side_confirm_score(side: str) -> int:
    return sum(
        1
        for tf in ("1", "3", "5")
        if _is_second_side_supported(side, _get_latest_row(WEBHOOK_CSV_BY_TF[tf]))
    )


def _is_second_side_supported(side: str, row: dict | None) -> bool:
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


def _send_second_entry_signal(h_position: dict, guard_signal: dict, state: dict) -> None:
    h_key = _position_key(h_position)
    if _get_second_position(state) is not None:
        return
    if state.get("last_second_entry_h_key") == h_key:
        return

    signal = _build_second_entry_signal(h_position, guard_signal)
    _append_guard_signal("entry", signal)
    state["last_second_entry_h_key"] = h_key
    state["second_position_side"] = signal["side"]
    state["second_position_entry_price"] = signal["entry_price"]
    state["second_add_entry_price"] = ""
    state["second_position_quantity"] = 1
    state["second_reference_h_key"] = h_key
    state["second_max_favorable_points"] = 0.0
    state["last_entry_signal_at"] = now_str()
    _save_state(state)

    side_label = "多單" if signal["side"] == "bull" else "空單"
    h_side_label = "多單" if h_position["side"] == "bull" else "空單"
    message = (
        f"第二帳號反向進場訊號：{side_label}\n"
        f"第一帳號 H1 {h_side_label} 看錯擴大，第二帳號反向進場\n"
        f"進場價={signal['entry_price']}，原因：{signal['reason']}"
    )
    send_loss_guard_message(message)


def _send_second_add_signal(state: dict, second_position: dict, close: float, first_unrealized: float) -> None:
    side = second_position["side"]
    score = _second_side_confirm_score(side)
    if (
        second_position["quantity"] >= 2
        or first_unrealized < SECOND_ADD_PROFIT_POINTS
        or score < SECOND_ADD_CONFIRM_SCORE
    ):
        return

    signal = _build_add_signal(
        side,
        close,
        second_position["entry_price"],
        close,
        (
            f"scale in after +{SECOND_ADD_PROFIT_POINTS:.0f}pts, "
            f"{score}/3 short timeframes support"
        ),
        second_position["h_position_key"],
    )
    _append_guard_signal("add", signal)
    state["second_add_entry_price"] = close
    state["second_position_quantity"] = 2
    state["last_add_signal_at"] = now_str()
    _save_state(state)

    side_label = "多單" if side == "bull" else "空單"
    message = (
        f"第二帳號加碼訊號：{side_label} 加 1 口\n"
        f"第一口進場={second_position['entry_price']}，加碼價={close}，"
        f"第一口浮盈={first_unrealized:.1f} 點\n"
        f"確認={score}/3 個短週期支持"
    )
    send_loss_guard_message(message)


def _get_latest_close() -> float | None:
    latest_1m = _get_latest_row(WEBHOOK_CSV_BY_TF["1"])
    return _row_close(latest_1m)


def _manage_second_position(h_position: dict | None, state: dict) -> bool:
    second_position = _get_second_position(state)
    if second_position is None:
        return False

    close = _get_latest_close()
    if close is None:
        return False

    side = second_position["side"]
    entry_price = second_position["entry_price"]
    first_unrealized = _unrealized_points(side, entry_price, close)
    max_favorable = max(second_position["max_favorable_points"], first_unrealized)
    state["second_max_favorable_points"] = max_favorable

    _send_second_add_signal(state, second_position, close, first_unrealized)
    second_position = _get_second_position(state) or second_position

    entries = [entry_price]
    if second_position.get("add_entry_price") is not None:
        entries.append(second_position["add_entry_price"])
    total_unrealized = sum(_unrealized_points(side, entry, close) for entry in entries)

    reason = ""
    h_key = _position_key(h_position) if h_position else ""
    if h_key and h_key != second_position["h_position_key"]:
        reason = "H1 position changed"
    elif second_position["quantity"] >= 2 and first_unrealized <= SECOND_AFTER_ADD_PROTECT_POINTS:
        reason = f"second-account after-add protect {SECOND_AFTER_ADD_PROTECT_POINTS:.0f}pts"
    elif second_position["quantity"] < 2 and first_unrealized <= SECOND_STOP_LOSS_POINTS:
        reason = f"second-account stop loss {SECOND_STOP_LOSS_POINTS:.0f}pts"
    elif first_unrealized >= SECOND_TAKE_PROFIT_POINTS:
        reason = f"second-account take profit {SECOND_TAKE_PROFIT_POINTS:.0f}pts"
    elif max_favorable >= SECOND_TRAIL_ARM_POINTS and first_unrealized <= SECOND_TRAIL_FLOOR_POINTS:
        reason = (
            f"second-account giveback: max {max_favorable:.1f}pts, "
            f"now {first_unrealized:.1f}pts"
        )

    if not reason:
        _save_state(state)
        return False

    signal = _build_exit_signal(side, entry_price, close, reason, second_position["h_position_key"])
    signal["unrealized_points"] = total_unrealized
    _append_guard_signal("exit", signal)
    _clear_second_position(state)
    state["last_exit_signal_at"] = now_str()
    _save_state(state)

    side_label = "多單" if side == "bull" else "空單"
    message = (
        f"第二帳號出場訊號：{side_label}\n"
        f"進場={entry_price}，現價={close}，"
        f"第一口浮動={first_unrealized:.1f} 點，總浮動={total_unrealized:.1f} 點\n"
        f"原因：{reason}"
    )
    send_loss_guard_message(message)
    return True


def apply_h_loss_guard_strategy() -> None:
    """Emit second-account reverse-entry and exit Discord signals."""
    with LOSS_GUARD_LOCK:
        h_position = _get_latest_h_position()
        state = _load_state()

        if _manage_second_position(h_position, state):
            return

        if not h_position:
            return

        if _get_second_position(state) is not None:
            return

        guard_signal = _build_guard_signal(h_position)
        if guard_signal:
            _send_second_entry_signal(h_position, guard_signal, state)
