"""TT/MXF draft strategy.

這一版是 15 分鐘的候選策略，用來測試「TT band 突破 + BBR + MXF 順勢確認」是否真的有邊際優勢。

進場依據
- 多單：15 分鐘 close 連續站在 TT band 上方，且 MXF 連續 2 筆都是 bull + gold。
  這代表價格站穩上方突破，MXF 也同步確認多方力道。
- 空單：15 分鐘 close 連續跌破 TT band 下方，且 MXF 連續 2 筆都是 bear + death。
  這代表價格跌破下方區間，MXF 也同步確認空方力道。
- BBR 用來做強弱濾網：
  - 多單要求 BBR 偏強，避免在上方突破但動能不足時追多
  - 空單要求 BBR 偏弱，避免在下方跌破但動能不足時追空

出場依據
- 停損：固定點數停損，控制單筆風險。
- 停利：固定點數停利，避免候選策略樣本太少時把已出現的優勢吐回去。
- TT re-entry：價格回到 TT band 內，表示原本的「帶外突破」已經消失。
- MXF fade：MXF 不再維持進場方向，表示原本支持這筆交易的力道已經失效。

這一版不是拿來直接宣稱最強，而是把樣本內看起來有優勢的結構先獨立保存，之後再用更多資料驗證。
"""

from __future__ import annotations

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
CSV_FILE_15MIN = os.path.join(TV_DOC_DIR, "webhook_data_15min.csv")
MXF_VALUE_CSV_PATH = os.path.join(TV_DOC_DIR, "mxf_value.csv")
TT_MXF_DRAFT_TRADE_LOG_PATH = os.path.join(TV_DOC_DIR, "tt_mxf_draft_trade.csv")
TT_MXF_DRAFT_STATE_PATH = os.path.join(TV_DOC_DIR, "tt_mxf_draft_state.json")

TT_MXF_DRAFT_TIMEFRAME = "15"
TT_MXF_DRAFT_LONG_BBR_MIN = 0.5
TT_MXF_DRAFT_SHORT_BBR_MAX = 0.5
TT_MXF_DRAFT_STOP_LOSS_POINTS = 25.0
TT_MXF_DRAFT_TAKE_PROFIT_POINTS = 50.0
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
    if not os.path.isfile(TT_MXF_DRAFT_STATE_PATH):
        return state
    try:
        with open(TT_MXF_DRAFT_STATE_PATH, "r", encoding="utf-8") as handle:
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
    os.makedirs(os.path.dirname(TT_MXF_DRAFT_STATE_PATH), exist_ok=True)
    with open(TT_MXF_DRAFT_STATE_PATH, "w", encoding="utf-8") as handle:
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
        TT_MXF_DRAFT_TRADE_LOG_PATH,
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
            "timeframe",
            "tt_short",
            "tt_long",
        ],
    )


def _append_trade(action: str, side: str, price: float, note: str, mxf_row: dict, bbr: float, timeframe: str, tt_short: float, tt_long: float) -> None:
    _ensure_trade_log_header()
    append_csv_row(
        TT_MXF_DRAFT_TRADE_LOG_PATH,
        [
            now_str(),
            action,
            side,
            price,
            note,
            str(mxf_row.get("signal", "")).strip(),
            str(mxf_row.get("trend", "")).strip(),
            mxf_row.get("tx_bvav", ""),
            mxf_row.get("mtx_bvav", ""),
            mxf_row.get("mtx_bvav_avg", ""),
            bbr,
            timeframe,
            tt_short,
            tt_long,
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
        "trend long": "順勢突破做多",
        "trend short": "順勢跌破做空",
    }
    return mapping.get(reason, reason)


def _trigger_entry(side: str, close_price: float, reason: str, mxf_row: dict, bbr: float, timeframe: str, tt_short: float, tt_long: float, note: str = "") -> None:
    def _runner() -> None:
        zh_reason = _reason_zh(reason)
        try:
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，TT/MXF 草案進場訊號 {side}，原因：{zh_reason}（僅通知，不下單）"
            )
            _append_trade("enter", side, close_price, note or f"進場原因：{zh_reason}", mxf_row, bbr, timeframe, tt_short, tt_long)
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _set_position(state, side, close_price)
                _save_state(state)
            print(f"🔔 TT/MXF draft entry alert({side}) because {reason}")
        except Exception as exc:
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _save_state(state)
            print(f"⚠️ TT/MXF draft entry alert({side}) failed before state update: {exc}")

    Thread(target=_runner, daemon=True).start()


def _trigger_exit(side: str, close_price: float, reason: str, mxf_row: dict, bbr: float, timeframe: str, tt_short: float, tt_long: float, note: str = "") -> None:
    def _runner() -> None:
        zh_reason = _reason_zh(reason)
        try:
            shortcycle_send_discord_message(
                f"webhook_server: close={close_price}，TT/MXF 草案平倉訊號 {side}，原因：{zh_reason}（僅通知，不下單）"
            )
            _append_trade("exit", side, close_price, note or f"出場原因：{zh_reason}", mxf_row, bbr, timeframe, tt_short, tt_long)
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _clear_position(state)
                _save_state(state)
            print(f"🔔 TT/MXF draft exit alert({side}) because {reason}")
        except Exception as exc:
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _save_state(state)
            print(f"⚠️ TT/MXF draft exit alert({side}) failed before state update: {exc}")

    Thread(target=_runner, daemon=True).start()


def apply_tt_mxf_draft_strategy() -> bool:
    """Apply the candidate 15-minute TT/MXF draft strategy."""
    with STRATEGY_LOCK:
        price_rows = read_last_n_rows(CSV_FILE_15MIN, 2)
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
        curr_bbr = to_float(curr_price_row.get("BBR"))

        if any(value is None for value in [prev_close, curr_close, prev_tt_short, prev_tt_long, curr_tt_short, curr_tt_long, curr_bbr]):
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

        long_mxf_confirm = _is_mxf_bull(prev_mxf_row) and _is_mxf_bull(curr_mxf_row)
        short_mxf_confirm = _is_mxf_bear(prev_mxf_row) and _is_mxf_bear(curr_mxf_row)
        long_strength_ok = curr_bbr >= TT_MXF_DRAFT_LONG_BBR_MIN
        short_strength_ok = curr_bbr <= TT_MXF_DRAFT_SHORT_BBR_MAX

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

        timeframe = str(curr_price_row.get("Timeframe", TT_MXF_DRAFT_TIMEFRAME)).strip() or TT_MXF_DRAFT_TIMEFRAME

        if position_side == "bull":
            bull_unrealized_pnl = _get_unrealized_pnl("bull", position_entry_price, curr_close)
            if (
                (bull_unrealized_pnl is not None and bull_unrealized_pnl <= -TT_MXF_DRAFT_STOP_LOSS_POINTS)
                or (bull_unrealized_pnl is not None and bull_unrealized_pnl >= TT_MXF_DRAFT_TAKE_PROFIT_POINTS)
                or curr_close_inside_tt
                or not _is_mxf_bull(curr_mxf_row)
            ):
                reason = (
                    "stop loss" if bull_unrealized_pnl is not None and bull_unrealized_pnl <= -TT_MXF_DRAFT_STOP_LOSS_POINTS
                    else "take profit" if bull_unrealized_pnl is not None and bull_unrealized_pnl >= TT_MXF_DRAFT_TAKE_PROFIT_POINTS
                    else "tt re-entry" if curr_close_inside_tt
                    else "mxf flip"
                )
                zh_reason = _reason_zh(reason)
                note = (
                    f"多單出場：價格 {curr_close}，進場價 {position_entry_price}，持有至 {state.get('position_since', '')}，"
                    f"出場原因：{zh_reason}，訊號={curr_mxf_row.get('signal', '')}，趨勢={curr_mxf_row.get('trend', '')}"
                )
                _mark_pending(state, "exit", "bull")
                _save_state(state)
                _trigger_exit("bull", curr_close, reason, curr_mxf_row, curr_bbr, timeframe, curr_tt_short, curr_tt_long, note=note)
                return True
            _save_state(state)
            return False

        if position_side == "bear":
            bear_unrealized_pnl = _get_unrealized_pnl("bear", position_entry_price, curr_close)
            if (
                (bear_unrealized_pnl is not None and bear_unrealized_pnl <= -TT_MXF_DRAFT_STOP_LOSS_POINTS)
                or (bear_unrealized_pnl is not None and bear_unrealized_pnl >= TT_MXF_DRAFT_TAKE_PROFIT_POINTS)
                or curr_close_inside_tt
                or not _is_mxf_bear(curr_mxf_row)
            ):
                reason = (
                    "stop loss" if bear_unrealized_pnl is not None and bear_unrealized_pnl <= -TT_MXF_DRAFT_STOP_LOSS_POINTS
                    else "take profit" if bear_unrealized_pnl is not None and bear_unrealized_pnl >= TT_MXF_DRAFT_TAKE_PROFIT_POINTS
                    else "tt re-entry" if curr_close_inside_tt
                    else "mxf flip"
                )
                zh_reason = _reason_zh(reason)
                note = (
                    f"空單出場：價格 {curr_close}，進場價 {position_entry_price}，持有至 {state.get('position_since', '')}，"
                    f"出場原因：{zh_reason}，訊號={curr_mxf_row.get('signal', '')}，趨勢={curr_mxf_row.get('trend', '')}"
                )
                _mark_pending(state, "exit", "bear")
                _save_state(state)
                _trigger_exit("bear", curr_close, reason, curr_mxf_row, curr_bbr, timeframe, curr_tt_short, curr_tt_long, note=note)
                return True
            _save_state(state)
            return False

        if prev_close_above_tt and curr_close_above_tt and long_mxf_confirm and long_strength_ok:
            strength = "strong" if curr_bbr >= 0.8 else "normal"
            note = (
                f"多單進場：價格 {curr_close}，週期={timeframe}分，強度={strength}，"
                f"TT短線={curr_tt_short}，TT長線={curr_tt_long}，訊號={curr_mxf_row.get('signal', '')}，"
                f"趨勢={curr_mxf_row.get('trend', '')}，BBR={curr_bbr}"
            )
            _mark_pending(state, "enter", "bull")
            _save_state(state)
            _trigger_entry("bull", curr_close, "trend long", curr_mxf_row, curr_bbr, timeframe, curr_tt_short, curr_tt_long, note=note)
            return True

        if prev_close_below_tt and curr_close_below_tt and short_mxf_confirm and short_strength_ok:
            strength = "strong" if curr_bbr <= 0.2 else "normal"
            note = (
                f"空單進場：價格 {curr_close}，週期={timeframe}分，強度={strength}，"
                f"TT短線={curr_tt_short}，TT長線={curr_tt_long}，訊號={curr_mxf_row.get('signal', '')}，"
                f"趨勢={curr_mxf_row.get('trend', '')}，BBR={curr_bbr}"
            )
            _mark_pending(state, "enter", "bear")
            _save_state(state)
            _trigger_entry("bear", curr_close, "trend short", curr_mxf_row, curr_bbr, timeframe, curr_tt_short, curr_tt_long, note=note)
            return True

        _save_state(state)
        return False
