"""TT/MXF live strategy.

這一版是 1 分鐘的保守 live 策略，核心思路是「順著 TT band 的突破做單」。

進場依據
- 多單：連續 2 根 K 都站在 TT band 上方，且 MXF 連續 2 筆維持 bull + gold。
  這代表價格已經不是單根假突破，而是短線真的站穩上方，同時 MXF 也確認有多方力道。
- 空單：連續 2 根 K 都跌在 TT band 下方，且 MXF 連續 2 筆維持 bear + death。
  這代表價格已經失守下方區間，而且 MXF 也確認空方延續。
- BBR 在這一版只當順勢濾網，避免在太弱或太亂的狀態下追單。

出場依據
- 停損：價格反向回到 TT band 的另一側，表示突破失敗或趨勢已破壞。
- 停利：固定點數停利，避免獲利回吐。
- 多單另外會在 MXF 翻空時出場，空單則在 MXF 翻多時出場。

這一版的設計重點不是抓大波段，而是先把方向確認做好，避免在區間裡頻繁被洗掉。
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
MXF_VALUE_CSV_PATH = os.path.join(TV_DOC_DIR, "mxf_value.csv")
TT_MXF_TRADE_LOG_PATH = os.path.join(TV_DOC_DIR, "tt_mxf_live_trade.csv")
TT_MXF_STATE_PATH = os.path.join(TV_DOC_DIR, "tt_mxf_live_state.json")

TT_MXF_ENABLE_LONG = False
TT_MXF_LONG_MAX_BREAKOUT_POINTS = 30.0
TT_MXF_SHORT_BBR_MAX = 0.5
TT_MXF_ENTRY_BREAKOUT_BUFFER_POINTS = 10.0
TT_MXF_STOP_LOSS_POINTS = 10.0
TT_MXF_TAKE_PROFIT_POINTS = 10.0
TT_MXF_PENDING_TIMEOUT_SECONDS = 60 * 60

STRATEGY_LOCK = RLock()
shortcycle_send_discord_message = build_shortcycle_send_discord_message(MXF_VALUE_CSV_PATH)


def _default_state() -> dict:
    return {
        "position_side": "",
        "position_entry_price": "",
        "position_since": "",
        "pending_action": "",
        "pending_side": "",
        "pending_since": "",
    }


def _load_state() -> dict:
    state = _default_state()
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


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(TT_MXF_STATE_PATH), exist_ok=True)
    with open(TT_MXF_STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)


def _clear_position(state: dict) -> None:
    state["position_side"] = ""
    state["position_entry_price"] = ""
    state["position_since"] = ""


def _clear_pending(state: dict) -> None:
    state["pending_action"] = ""
    state["pending_side"] = ""
    state["pending_since"] = ""


def _set_position(state: dict, side: str, entry_price: float) -> None:
    state["position_side"] = side
    state["position_entry_price"] = entry_price
    state["position_since"] = now_str()
    _clear_pending(state)


def _mark_pending(state: dict, action: str, side: str) -> None:
    state["pending_action"] = action
    state["pending_side"] = side
    state["pending_since"] = now_str()


def _parse_pending_since(raw_value: str) -> datetime | None:
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
    return (datetime.now(TZ) - pending_since).total_seconds() > TT_MXF_PENDING_TIMEOUT_SECONDS


def _ensure_trade_log_header() -> None:
    ensure_csv_header(
        TT_MXF_TRADE_LOG_PATH,
        [
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
        ],
    )


def _append_trade(action: str, side: str, price: float, note: str = "", mxf_row: dict | None = None, bbr: float | None = None) -> None:
    _ensure_trade_log_header()
    snapshot = mxf_row or {}
    append_csv_row(
        TT_MXF_TRADE_LOG_PATH,
        [
            now_str(),
            action,
            side,
            price,
            note,
            str(snapshot.get("signal", "")).strip(),
            str(snapshot.get("trend", "")).strip(),
            snapshot.get("tx_bvav", ""),
            snapshot.get("mtx_bvav", ""),
            snapshot.get("mtx_bvav_avg", ""),
            "" if bbr is None else bbr,
        ],
    )


def _is_mxf_bull(row: dict) -> bool:
    return str(row.get("signal", "")).strip().lower() == "bull" and str(row.get("trend", "")).strip().lower() == "gold"


def _is_mxf_bear(row: dict) -> bool:
    return str(row.get("signal", "")).strip().lower() == "bear" and str(row.get("trend", "")).strip().lower() == "death"


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
        "tt re-entry": "回到TT區間內",
        "mxf flip": "MXF翻轉",
        "breakout": "突破進場",
    }
    return mapping.get(reason, reason)


def _trigger_entry(side: str, close_price: float, reason: str, mxf_row: dict, bbr: float, note: str = "") -> None:
    def _runner() -> None:
        try:
            zh_reason = _reason_zh(reason)
            _append_trade("enter", side, close_price, note or f"進場原因：{zh_reason}", mxf_row=mxf_row, bbr=bbr)
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，TT/MXF 進場訊號 {side}，原因：{zh_reason}（僅通知，不下單）"
            )
        finally:
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _set_position(state, side, close_price)
                _save_state(state)
            print(f"🔔 TT/MXF live entry alert({side}) because {reason}")

    Thread(target=_runner, daemon=True).start()


def _trigger_exit(side: str, close_price: float, reason: str, mxf_row: dict, bbr: float, note: str = "") -> None:
    def _runner() -> None:
        try:
            zh_reason = _reason_zh(reason)
            _append_trade("exit", side, close_price, note or f"出場原因：{zh_reason}", mxf_row=mxf_row, bbr=bbr)
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，TT/MXF 平倉訊號 {side}，原因：{zh_reason}（僅通知，不下單）"
            )
        finally:
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _clear_position(state)
                _save_state(state)
            print(f"🔔 TT/MXF live exit alert({side}) because {reason}")

    Thread(target=_runner, daemon=True).start()


def apply_tt_mxf_live_strategy() -> bool:
    """Apply the live conservative TT/MXF strategy on 1-minute data."""
    with STRATEGY_LOCK:
        price_rows = read_last_n_rows(CSV_FILE_1MIN, 2)
        mxf_rows = read_last_n_rows(MXF_VALUE_CSV_PATH, 2)
        if len(price_rows) < 2 or len(mxf_rows) < 2:
            return False

        prev_price_row, curr_price_row = price_rows[-2], price_rows[-1]
        prev_mxf_row, curr_mxf_row = mxf_rows[-2], mxf_rows[-1]

        prev_close = to_float(prev_price_row.get("Close"))
        curr_close = to_float(curr_price_row.get("Close"))
        prev_tt_short = to_float(prev_price_row.get("tt_short"))
        prev_tt_long = to_float(prev_price_row.get("tt_long"))
        curr_tt_short = to_float(curr_price_row.get("tt_short"))
        curr_tt_long = to_float(curr_price_row.get("tt_long"))
        prev_bbr = to_float(prev_price_row.get("BBR"))
        curr_bbr = to_float(curr_price_row.get("BBR"))

        if any(value is None for value in [prev_close, curr_close, prev_tt_short, prev_tt_long, curr_tt_short, curr_tt_long, prev_bbr, curr_bbr]):
            return False

        prev_upper_tt = max(prev_tt_short, prev_tt_long)
        prev_lower_tt = min(prev_tt_short, prev_tt_long)
        curr_upper_tt = max(curr_tt_short, curr_tt_long)
        curr_lower_tt = min(curr_tt_short, curr_tt_long)

        prev_close_above_tt = prev_close > prev_upper_tt
        curr_close_above_tt = curr_close > curr_upper_tt
        prev_close_below_tt = prev_close < prev_lower_tt
        curr_close_below_tt = curr_close < curr_lower_tt
        curr_close_inside_tt = not curr_close_above_tt and not curr_close_below_tt

        mxf_bull = _is_mxf_bull(prev_mxf_row) and _is_mxf_bull(curr_mxf_row)
        mxf_bear = _is_mxf_bear(prev_mxf_row) and _is_mxf_bear(curr_mxf_row)

        long_momentum_ok = curr_bbr >= prev_bbr and curr_bbr >= 0
        short_momentum_ok = curr_bbr <= prev_bbr and curr_bbr <= 1

        state = _load_state()
        position_side = str(state.get("position_side", "")).strip().lower()
        position_entry_price = to_float(state.get("position_entry_price"))
        pending_action = str(state.get("pending_action", "")).strip().lower()
        if pending_action:
            if _is_pending_expired(state):
                _clear_pending(state)
                _save_state(state)
            else:
                return False

        if position_side == "bull":
            bull_unrealized_pnl = _get_unrealized_pnl("bull", position_entry_price, curr_close)
            if (
                (bull_unrealized_pnl is not None and bull_unrealized_pnl <= -TT_MXF_STOP_LOSS_POINTS)
                or (bull_unrealized_pnl is not None and bull_unrealized_pnl >= TT_MXF_TAKE_PROFIT_POINTS)
                or curr_close_inside_tt
                or mxf_bear
                or curr_close_below_tt
            ):
                reason = (
                    "stop loss" if bull_unrealized_pnl is not None and bull_unrealized_pnl <= -TT_MXF_STOP_LOSS_POINTS
                    else "take profit" if bull_unrealized_pnl is not None and bull_unrealized_pnl >= TT_MXF_TAKE_PROFIT_POINTS
                    else "tt re-entry" if curr_close_inside_tt
                    else "mxf flip"
                )
                zh_reason = _reason_zh(reason)
                note = (
                    f"多單出場：價格 {curr_close}，進場價 {position_entry_price}，持有至 {state.get('position_since', '')}，"
                    f"出場原因：{zh_reason}，TT短線={curr_tt_short}，TT長線={curr_tt_long}，"
                    f"MXF訊號={curr_mxf_row.get('signal', '')}，MXF趨勢={curr_mxf_row.get('trend', '')}"
                )
                _mark_pending(state, "exit", "bull")
                _save_state(state)
                _trigger_exit("bull", curr_close, reason, curr_mxf_row, curr_bbr, note)
                return True
            _save_state(state)
            return False

        if position_side == "bear":
            bear_unrealized_pnl = _get_unrealized_pnl("bear", position_entry_price, curr_close)
            if (
                (bear_unrealized_pnl is not None and bear_unrealized_pnl <= -TT_MXF_STOP_LOSS_POINTS)
                or (bear_unrealized_pnl is not None and bear_unrealized_pnl >= TT_MXF_TAKE_PROFIT_POINTS)
                or curr_close_inside_tt
                or mxf_bull
                or curr_close_above_tt
            ):
                reason = (
                    "stop loss" if bear_unrealized_pnl is not None and bear_unrealized_pnl <= -TT_MXF_STOP_LOSS_POINTS
                    else "take profit" if bear_unrealized_pnl is not None and bear_unrealized_pnl >= TT_MXF_TAKE_PROFIT_POINTS
                    else "tt re-entry" if curr_close_inside_tt
                    else "mxf flip"
                )
                zh_reason = _reason_zh(reason)
                note = (
                    f"空單出場：價格 {curr_close}，進場價 {position_entry_price}，持有至 {state.get('position_since', '')}，"
                    f"出場原因：{zh_reason}，TT短線={curr_tt_short}，TT長線={curr_tt_long}，"
                    f"MXF訊號={curr_mxf_row.get('signal', '')}，MXF趨勢={curr_mxf_row.get('trend', '')}"
                )
                _mark_pending(state, "exit", "bear")
                _save_state(state)
                _trigger_exit("bear", curr_close, reason, curr_mxf_row, curr_bbr, note)
                return True
            _save_state(state)
            return False

        if (
            prev_close_above_tt
            and curr_close_above_tt
            and mxf_bull
            and long_momentum_ok
            and TT_MXF_ENABLE_LONG
            and (curr_close - curr_upper_tt) <= TT_MXF_LONG_MAX_BREAKOUT_POINTS
        ):
            note = (
                f"多單進場：價格 {curr_close}，前一根收盤 {prev_close}，TT短線={curr_tt_short}，TT長線={curr_tt_long}，"
                f"MXF訊號={curr_mxf_row.get('signal', '')}，MXF趨勢={curr_mxf_row.get('trend', '')}"
            )
            _mark_pending(state, "enter", "bull")
            _save_state(state)
            _trigger_entry("bull", curr_close, "突破進場", curr_mxf_row, curr_bbr, note)
            return True

        if (
            prev_close_below_tt
            and curr_close_below_tt
            and mxf_bear
            and short_momentum_ok
            and curr_close <= curr_lower_tt - TT_MXF_ENTRY_BREAKOUT_BUFFER_POINTS
            and curr_bbr <= TT_MXF_SHORT_BBR_MAX
        ):
            note = (
                f"空單進場：價格 {curr_close}，前一根收盤 {prev_close}，TT短線={curr_tt_short}，TT長線={curr_tt_long}，"
                f"MXF訊號={curr_mxf_row.get('signal', '')}，MXF趨勢={curr_mxf_row.get('trend', '')}"
            )
            _mark_pending(state, "enter", "bear")
            _save_state(state)
            _trigger_entry("bear", curr_close, "突破進場", curr_mxf_row, curr_bbr, note)
            return True

        _save_state(state)
        return False
