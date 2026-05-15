"""TT/MXF draft strategy.

這一版是多週期候選策略，用 15 分鐘產生主訊號，再用 5/10 分鐘與 MXF 力道過濾假突破。

進場依據
- 多單：15 分鐘 close 連續站在 TT band 上方，5/10 分鐘也站在 TT band 上方，
  且 MXF 連續 2 筆都是 bull + gold。當前 mtx_bvav 必須 >= 1100，
  mtx_bvav_avg 也要在 0 以上，避免弱多或剛翻多就追。
- 空單：15 分鐘 close 連續跌破 TT band 下方，5/10 分鐘也跌破 TT band 下方，
  且 MXF 連續 2 筆都是 bear + death。當前 mtx_bvav 必須 <= -1100，
  mtx_bvav_avg 也要在 0 以下。
- BBR 用來做強弱濾網，但不追過熱/過冷：
  - 多單要求 BBR 偏強，但避開已經過熱的位置
  - 空單要求 BBR 偏弱
- 進場位置不能離 15 分鐘 TT band 太遠，避免追在延伸尾端。

出場依據
- 停損：依近期 15 分 K 波動動態放大，避免正常震盪造成過早停損。
- 停利：依近期 15 分 K 波動動態放大，並把單趟約 3 點交易成本納入最低門檻。
- TT re-entry：1/3/5/10/15 分鐘最新價格回到該週期 TT band 內，表示原本的「帶外突破」已經消失。
- MXF flip：MXF 翻到反方向，表示原本支持這筆交易的力道已經失效。
- 風控會在所有 webhook 週期進來時檢查，但新進場只允許由 15 分鐘訊號觸發。

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
CSV_FILE_1MIN = os.path.join(TV_DOC_DIR, "webhook_data_1min.csv")
CSV_FILE_3MIN = os.path.join(TV_DOC_DIR, "webhook_data_3min.csv")
CSV_FILE_5MIN = os.path.join(TV_DOC_DIR, "webhook_data_5min.csv")
CSV_FILE_10MIN = os.path.join(TV_DOC_DIR, "webhook_data_10min.csv")
CSV_FILE_15MIN = os.path.join(TV_DOC_DIR, "webhook_data_15min.csv")
MXF_VALUE_CSV_PATH = os.path.join(TV_DOC_DIR, "mxf_value.csv")
TT_MXF_DRAFT_TRADE_LOG_PATH = os.path.join(TV_DOC_DIR, "tt_mxf_draft_trade.csv")
TT_MXF_DRAFT_STATE_PATH = os.path.join(TV_DOC_DIR, "tt_mxf_draft_state.json")

TT_MXF_DRAFT_TIMEFRAME = "15"
TT_MXF_DRAFT_CONFIRM_TIMEFRAMES = ("5", "10")
TT_MXF_DRAFT_LONG_BBR_MIN = 0.55
TT_MXF_DRAFT_LONG_BBR_MAX = 1.00
TT_MXF_DRAFT_SHORT_BBR_MAX = 0.45
TT_MXF_DRAFT_LONG_MTX_BVAV_MIN = 1100.0
TT_MXF_DRAFT_SHORT_MTX_BVAV_MAX = -1100.0
TT_MXF_DRAFT_LONG_MTX_BVAV_AVG_MIN = 0.0
TT_MXF_DRAFT_SHORT_MTX_BVAV_AVG_MAX = 0.0
TT_MXF_DRAFT_MAX_TT_EXTENSION_POINTS = 220.0
TT_MXF_DRAFT_COOLDOWN_SECONDS = 15 * 60
TT_MXF_DRAFT_ROUND_TRIP_COST_POINTS = 3.0
TT_MXF_DRAFT_ATR_LOOKBACK_BARS = 20
TT_MXF_DRAFT_STOP_LOSS_MIN_POINTS = 95.0
TT_MXF_DRAFT_TAKE_PROFIT_MIN_POINTS = 190.0
TT_MXF_DRAFT_STOP_LOSS_ATR_MULTIPLIER = 1.2
TT_MXF_DRAFT_TAKE_PROFIT_ATR_MULTIPLIER = 2.4
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
        "last_entry_signal_time": "",
        "last_exit_time": "",
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
    state["last_entry_signal_time"] = str(raw_state.get("last_entry_signal_time", "")).strip()
    state["last_exit_time"] = str(raw_state.get("last_exit_time", "")).strip()
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


def _mark_entry_signal(state: dict, signal_time: str) -> None:
    state["last_entry_signal_time"] = signal_time


def _mark_exit_time(state: dict) -> None:
    state["last_exit_time"] = now_str()


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


def _parse_state_time(raw_value: str) -> datetime | None:
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


def _is_cooling_down(state: dict) -> bool:
    last_exit_time = _parse_state_time(str(state.get("last_exit_time", "")).strip())
    if last_exit_time is None:
        return False
    return (datetime.now(TZ) - last_exit_time).total_seconds() < TT_MXF_DRAFT_COOLDOWN_SECONDS


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


def _is_long_mtx_bvav_strong(row: dict) -> bool:
    mtx_bvav = to_float(row.get("mtx_bvav"))
    return mtx_bvav is not None and mtx_bvav >= TT_MXF_DRAFT_LONG_MTX_BVAV_MIN


def _is_short_mtx_bvav_strong(row: dict) -> bool:
    mtx_bvav = to_float(row.get("mtx_bvav"))
    return mtx_bvav is not None and mtx_bvav <= TT_MXF_DRAFT_SHORT_MTX_BVAV_MAX


def _is_long_mxf_pressure_strong(row: dict) -> bool:
    mtx_bvav_avg = to_float(row.get("mtx_bvav_avg"))
    return (
        _is_long_mtx_bvav_strong(row)
        and mtx_bvav_avg is not None
        and mtx_bvav_avg >= TT_MXF_DRAFT_LONG_MTX_BVAV_AVG_MIN
    )


def _is_short_mxf_pressure_strong(row: dict) -> bool:
    mtx_bvav_avg = to_float(row.get("mtx_bvav_avg"))
    return (
        _is_short_mtx_bvav_strong(row)
        and mtx_bvav_avg is not None
        and mtx_bvav_avg <= TT_MXF_DRAFT_SHORT_MTX_BVAV_AVG_MAX
    )


def _get_latest_price_row(path: str) -> dict:
    rows = read_last_n_rows(path, 1)
    return rows[-1] if rows else {}


def _get_latest_execution_row() -> dict:
    for path in (CSV_FILE_1MIN, CSV_FILE_3MIN, CSV_FILE_5MIN, CSV_FILE_10MIN, CSV_FILE_15MIN):
        row = _get_latest_price_row(path)
        close_price = to_float(row.get("Close"))
        if close_price is not None:
            return row
    return {}


def _get_tt_bounds(row: dict) -> tuple[float, float] | None:
    tt_short = to_float(row.get("tt_short"))
    tt_long = to_float(row.get("tt_long"))
    if tt_short is None or tt_long is None:
        return None
    return max(tt_short, tt_long), min(tt_short, tt_long)


def _is_close_above_tt(row: dict) -> bool:
    close_price = to_float(row.get("Close"))
    bounds = _get_tt_bounds(row)
    return close_price is not None and bounds is not None and close_price > bounds[0]


def _is_close_below_tt(row: dict) -> bool:
    close_price = to_float(row.get("Close"))
    bounds = _get_tt_bounds(row)
    return close_price is not None and bounds is not None and close_price < bounds[1]


def _is_close_inside_tt(row: dict) -> bool:
    close_price = to_float(row.get("Close"))
    bounds = _get_tt_bounds(row)
    return close_price is not None and bounds is not None and bounds[1] <= close_price <= bounds[0]


def _get_latest_confirmation_rows() -> dict[str, dict]:
    return {
        "5": _get_latest_price_row(CSV_FILE_5MIN),
        "10": _get_latest_price_row(CSV_FILE_10MIN),
    }


def _is_multitimeframe_confirmed(side: str, rows_by_timeframe: dict[str, dict]) -> bool:
    for timeframe in TT_MXF_DRAFT_CONFIRM_TIMEFRAMES:
        row = rows_by_timeframe.get(timeframe) or {}
        bbr = to_float(row.get("BBR"))
        if side == "bull":
            if not _is_close_above_tt(row) or bbr is None or bbr < 0.50:
                return False
        elif side == "bear":
            if not _is_close_below_tt(row) or bbr is None or bbr > 0.50:
                return False
        else:
            return False
    return True


def _get_unrealized_pnl(side: str, entry_price: float, close_price: float) -> float | None:
    if entry_price is None or close_price is None:
        return None
    if side == "bull":
        return close_price - entry_price
    if side == "bear":
        return entry_price - close_price
    return None


def _get_average_range(rows: list[dict]) -> float | None:
    ranges: list[float] = []
    for row in rows[-TT_MXF_DRAFT_ATR_LOOKBACK_BARS:]:
        high = to_float(row.get("High"))
        low = to_float(row.get("Low"))
        if high is None or low is None:
            continue
        if high >= low:
            ranges.append(high - low)
    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def _get_exit_thresholds(rows: list[dict]) -> tuple[float, float]:
    average_range = _get_average_range(rows)
    if average_range is None:
        return TT_MXF_DRAFT_STOP_LOSS_MIN_POINTS, TT_MXF_DRAFT_TAKE_PROFIT_MIN_POINTS

    stop_loss_points = max(
        TT_MXF_DRAFT_STOP_LOSS_MIN_POINTS,
        average_range * TT_MXF_DRAFT_STOP_LOSS_ATR_MULTIPLIER,
    )
    take_profit_points = max(
        TT_MXF_DRAFT_TAKE_PROFIT_MIN_POINTS,
        average_range * TT_MXF_DRAFT_TAKE_PROFIT_ATR_MULTIPLIER + TT_MXF_DRAFT_ROUND_TRIP_COST_POINTS,
    )
    return round(stop_loss_points, 1), round(take_profit_points, 1)


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
                _mark_exit_time(state)
                _save_state(state)
            print(f"🔔 TT/MXF draft exit alert({side}) because {reason}")
        except Exception as exc:
            with STRATEGY_LOCK:
                state = _load_state()
                _clear_pending(state)
                _save_state(state)
            print(f"⚠️ TT/MXF draft exit alert({side}) failed before state update: {exc}")

    Thread(target=_runner, daemon=True).start()


def apply_tt_mxf_draft_strategy(trigger_timeframe: str | None = None) -> bool:
    """Apply the multi-timeframe TT/MXF draft strategy."""
    with STRATEGY_LOCK:
        price_rows = read_last_n_rows(CSV_FILE_15MIN, max(2, TT_MXF_DRAFT_ATR_LOOKBACK_BARS))
        mxf_rows = read_last_n_rows(MXF_VALUE_CSV_PATH, 2)
        if len(price_rows) < 2 or len(mxf_rows) < 2:
            return False

        prev_price_row, curr_price_row = price_rows[-2], price_rows[-1]
        prev_mxf_row, curr_mxf_row = mxf_rows[-2], mxf_rows[-1]
        execution_row = _get_latest_execution_row()
        confirmation_rows = _get_latest_confirmation_rows()

        prev_close = to_float(prev_price_row.get("Close"))
        curr_close = to_float(curr_price_row.get("Close"))
        execution_close = to_float(execution_row.get("Close"))
        prev_tt_short = to_float(prev_price_row.get("tt_short"))
        prev_tt_long = to_float(prev_price_row.get("tt_long"))
        curr_tt_short = to_float(curr_price_row.get("tt_short"))
        curr_tt_long = to_float(curr_price_row.get("tt_long"))
        curr_bbr = to_float(curr_price_row.get("BBR"))

        if any(value is None for value in [prev_close, curr_close, execution_close, prev_tt_short, prev_tt_long, curr_tt_short, curr_tt_long, curr_bbr]):
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
        long_mtx_bvav_ok = _is_long_mxf_pressure_strong(curr_mxf_row)
        short_mtx_bvav_ok = _is_short_mxf_pressure_strong(curr_mxf_row)
        long_strength_ok = TT_MXF_DRAFT_LONG_BBR_MIN <= curr_bbr <= TT_MXF_DRAFT_LONG_BBR_MAX
        short_strength_ok = curr_bbr <= TT_MXF_DRAFT_SHORT_BBR_MAX
        long_extension_ok = (curr_close - curr_upper_tt) <= TT_MXF_DRAFT_MAX_TT_EXTENSION_POINTS
        short_extension_ok = (curr_lower_tt - curr_close) <= TT_MXF_DRAFT_MAX_TT_EXTENSION_POINTS
        stop_loss_points, take_profit_points = _get_exit_thresholds(price_rows)

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

        execution_timeframe = str(execution_row.get("Timeframe", TT_MXF_DRAFT_TIMEFRAME)).strip() or TT_MXF_DRAFT_TIMEFRAME
        timeframe = str(curr_price_row.get("Timeframe", TT_MXF_DRAFT_TIMEFRAME)).strip() or TT_MXF_DRAFT_TIMEFRAME
        signal_time = str(curr_price_row.get("TradingView Time", "")).strip()

        if position_side == "bull":
            bull_unrealized_pnl = _get_unrealized_pnl("bull", position_entry_price, execution_close)
            execution_inside_tt = _is_close_inside_tt(execution_row)
            execution_below_tt = _is_close_below_tt(execution_row)
            if (
                (bull_unrealized_pnl is not None and bull_unrealized_pnl <= -stop_loss_points)
                or (bull_unrealized_pnl is not None and bull_unrealized_pnl >= take_profit_points)
                or execution_inside_tt
                or execution_below_tt
                or _is_mxf_bear(curr_mxf_row)
            ):
                reason = (
                    "stop loss" if bull_unrealized_pnl is not None and bull_unrealized_pnl <= -stop_loss_points
                    else "take profit" if bull_unrealized_pnl is not None and bull_unrealized_pnl >= take_profit_points
                    else "tt re-entry" if execution_inside_tt
                    else "mxf flip"
                )
                zh_reason = _reason_zh(reason)
                note = (
                    f"多單出場：價格 {execution_close}，進場價 {position_entry_price}，持有至 {state.get('position_since', '')}，"
                    f"出場原因：{zh_reason}，訊號={curr_mxf_row.get('signal', '')}，趨勢={curr_mxf_row.get('trend', '')}，"
                    f"執行週期={execution_timeframe}分，動態停損={stop_loss_points}，動態停利={take_profit_points}，"
                    f"估計成本={TT_MXF_DRAFT_ROUND_TRIP_COST_POINTS}"
                )
                _mark_pending(state, "exit", "bull")
                _save_state(state)
                _trigger_exit("bull", execution_close, reason, curr_mxf_row, curr_bbr, execution_timeframe, curr_tt_short, curr_tt_long, note=note)
                return True
            _save_state(state)
            return False

        if position_side == "bear":
            bear_unrealized_pnl = _get_unrealized_pnl("bear", position_entry_price, execution_close)
            execution_inside_tt = _is_close_inside_tt(execution_row)
            execution_above_tt = _is_close_above_tt(execution_row)
            if (
                (bear_unrealized_pnl is not None and bear_unrealized_pnl <= -stop_loss_points)
                or (bear_unrealized_pnl is not None and bear_unrealized_pnl >= take_profit_points)
                or execution_inside_tt
                or execution_above_tt
                or _is_mxf_bull(curr_mxf_row)
            ):
                reason = (
                    "stop loss" if bear_unrealized_pnl is not None and bear_unrealized_pnl <= -stop_loss_points
                    else "take profit" if bear_unrealized_pnl is not None and bear_unrealized_pnl >= take_profit_points
                    else "tt re-entry" if execution_inside_tt
                    else "mxf flip"
                )
                zh_reason = _reason_zh(reason)
                note = (
                    f"空單出場：價格 {execution_close}，進場價 {position_entry_price}，持有至 {state.get('position_since', '')}，"
                    f"出場原因：{zh_reason}，訊號={curr_mxf_row.get('signal', '')}，趨勢={curr_mxf_row.get('trend', '')}，"
                    f"執行週期={execution_timeframe}分，動態停損={stop_loss_points}，動態停利={take_profit_points}，"
                    f"估計成本={TT_MXF_DRAFT_ROUND_TRIP_COST_POINTS}"
                )
                _mark_pending(state, "exit", "bear")
                _save_state(state)
                _trigger_exit("bear", execution_close, reason, curr_mxf_row, curr_bbr, execution_timeframe, curr_tt_short, curr_tt_long, note=note)
                return True
            _save_state(state)
            return False

        if trigger_timeframe is not None and str(trigger_timeframe).strip() != TT_MXF_DRAFT_TIMEFRAME:
            _save_state(state)
            return False

        if _is_cooling_down(state) or (signal_time and signal_time == str(state.get("last_entry_signal_time", "")).strip()):
            _save_state(state)
            return False

        if (
            prev_close_above_tt
            and curr_close_above_tt
            and long_mxf_confirm
            and long_mtx_bvav_ok
            and long_strength_ok
            and long_extension_ok
            and _is_multitimeframe_confirmed("bull", confirmation_rows)
        ):
            strength = "strong" if curr_bbr >= 0.8 else "normal"
            note = (
                f"多單進場：價格 {curr_close}，週期={timeframe}分，強度={strength}，"
                f"TT短線={curr_tt_short}，TT長線={curr_tt_long}，訊號={curr_mxf_row.get('signal', '')}，"
                f"趨勢={curr_mxf_row.get('trend', '')}，mtx_bvav={curr_mxf_row.get('mtx_bvav', '')}，"
                f"mtx_bvav_avg={curr_mxf_row.get('mtx_bvav_avg', '')}，BBR={curr_bbr}，"
                f"確認週期={','.join(TT_MXF_DRAFT_CONFIRM_TIMEFRAMES)}分，"
                f"動態停損={stop_loss_points}，動態停利={take_profit_points}，估計成本={TT_MXF_DRAFT_ROUND_TRIP_COST_POINTS}"
            )
            _mark_pending(state, "enter", "bull")
            _mark_entry_signal(state, signal_time)
            _save_state(state)
            _trigger_entry("bull", curr_close, "trend long", curr_mxf_row, curr_bbr, timeframe, curr_tt_short, curr_tt_long, note=note)
            return True

        if (
            prev_close_below_tt
            and curr_close_below_tt
            and short_mxf_confirm
            and short_mtx_bvav_ok
            and short_strength_ok
            and short_extension_ok
            and _is_multitimeframe_confirmed("bear", confirmation_rows)
        ):
            strength = "strong" if curr_bbr <= 0.2 else "normal"
            note = (
                f"空單進場：價格 {curr_close}，週期={timeframe}分，強度={strength}，"
                f"TT短線={curr_tt_short}，TT長線={curr_tt_long}，訊號={curr_mxf_row.get('signal', '')}，"
                f"趨勢={curr_mxf_row.get('trend', '')}，mtx_bvav={curr_mxf_row.get('mtx_bvav', '')}，"
                f"mtx_bvav_avg={curr_mxf_row.get('mtx_bvav_avg', '')}，BBR={curr_bbr}，"
                f"確認週期={','.join(TT_MXF_DRAFT_CONFIRM_TIMEFRAMES)}分，"
                f"動態停損={stop_loss_points}，動態停利={take_profit_points}，估計成本={TT_MXF_DRAFT_ROUND_TRIP_COST_POINTS}"
            )
            _mark_pending(state, "enter", "bear")
            _mark_entry_signal(state, signal_time)
            _save_state(state)
            _trigger_entry("bear", curr_close, "trend short", curr_mxf_row, curr_bbr, timeframe, curr_tt_short, curr_tt_long, note=note)
            return True

        _save_state(state)
        return False
