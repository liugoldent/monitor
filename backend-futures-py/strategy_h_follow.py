"""H follow strategy module.

這是一個跟單型策略，核心不是自己預測方向，而是先看 `h_trade.csv` 最新一筆有效方向，
再用 1 分鐘 K 線上的 MA_N200 / MA_P200 結構去找跟進點。

進場依據
- 只有當最新 H 單本身是獲利狀態時，才允許跟單。
  這個限制的目的，是避免去追一筆已經失真的參考方向。
- 多單：
  - close 正式站上 MA_N200，代表下方支撐被確認
  - 或影線打到 MA_N200 後收回上方，代表有打腳反應
  - 或 close 靠近 MA_N200，視為貼線確認
- 空單：
  - close 正式跌破 MA_P200，代表上方壓力被確認
  - 或影線插到 MA_P200 後收回下方，代表有壓回反應
  - 或 close 靠近 MA_P200，視為貼線確認

出場依據
- 停損：
  - 多單跌回 MA_N200 下方
  - 空單站回 MA_P200 上方
  這代表原本的支撐 / 壓力結構失效。
- 停利：
  - 多單先碰到 MA_P200，之後再跌回 MA_P200 下方
  - 空單先碰到 MA_N200，之後再站回 MA_N200 上方
  這代表已經先達到遠端目標，接下來只等反向確認後出場。

這一版的本質是「跟著已經獲利的參考單，利用短均線結構接力」，不是主觀猜方向。
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime
from threading import RLock, Thread

from strategy_common import (
    append_csv_row,
    build_shortcycle_send_discord_message,
    ensure_csv_header,
    now_str,
    read_last_n_rows,
    to_float,
)
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
BASE_DIR = os.path.dirname(__file__)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

TV_DOC_DIR = os.path.join(BASE_DIR, "tv_doc")

CSV_FILE_1MIN = os.path.join(TV_DOC_DIR, "webhook_data_1min.csv")
H_TRADE_CSV_PATH = os.path.join(TV_DOC_DIR, "h_trade.csv")
H_FOLLOW_TRADE_LOG_PATH = os.path.join(TV_DOC_DIR, "h_follow_trade.csv")
H_FOLLOW_STATE_PATH = os.path.join(TV_DOC_DIR, "h_follow_state.json")
MXF_VALUE_CSV_PATH = os.path.join(TV_DOC_DIR, "mxf_value.csv")

H_FOLLOW_NEAR_TOUCH_POINTS = 25.0
STRATEGY_LOCK = RLock()

shortcycle_send_discord_message = build_shortcycle_send_discord_message(MXF_VALUE_CSV_PATH)


def _default_state() -> dict:
    return {
        "position_side": "",
        "position_entry_price": "",
        "position_since": "",
        "take_profit_armed": "",
        "pending_action": "",
        "pending_side": "",
        "pending_since": "",
    }


def _load_state() -> dict:
    state = _default_state()
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


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(H_FOLLOW_STATE_PATH), exist_ok=True)
    with open(H_FOLLOW_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _clear_position(state: dict) -> None:
    state["position_side"] = ""
    state["position_entry_price"] = ""
    state["position_since"] = ""
    state["take_profit_armed"] = ""
    state["pending_action"] = ""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _set_position(state: dict, side: str, entry_price: float) -> None:
    state["position_side"] = side
    state["position_entry_price"] = entry_price
    state["position_since"] = now_str()
    state["take_profit_armed"] = ""
    state["pending_action"] = ""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _clear_pending(state: dict) -> None:
    state["pending_action"] = ""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _mark_pending(state: dict, action: str, side: str) -> None:
    state["pending_action"] = action
    state["pending_side"] = side
    state["pending_since"] = now_str()


def _parse_pending_since(raw_value: str):
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except ValueError:
        return None


def _is_pending_expired(state: dict) -> bool:
    pending_since = _parse_pending_since(str(state.get("pending_since", "")).strip())
    if pending_since is None:
        return True
    return (datetime.now(TZ) - pending_since).total_seconds() > 60 * 60


def _get_latest_h_trade_entry() -> dict | None:
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
        entry_price = to_float(row.get("price"))
        if entry_price is None:
            continue
        return {"timestamp": str(row.get("timestamp", "")).strip(), "side": side, "price": entry_price}
    return None


def _ensure_trade_log_header() -> None:
    ensure_csv_header(
        H_FOLLOW_TRADE_LOG_PATH,
        [
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
        ],
    )


def _append_trade(
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
    _ensure_trade_log_header()
    append_csv_row(
        H_FOLLOW_TRADE_LOG_PATH,
        [
            now_str(),
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
        ],
    )


def _get_unrealized_pnl(side: str, entry_price: float, close_price: float) -> float | None:
    if entry_price is None or close_price is None:
        return None
    if side == "bull":
        return close_price - entry_price
    if side == "bear":
        return entry_price - close_price
    return None


def _reason_zh(reason: str) -> str:
    mapping = {
        "stop loss": "停損",
        "take profit": "停利",
        "cross": "正式穿越確認",
        "wick": "影線測試確認",
        "near": "貼線確認",
    }
    return mapping.get(reason, reason)


def _trigger_entry(side: str, close_price: float, reason: str, latest_h_trade: dict, reference_pnl: float, ma_n200: float, ma_p200: float) -> None:
    def _runner() -> None:
        try:
            latest_side = str(latest_h_trade.get("side", "")).strip().lower()
            latest_entry_price = to_float(latest_h_trade.get("price"))
            zh_reason = _reason_zh(reason)
            note = (
                f"{'多單' if side == 'bull' else '空單'}進場：價格 {close_price}，原因：{zh_reason}，"
                f"最新H單進場價={latest_entry_price}，最新H單浮盈虧={reference_pnl}，"
                f"參考方向={latest_side}，MA_N200={ma_n200}，MA_P200={ma_p200}"
            )
            _append_trade(
                "enter",
                side,
                close_price,
                note,
                reference_side=latest_side,
                reference_entry_price=latest_entry_price,
                reference_close=close_price,
                reference_pnl=reference_pnl,
                ma_n200=ma_n200,
                ma_p200=ma_p200,
            )
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，H follow 進場訊號 {side}，原因：{zh_reason}（僅通知，不下單）"
            )
        finally:
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _set_position(state, side, close_price)
                _save_state(state)
            print(f"🔔 H follow entry alert({side}) because {reason}")

    Thread(target=_runner, daemon=True).start()


def _trigger_exit(side: str, close_price: float, reason: str, latest_h_trade: dict, reference_pnl: float, position_entry_price: float | None, position_since: str, ma_n200: float, ma_p200: float) -> None:
    def _runner() -> None:
        try:
            latest_side = str(latest_h_trade.get("side", "")).strip().lower()
            latest_entry_price = to_float(latest_h_trade.get("price"))
            zh_reason = _reason_zh(reason)
            note = (
                f"{'多單' if side == 'bull' else '空單'}出場：價格 {close_price}，進場價 {position_entry_price}，持有至 {position_since}，"
                f"出場原因：{zh_reason}，最新H方向={latest_side}，最新H單進場價={latest_entry_price}，"
                f"最新H單浮盈虧={reference_pnl}，MA_N200={ma_n200}，MA_P200={ma_p200}"
            )
            _append_trade(
                "exit",
                side,
                close_price,
                note,
                reference_side=latest_side,
                reference_entry_price=latest_entry_price,
                reference_close=close_price,
                reference_pnl=reference_pnl,
                ma_n200=ma_n200,
                ma_p200=ma_p200,
            )
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，H follow 平倉訊號 {side}，原因：{zh_reason}（僅通知，不下單）"
            )
        finally:
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _clear_position(state)
                _save_state(state)
            print(f"🔔 H follow exit alert({side}) because {reason}")

    Thread(target=_runner, daemon=True).start()


def _is_close_above_level(close_price: float, level_price: float) -> bool:
    return close_price > level_price


def _is_close_below_level(close_price: float, level_price: float) -> bool:
    return close_price < level_price


def _is_within_near_touch(close_price: float, level_price: float, points: float) -> bool:
    return abs(close_price - level_price) <= points


def apply_h_follow_strategy() -> bool:
    """Apply the H follow strategy."""
    with STRATEGY_LOCK:
        price_rows = read_last_n_rows(CSV_FILE_1MIN, 2)
        if len(price_rows) < 2:
            return False

        prev_price_row, curr_price_row = price_rows[-2], price_rows[-1]
        prev_close = to_float(prev_price_row.get("Close"))
        curr_close = to_float(curr_price_row.get("Close"))
        curr_low = to_float(curr_price_row.get("Low"))
        curr_high = to_float(curr_price_row.get("High"))
        prev_ma_p200 = to_float(prev_price_row.get("MA_P200"))
        curr_ma_p200 = to_float(curr_price_row.get("MA_P200"))
        prev_ma_n200 = to_float(prev_price_row.get("MA_N200"))
        curr_ma_n200 = to_float(curr_price_row.get("MA_N200"))

        if any(value is None for value in [prev_close, curr_close, curr_low, curr_high, prev_ma_p200, curr_ma_p200, prev_ma_n200, curr_ma_n200]):
            return False

        latest_h_trade = _get_latest_h_trade_entry()
        if latest_h_trade is None:
            return False

        latest_side = str(latest_h_trade.get("side", "")).strip().lower()
        latest_entry_price = to_float(latest_h_trade.get("price"))
        if latest_side not in {"bull", "bear"} or latest_entry_price is None:
            return False

        reference_close = curr_close
        reference_pnl = _get_unrealized_pnl(latest_side, latest_entry_price, reference_close)
        if reference_pnl is None:
            return False

        state = _load_state()
        position_side = str(state.get("position_side", "")).strip().lower()
        position_entry_price = to_float(state.get("position_entry_price"))
        position_since = str(state.get("position_since", "")).strip()
        take_profit_armed = str(state.get("take_profit_armed", "")).strip().lower() == "true"
        pending_action = str(state.get("pending_action", "")).strip().lower()
        if pending_action:
            if _is_pending_expired(state):
                _clear_pending(state)
                _save_state(state)
            else:
                return False

        # Entry signals follow the latest H trade direction.
        long_entry_close_ok = curr_close > curr_ma_n200
        long_cross_signal = prev_close <= prev_ma_n200 and long_entry_close_ok
        long_wick_signal = curr_low <= curr_ma_n200 and long_entry_close_ok
        long_near_signal = _is_close_above_level(curr_close, curr_ma_n200) and _is_within_near_touch(curr_close, curr_ma_n200, H_FOLLOW_NEAR_TOUCH_POINTS)

        short_entry_close_ok = curr_close < curr_ma_p200
        short_cross_signal = prev_close >= prev_ma_p200 and short_entry_close_ok
        short_wick_signal = curr_high >= curr_ma_p200 and short_entry_close_ok
        short_near_signal = _is_close_below_level(curr_close, curr_ma_p200) and _is_within_near_touch(curr_close, curr_ma_p200, H_FOLLOW_NEAR_TOUCH_POINTS)

        prev_long_cross = long_cross_signal or long_wick_signal or long_near_signal
        prev_short_cross = short_cross_signal or short_wick_signal or short_near_signal
        long_stop_loss = prev_close >= prev_ma_n200 and curr_close < curr_ma_n200
        short_stop_loss = prev_close <= prev_ma_p200 and curr_close > curr_ma_p200
        long_take_profit_arm = curr_close > curr_ma_p200
        short_take_profit_arm = curr_close < curr_ma_n200
        long_take_profit_exit = take_profit_armed and prev_close >= prev_ma_p200 and curr_close < curr_ma_p200
        short_take_profit_exit = take_profit_armed and prev_close <= prev_ma_n200 and curr_close > curr_ma_n200

        if position_side == "bull":
            if long_stop_loss or long_take_profit_exit:
                reason = "stop loss" if long_stop_loss else "take profit"
                _mark_pending(state, "exit", "bull")
                _save_state(state)
                _trigger_exit("bull", curr_close, reason, latest_h_trade, reference_pnl, position_entry_price, position_since, curr_ma_n200, curr_ma_p200)
                return True

            if not take_profit_armed and long_take_profit_arm:
                state["take_profit_armed"] = "true"
                _save_state(state)
            else:
                _save_state(state)
            return False

        if position_side == "bear":
            if short_stop_loss or short_take_profit_exit:
                reason = "stop loss" if short_stop_loss else "take profit"
                _mark_pending(state, "exit", "bear")
                _save_state(state)
                _trigger_exit("bear", curr_close, reason, latest_h_trade, reference_pnl, position_entry_price, position_since, curr_ma_n200, curr_ma_p200)
                return True

            if not take_profit_armed and short_take_profit_arm:
                state["take_profit_armed"] = "true"
                _save_state(state)
            else:
                _save_state(state)
            return False

        if latest_side == "bull" and reference_pnl > 0 and prev_long_cross and long_entry_close_ok:
            entry_reason = "near" if long_near_signal else "wick" if long_wick_signal else "cross"
            _mark_pending(state, "enter", "bull")
            _save_state(state)
            _trigger_entry("bull", curr_close, entry_reason, latest_h_trade, reference_pnl, curr_ma_n200, curr_ma_p200)
            return True

        if latest_side == "bear" and reference_pnl > 0 and prev_short_cross and short_entry_close_ok:
            entry_reason = "near" if short_near_signal else "wick" if short_wick_signal else "cross"
            _mark_pending(state, "enter", "bear")
            _save_state(state)
            _trigger_entry("bear", curr_close, entry_reason, latest_h_trade, reference_pnl, curr_ma_n200, curr_ma_p200)
            return True

        _save_state(state)
        return False
