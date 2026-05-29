"""Unified H weakness reverse-guard strategy.

This replaces the separate reverse-guard and profit-retrace observation modules
in the webhook flow. It only emits Discord/CSV observation signals. It does not
place orders.

Trigger kinds:
- loss_guard: H is already losing and MXF/TF context confirms reverse pressure.
- profit_retrace: H had a large open profit, gave back enough of it, and MXF
  confirms reverse pressure.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from threading import RLock

from strategy_common import build_shortcycle_send_discord_message, now_str, to_float


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
STRATEGY_STATE_DIR = TV_DOC_DIR / "strategy_state"
STRATEGY_ALERT_DIR = TV_DOC_DIR / "strategy_alerts"
H_TRADE_CSV_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_CSV_PATH = TV_DOC_DIR / "mxf_value.csv"
WEBHOOK_CSV_BY_TF = {
    "1": TV_DOC_DIR / "webhook_data_1min.csv",
    "5": TV_DOC_DIR / "webhook_data_5min.csv",
    "10": TV_DOC_DIR / "webhook_data_10min.csv",
    "15": TV_DOC_DIR / "webhook_data_15min.csv",
}
STATE_PATH = STRATEGY_STATE_DIR / "h_weakness_reverse_guard_state.json"
ALERT_PATH = STRATEGY_ALERT_DIR / "h_weakness_reverse_guard_alert.csv"

LOSS_GUARD_MIN_H_LOSS_POINTS = 100.0
LOSS_GUARD_FLOW_CONFIRM_THRESHOLD = 150.0
LOSS_GUARD_TAKE_PROFIT_POINTS = 160.0
LOSS_GUARD_STOP_AVG_THRESHOLD = 0.0
LOSS_GUARD_REQUIRE_MXF_SIGNAL = True
LOSS_GUARD_MIN_TF_INVALID_SCORE = 0

PROFIT_TRIGGER_POINTS = 750.0
PROFIT_GIVEBACK_POINTS = 500.0
PROFIT_GIVEBACK_RATIO = 0.50
PROFIT_MXF_AVG_THRESHOLD = 500.0
PROFIT_REQUIRE_MXF_SIGNAL = True
PROFIT_RECOVERY_STOP_BUFFER_POINTS = 0.0
PROFIT_GUARD_STOP_LOSS_POINTS = 300.0

GUARD_QUANTITY = 1

ALERT_HEADER = [
    "timestamp",
    "action",
    "trigger_kind",
    "side",
    "quantity",
    "entry_price",
    "close",
    "h_position_timestamp",
    "h_side",
    "h_entry_price",
    "h_unrealized_points",
    "max_h_unrealized_points",
    "giveback_points",
    "mtx_bvav",
    "mtx_bvav_avg",
    "mxf_signal",
    "mxf_trend",
    "tf_score",
    "reason",
]

LOCK = RLock()
send_weakness_guard_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))


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


def _latest_csv_row(path: Path) -> dict[str, str] | None:
    rows = _read_csv_rows(path)
    return rows[-1] if rows else None


def _latest_h_position() -> dict | None:
    current: dict | None = None
    for row in _read_csv_rows(H_TRADE_CSV_PATH):
        action = str(row.get("action") or "").strip()
        side = str(row.get("side") or "").strip().lower()
        price = to_float(row.get("price"))
        if action == "enter" and side in {"bull", "bear"} and price is not None:
            current = {
                "timestamp": row.get("timestamp", ""),
                "side": side,
                "price": price,
            }
        elif action == "exiting":
            current = None
    return current


def _position_key(position: dict | None) -> str:
    if not position:
        return ""
    return f"{position.get('timestamp', '')}|{position.get('side', '')}|{position.get('price', '')}"


def _side_text(side: str) -> str:
    if side == "bull":
        return "多單"
    if side == "bear":
        return "空單"
    return side


def _reverse_side(side: str) -> str:
    return "bear" if side == "bull" else "bull"


def _unrealized_points(side: str, entry_price: float, close: float) -> float:
    return close - entry_price if side == "bull" else entry_price - close


def _guard_points(side: str, entry_price: float, close: float) -> float:
    return close - entry_price if side == "bull" else entry_price - close


def _pressure_against_h(h_side: str, value: float | None) -> float | None:
    if value is None:
        return None
    return -value if h_side == "bull" else value


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


def _loss_guard_mxf_opposes_h(h_side: str, mxf: dict) -> bool:
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
    flow_ok = all(float(pressure) >= LOSS_GUARD_FLOW_CONFIRM_THRESHOLD for pressure in pressures)
    return flow_ok and (signal_ok if LOSS_GUARD_REQUIRE_MXF_SIGNAL else True)


def _profit_retrace_mxf_opposes_h(h_side: str, mxf: dict) -> bool:
    avg = to_float(mxf.get("mtx_bvav_avg"))
    signal = str(mxf.get("signal") or "").strip().lower()
    trend = str(mxf.get("trend") or "").strip().lower()
    if avg is None:
        return False
    if h_side == "bull":
        avg_ok = avg <= -PROFIT_MXF_AVG_THRESHOLD
        signal_ok = signal == "bear" and trend == "death"
    else:
        avg_ok = avg >= PROFIT_MXF_AVG_THRESHOLD
        signal_ok = signal == "bull" and trend == "gold"
    return avg_ok and (signal_ok if PROFIT_REQUIRE_MXF_SIGNAL else True)


def _loss_guard_stop_hit(h_side: str, guard_side: str, entry_price: float | None, close: float, mxf: dict) -> tuple[bool, str]:
    if entry_price is not None and _guard_points(guard_side, entry_price, close) >= LOSS_GUARD_TAKE_PROFIT_POINTS:
        return True, f"loss_guard reached {LOSS_GUARD_TAKE_PROFIT_POINTS:g} points"

    avg = to_float(mxf.get("mtx_bvav_avg"))
    avg_pressure = _pressure_against_h(h_side, avg)
    if avg_pressure is not None and avg_pressure <= LOSS_GUARD_STOP_AVG_THRESHOLD:
        return True, "loss_guard mtx_bvav_avg pressure reverted"
    return False, ""


def _send_signal_message(signal: dict) -> None:
    action_text = "進場" if signal.get("action") == "enter" else "出場"
    trigger_text = {
        "loss_guard": "虧損轉弱",
        "profit_retrace": "浮盈回吐轉弱",
    }.get(str(signal.get("trigger_kind") or ""), str(signal.get("trigger_kind") or ""))
    message = (
        "策略=H轉弱反向護欄(strategy_h_weakness_reverse_guard，僅通知不下單)；"
        f"{trigger_text}保護單{action_text}：{_side_text(str(signal.get('side') or ''))} "
        f"{signal.get('quantity', GUARD_QUANTITY)}口，"
        f"價格={signal.get('close', signal.get('entry_price', ''))}，"
        f"H方向={_side_text(str(signal.get('h_side') or ''))}，"
        f"H進場={signal.get('h_entry_price', '')}，"
        f"H浮動={signal.get('h_unrealized_points', '')}點，"
        f"H最高浮盈={signal.get('max_h_unrealized_points', '')}點，"
        f"回吐={signal.get('giveback_points', '')}點，"
        f"mtx_bvav_avg={signal.get('mtx_bvav_avg', '')}，"
        f"tf_score={signal.get('tf_score', '')}，"
        f"原因={signal.get('reason', '')}"
    )
    send_weakness_guard_message(message)


def _build_signal(
    *,
    action: str,
    trigger_kind: str,
    guard_side: str,
    close: float,
    position: dict | None,
    h_unrealized: float | None,
    max_unrealized: float,
    giveback: float,
    mxf: dict,
    tf_score: int,
    reason: str,
    entry_price: object = "",
    quantity: int = GUARD_QUANTITY,
) -> dict:
    h_side = str(position.get("side") or "") if position else ""
    h_entry = to_float(position.get("price")) if position else None
    return {
        "timestamp": now_str(),
        "action": action,
        "trigger_kind": trigger_kind,
        "side": guard_side,
        "quantity": quantity,
        "entry_price": entry_price,
        "close": close,
        "h_position_timestamp": position.get("timestamp", "") if position else "",
        "h_side": h_side,
        "h_entry_price": "" if h_entry is None else h_entry,
        "h_unrealized_points": "" if h_unrealized is None else round(h_unrealized, 1),
        "max_h_unrealized_points": round(max_unrealized, 1),
        "giveback_points": round(giveback, 1),
        "mtx_bvav": to_float(mxf.get("mtx_bvav")),
        "mtx_bvav_avg": to_float(mxf.get("mtx_bvav_avg")),
        "mxf_signal": str(mxf.get("signal") or "").strip().lower(),
        "mxf_trend": str(mxf.get("trend") or "").strip().lower(),
        "tf_score": tf_score,
        "reason": reason,
    }


def _reset_for_position(position: dict, h_key: str, h_unrealized: float, close: float) -> dict:
    max_unrealized = max(0.0, h_unrealized)
    return {
        "h_position_key": h_key,
        "h_position_timestamp": position.get("timestamp", ""),
        "h_side": position.get("side", ""),
        "h_entry_price": position.get("price"),
        "max_h_unrealized_points": round(max_unrealized, 2),
        "max_h_unrealized_price": close if max_unrealized > 0 else position.get("price"),
        "active_guard": None,
    }


def evaluate_h_weakness_reverse_guard() -> dict | None:
    """Evaluate the unified H weakness reverse guard and return a signal."""
    with LOCK:
        latest_1m = _latest_csv_row(WEBHOOK_CSV_BY_TF["1"])
        mxf = _latest_csv_row(MXF_VALUE_CSV_PATH) or {}
        close = to_float(latest_1m.get("Close")) if latest_1m else None
        if close is None:
            return None

        position = _latest_h_position()
        h_key = _position_key(position)
        state = _read_state()
        active = state.get("active_guard")

        h_side = str(position.get("side") or "") if position else ""
        h_entry = to_float(position.get("price")) if position else None
        h_unrealized = (
            _unrealized_points(h_side, h_entry, close)
            if h_side in {"bull", "bear"} and h_entry is not None
            else None
        )
        max_unrealized = float(state.get("max_h_unrealized_points", 0) or 0)
        giveback = 0.0
        tf_rows = {tf: _latest_csv_row(path) for tf, path in WEBHOOK_CSV_BY_TF.items()}
        tf_score = sum(1 for row in tf_rows.values() if _is_tf_invalid(h_side, row)) if h_side else 0

        if active:
            guard_side = str(active.get("side") or "")
            trigger_kind = str(active.get("trigger_kind") or "loss_guard")
            guard_entry = to_float(active.get("entry_price"))
            h_changed = str(active.get("h_position_key") or "") != h_key
            if h_unrealized is not None:
                max_unrealized = max(max_unrealized, h_unrealized)
                giveback = max(0.0, max_unrealized - h_unrealized)

            if trigger_kind == "profit_retrace":
                entry_max = to_float(active.get("entry_max_h_unrealized_points")) or 0.0
                recovered = h_unrealized is not None and h_unrealized >= entry_max + PROFIT_RECOVERY_STOP_BUFFER_POINTS
                stopped = (
                    guard_entry is not None
                    and _guard_points(guard_side, guard_entry, close) <= -PROFIT_GUARD_STOP_LOSS_POINTS
                )
                should_exit = h_changed or recovered or stopped
                reason = "H 主單已出場/反向/換倉，轉弱護欄跟隨出場"
                if stopped:
                    reason = f"浮盈回吐護欄虧損達 {PROFIT_GUARD_STOP_LOSS_POINTS:g} 點停損"
                elif recovered:
                    reason = "H 浮盈重新創高，浮盈回吐護欄退場"
            else:
                stopped, stop_reason = _loss_guard_stop_hit(h_side, guard_side, guard_entry, close, mxf)
                should_exit = h_changed or stopped
                reason = "H 主單已出場/反向/換倉，轉弱護欄跟隨出場"
                if stopped:
                    reason = stop_reason

            if not should_exit:
                state["max_h_unrealized_points"] = round(max_unrealized, 2)
                _write_state(state)
                return None

            signal = _build_signal(
                action="exit",
                trigger_kind=trigger_kind,
                guard_side=guard_side,
                close=close,
                position=position,
                h_unrealized=h_unrealized,
                max_unrealized=max_unrealized,
                giveback=giveback,
                mxf=mxf,
                tf_score=tf_score,
                reason=reason,
                entry_price=active.get("entry_price", ""),
                quantity=int(active.get("quantity", GUARD_QUANTITY) or GUARD_QUANTITY),
            )
            state["active_guard"] = None
            state["last_exit"] = signal
            _write_state(state)
            _append_alert(signal)
            _send_signal_message(signal)
            return signal

        if position is None or h_unrealized is None:
            return None

        if state.get("h_position_key") != h_key:
            state = _reset_for_position(position, h_key, h_unrealized, close)

        max_unrealized = max(float(state.get("max_h_unrealized_points", 0) or 0), h_unrealized)
        state["max_h_unrealized_points"] = round(max_unrealized, 2)
        if max_unrealized == h_unrealized:
            state["max_h_unrealized_price"] = close
        giveback = max(0.0, max_unrealized - h_unrealized)

        trigger_kind = ""
        reason = ""
        profit_retrace_ok = (
            max_unrealized >= PROFIT_TRIGGER_POINTS
            and giveback >= PROFIT_GIVEBACK_POINTS
            and max_unrealized > 0
            and giveback / max_unrealized >= PROFIT_GIVEBACK_RATIO
            and _profit_retrace_mxf_opposes_h(h_side, mxf)
        )
        loss_guard_ok = (
            h_unrealized <= -LOSS_GUARD_MIN_H_LOSS_POINTS
            and tf_score >= LOSS_GUARD_MIN_TF_INVALID_SCORE
            and _loss_guard_mxf_opposes_h(h_side, mxf)
        )

        if profit_retrace_ok:
            trigger_kind = "profit_retrace"
            reason = (
                f"H 最高浮盈 {max_unrealized:.1f} 點後回吐 {giveback:.1f} 點，"
                "且 MXF 轉向 H 反向，進反向保護"
            )
        elif loss_guard_ok:
            trigger_kind = "loss_guard"
            reason = "H 已轉為虧損且 MXF/多週期確認 H 反向壓力，進反向保護"
        else:
            _write_state(state)
            return None

        guard_side = _reverse_side(h_side)
        signal = _build_signal(
            action="enter",
            trigger_kind=trigger_kind,
            guard_side=guard_side,
            close=close,
            position=position,
            h_unrealized=h_unrealized,
            max_unrealized=max_unrealized,
            giveback=giveback,
            mxf=mxf,
            tf_score=tf_score,
            reason=reason,
            entry_price=close,
        )
        state["active_guard"] = {
            "side": guard_side,
            "quantity": GUARD_QUANTITY,
            "entry_price": close,
            "entry_time": signal["timestamp"],
            "trigger_kind": trigger_kind,
            "h_position_key": h_key,
            "entry_max_h_unrealized_points": round(max_unrealized, 2),
        }
        state["last_signal"] = signal
        _write_state(state)
        _append_alert(signal)
        _send_signal_message(signal)
        return signal


if __name__ == "__main__":
    result = evaluate_h_weakness_reverse_guard()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
