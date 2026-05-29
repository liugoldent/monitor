"""H profit-retrace reverse guard strategy.

This strategy emits Discord observation signals only. It does not place orders
or block other second-account strategies.

Rules:
- Track the active H position's best unrealized close-to-close profit.
- If H has reached a large open profit, then gives back a large share of it,
  and `mxf_value.csv` confirms pressure against H, enter one reverse guard.
- Exit the guard when H changes/exits, or if H recovers to a new favorable
  close beyond the best H profit seen when the guard entered.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from threading import RLock

from strategy_common import build_shortcycle_send_discord_message, now_str, to_float


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
H_TRADE_CSV_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_CSV_PATH = TV_DOC_DIR / "mxf_value.csv"
WEBHOOK_1M_CSV_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
STATE_PATH = TV_DOC_DIR / "h_profit_retrace_guard_state.json"
ALERT_PATH = TV_DOC_DIR / "h_profit_retrace_guard_alert.csv"

PROFIT_TRIGGER_POINTS = 750.0
GIVEBACK_POINTS = 600.0
GIVEBACK_RATIO = 0.50
MTX_BVAV_AVG_THRESHOLD = 500.0
REQUIRE_MXF_SIGNAL = True
RECOVERY_STOP_BUFFER_POINTS = 0.0
GUARD_STOP_LOSS_POINTS = 300.0
GUARD_QUANTITY = 1

ALERT_HEADER = [
    "timestamp",
    "action",
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
    "mtx_bvav_avg",
    "mxf_signal",
    "mxf_trend",
    "reason",
]

LOCK = RLock()
send_profit_retrace_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))


def _read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    state["updated_at"] = now_str()
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
                side = str(row.get("side") or "").strip().lower()
                if price is None or side not in {"bull", "bear"}:
                    continue
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
    if side == "bull":
        return close - entry_price
    return entry_price - close


def _guard_points(side: str, entry_price: float, close: float) -> float:
    return close - entry_price if side == "bull" else entry_price - close


def _mxf_opposes_h(h_side: str, mxf: dict) -> bool:
    avg = to_float(mxf.get("mtx_bvav_avg"))
    signal = str(mxf.get("signal") or "").strip().lower()
    trend = str(mxf.get("trend") or "").strip().lower()
    if avg is None:
        return False

    if h_side == "bull":
        avg_ok = avg <= -MTX_BVAV_AVG_THRESHOLD
        signal_ok = signal == "bear" and trend == "death"
    else:
        avg_ok = avg >= MTX_BVAV_AVG_THRESHOLD
        signal_ok = signal == "bull" and trend == "gold"
    return avg_ok and (signal_ok if REQUIRE_MXF_SIGNAL else True)


def _send_signal_message(signal: dict) -> None:
    action = str(signal.get("action") or "")
    side = str(signal.get("side") or "")
    action_text = "進場" if action == "enter" else "出場"
    message = (
        "策略=H浮盈回吐反向保護(strategy_h_profit_retrace_guard，僅通知不下單)；"
        f"保護單{action_text}：{_side_text(side)} {signal.get('quantity', GUARD_QUANTITY)}口，"
        f"價格={signal.get('close', signal.get('entry_price', ''))}，"
        f"H方向={_side_text(str(signal.get('h_side') or ''))}，"
        f"H進場={signal.get('h_entry_price', '')}，"
        f"H浮動={signal.get('h_unrealized_points', '')}點，"
        f"H最高浮盈={signal.get('max_h_unrealized_points', '')}點，"
        f"回吐={signal.get('giveback_points', '')}點，"
        f"mtx_bvav_avg={signal.get('mtx_bvav_avg', '')}，"
        f"原因={signal.get('reason', '')}"
    )
    send_profit_retrace_message(message)


def _reset_state_for_position(position: dict, h_key: str, h_unrealized: float, close: float) -> dict:
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


def evaluate_h_profit_retrace_guard() -> dict | None:
    """Evaluate the profit-retrace guard and return an enter/exit signal if any."""
    with LOCK:
        position = _latest_h_position()
        latest_1m = _latest_csv_row(WEBHOOK_1M_CSV_PATH)
        mxf = _latest_csv_row(MXF_VALUE_CSV_PATH) or {}
        close = to_float(latest_1m.get("Close")) if latest_1m else None
        if not position or close is None:
            return None

        h_side = str(position["side"])
        h_entry = float(position["price"])
        h_key = _position_key(position)
        h_unrealized = _unrealized_points(h_side, h_entry, close)

        state = _read_state()
        active = state.get("active_guard")
        if state.get("h_position_key") != h_key and active:
            guard_side = str(active.get("side") or "")
            reason = "H 主單已出場/反向/換倉，保護單跟隨出場"
            signal = {
                "timestamp": now_str(),
                "action": "exit",
                "side": guard_side,
                "quantity": active.get("quantity", GUARD_QUANTITY),
                "entry_price": active.get("entry_price"),
                "close": close,
                "h_position_timestamp": position.get("timestamp", ""),
                "h_side": h_side,
                "h_entry_price": h_entry,
                "h_unrealized_points": round(h_unrealized, 2),
                "max_h_unrealized_points": state.get("max_h_unrealized_points", ""),
                "giveback_points": "",
                "mtx_bvav_avg": to_float(mxf.get("mtx_bvav_avg")),
                "mxf_signal": str(mxf.get("signal") or "").strip().lower(),
                "mxf_trend": str(mxf.get("trend") or "").strip().lower(),
                "reason": reason,
            }
            state = _reset_state_for_position(position, h_key, h_unrealized, close)
            _write_state(state)
            _append_alert([signal.get(key, "") for key in ALERT_HEADER])
            _send_signal_message(signal)
            return signal

        if state.get("h_position_key") != h_key:
            state = _reset_state_for_position(position, h_key, h_unrealized, close)

        max_unrealized = max(float(state.get("max_h_unrealized_points", 0) or 0), h_unrealized)
        if max_unrealized > float(state.get("max_h_unrealized_points", 0) or 0):
            state["max_h_unrealized_points"] = round(max_unrealized, 2)
            state["max_h_unrealized_price"] = close

        giveback = max(0.0, max_unrealized - h_unrealized)
        active = state.get("active_guard")

        if active:
            guard_side = str(active.get("side") or "")
            guard_entry = to_float(active.get("entry_price"))
            entry_max = to_float(active.get("entry_max_h_unrealized_points")) or 0.0
            recovered = h_unrealized >= entry_max + RECOVERY_STOP_BUFFER_POINTS
            stopped = (
                guard_entry is not None
                and _guard_points(guard_side, guard_entry, close) <= -GUARD_STOP_LOSS_POINTS
            )
            if not recovered and not stopped:
                _write_state(state)
                return None

            reason = (
                f"保護單虧損達 {GUARD_STOP_LOSS_POINTS:g} 點停損"
                if stopped
                else "H 浮盈重新創高，保護單退場"
            )
            signal = {
                "timestamp": now_str(),
                "action": "exit",
                "side": guard_side,
                "quantity": active.get("quantity", GUARD_QUANTITY),
                "entry_price": active.get("entry_price"),
                "close": close,
                "h_position_timestamp": position.get("timestamp", ""),
                "h_side": h_side,
                "h_entry_price": h_entry,
                "h_unrealized_points": round(h_unrealized, 2),
                "max_h_unrealized_points": round(max_unrealized, 2),
                "giveback_points": round(giveback, 2),
                "mtx_bvav_avg": to_float(mxf.get("mtx_bvav_avg")),
                "mxf_signal": str(mxf.get("signal") or "").strip().lower(),
                "mxf_trend": str(mxf.get("trend") or "").strip().lower(),
                "reason": reason,
            }
            state["active_guard"] = None
            _write_state(state)
            _append_alert([signal.get(key, "") for key in ALERT_HEADER])
            _send_signal_message(signal)
            return signal

        if max_unrealized < PROFIT_TRIGGER_POINTS:
            _write_state(state)
            return None
        if giveback < GIVEBACK_POINTS:
            _write_state(state)
            return None
        if max_unrealized <= 0 or giveback / max_unrealized < GIVEBACK_RATIO:
            _write_state(state)
            return None
        if not _mxf_opposes_h(h_side, mxf):
            _write_state(state)
            return None

        guard_side = _reverse_side(h_side)
        reason = (
            f"H 最高浮盈 {max_unrealized:.1f} 點後回吐 {giveback:.1f} 點，"
            "且 mtx_bvav_avg/signal 轉向 H 反向，進反向保護"
        )
        signal = {
            "timestamp": now_str(),
            "action": "enter",
            "side": guard_side,
            "quantity": GUARD_QUANTITY,
            "entry_price": close,
            "close": close,
            "h_position_timestamp": position.get("timestamp", ""),
            "h_side": h_side,
            "h_entry_price": h_entry,
            "h_unrealized_points": round(h_unrealized, 2),
            "max_h_unrealized_points": round(max_unrealized, 2),
            "giveback_points": round(giveback, 2),
            "mtx_bvav_avg": to_float(mxf.get("mtx_bvav_avg")),
            "mxf_signal": str(mxf.get("signal") or "").strip().lower(),
            "mxf_trend": str(mxf.get("trend") or "").strip().lower(),
            "reason": reason,
        }
        state["active_guard"] = {
            "side": guard_side,
            "quantity": GUARD_QUANTITY,
            "entry_price": close,
            "h_position_key": h_key,
            "entry_max_h_unrealized_points": round(max_unrealized, 2),
            "entered_at": signal["timestamp"],
        }
        _write_state(state)
        _append_alert([signal.get(key, "") for key in ALERT_HEADER])
        _send_signal_message(signal)
        return signal
