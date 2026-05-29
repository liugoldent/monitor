"""H/MXF aligned follow observation strategy.

This strategy emits Discord/CSV observation signals only. It does not place
orders.

Live rule:
- Morning H entry with `mtx_bvav_avg` supporting the H direction.
- Follow H with one contract.
- Exit when H exits/changes, or when the follow position moves 250 points
  against the entry.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from threading import RLock

from strategy_common import build_shortcycle_send_discord_message, now_str, to_float


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
STRATEGY_STATE_DIR = TV_DOC_DIR / "strategy_state"
STRATEGY_ALERT_DIR = TV_DOC_DIR / "strategy_alerts"
H_TRADE_CSV_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_CSV_PATH = TV_DOC_DIR / "mxf_value.csv"
WEBHOOK_1M_CSV_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
STATE_PATH = STRATEGY_STATE_DIR / "h_mxf_aligned_follow_state.json"
ALERT_PATH = STRATEGY_ALERT_DIR / "h_mxf_aligned_follow_alert.csv"

FOLLOW_QUANTITY = 1
STOP_LOSS_POINTS = 250.0

ALERT_HEADER = [
    "timestamp",
    "action",
    "side",
    "quantity",
    "entry_price",
    "close",
    "h_position_timestamp",
    "h_entry_price",
    "h_unrealized_points",
    "mxf_time",
    "session",
    "tx_bvav",
    "mtx_bvav",
    "mtx_bvav_avg",
    "tx_support",
    "mtx_support",
    "avg_support",
    "mxf_signal",
    "mxf_trend",
    "rule",
    "follow_points",
    "max_follow_points",
    "reason",
]

LOCK = RLock()
send_mxf_aligned_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))


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


def _parse_dt(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _latest_h_position() -> dict | None:
    current: dict | None = None
    for row in _read_csv_rows(H_TRADE_CSV_PATH):
        action = str(row.get("action") or "").strip()
        timestamp = _parse_dt(row.get("timestamp"))
        side = str(row.get("side") or "").strip().lower()
        price = to_float(row.get("price"))
        if action == "enter" and timestamp is not None and side in {"bull", "bear"} and price is not None:
            current = {"timestamp": timestamp, "side": side, "price": price}
        elif action == "exiting":
            current = None
    return current


def _latest_1m_row() -> dict[str, str] | None:
    rows = _read_csv_rows(WEBHOOK_1M_CSV_PATH)
    return rows[-1] if rows else None


def _position_key(position: dict | None) -> str:
    if not position:
        return ""
    timestamp = position.get("timestamp")
    if isinstance(timestamp, datetime):
        timestamp = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    return f"{timestamp}|{position.get('side', '')}|{position.get('price', '')}"


def _mxf_at_or_before(value: datetime) -> dict[str, str] | None:
    target = value.replace(second=0, microsecond=0)
    latest: dict[str, str] | None = None
    latest_time: datetime | None = None
    for row in _read_csv_rows(MXF_VALUE_CSV_PATH):
        row_time = _parse_dt(row.get("time"))
        if row_time is None:
            continue
        row_minute = row_time.replace(second=0, microsecond=0)
        if row_minute <= target and (latest_time is None or row_minute >= latest_time):
            latest = row
            latest_time = row_minute
    return latest


def _session_name(value: datetime) -> str:
    minute = value.hour * 60 + value.minute
    if 8 * 60 + 45 <= minute <= 13 * 60 + 45:
        return "morning"
    if minute >= 15 * 60 or minute < 5 * 60:
        return "night"
    return "other"


def _support_value(side: str, value: float | None) -> float | None:
    if value is None:
        return None
    return value if side == "bull" else -value


def _side_text(side: str) -> str:
    if side == "bull":
        return "多單"
    if side == "bear":
        return "空單"
    return side


def _points(side: str, entry_price: float, close: float) -> float:
    return close - entry_price if side == "bull" else entry_price - close


def _stop_hit(side: str, entry_price: float, latest_1m: dict[str, str]) -> bool:
    high = to_float(latest_1m.get("High"))
    low = to_float(latest_1m.get("Low"))
    if side == "bull":
        return low is not None and entry_price - low >= STOP_LOSS_POINTS
    return high is not None and high - entry_price >= STOP_LOSS_POINTS


def _build_signal(
    *,
    action: str,
    position: dict | None,
    mxf: dict[str, str],
    rule: str,
    reason: str,
    side: str,
    close: float,
    entry_price: object = "",
    follow_points: object = "",
    max_follow_points: object = "",
) -> dict:
    side = str(position["side"])
    h_time = position["timestamp"]
    h_entry = to_float(position.get("price"))
    h_unrealized = (
        _points(side, h_entry, close)
        if h_entry is not None and side in {"bull", "bear"}
        else ""
    )
    tx_bvav = to_float(mxf.get("tx_bvav"))
    mtx_bvav = to_float(mxf.get("mtx_bvav"))
    avg = to_float(mxf.get("mtx_bvav_avg"))
    return {
        "timestamp": now_str(),
        "action": action,
        "side": side,
        "quantity": FOLLOW_QUANTITY,
        "entry_price": entry_price,
        "close": close,
        "h_position_timestamp": h_time.strftime("%Y-%m-%d %H:%M:%S"),
        "h_entry_price": position["price"],
        "h_unrealized_points": "" if h_unrealized == "" else round(float(h_unrealized), 1),
        "mxf_time": mxf.get("time", ""),
        "session": _session_name(h_time),
        "tx_bvav": "" if tx_bvav is None else round(tx_bvav, 2),
        "mtx_bvav": "" if mtx_bvav is None else round(mtx_bvav, 2),
        "mtx_bvav_avg": "" if avg is None else round(avg, 2),
        "tx_support": "" if tx_bvav is None else round(_support_value(side, tx_bvav), 2),
        "mtx_support": "" if mtx_bvav is None else round(_support_value(side, mtx_bvav), 2),
        "avg_support": "" if avg is None else round(_support_value(side, avg), 2),
        "mxf_signal": str(mxf.get("signal") or "").strip().lower(),
        "mxf_trend": str(mxf.get("trend") or "").strip().lower(),
        "rule": rule,
        "follow_points": follow_points,
        "max_follow_points": max_follow_points,
        "reason": reason,
    }


def _send_signal_message(signal: dict) -> None:
    action_text = "觀察進場" if signal.get("action") == "enter" else "觀察出場"
    message = (
        "策略=H/MXF順勢觀察(strategy_h_mxf_aligned_follow，僅通知不下單)；"
        f"{action_text}：{_side_text(str(signal.get('side') or ''))} "
        f"{signal.get('quantity', FOLLOW_QUANTITY)}口，"
        f"價格={signal.get('close', signal.get('entry_price', ''))}，"
        f"H進場={signal.get('h_entry_price', '')}，"
        f"H時間={signal.get('h_position_timestamp', '')}，"
        f"H浮動={signal.get('h_unrealized_points', '')}點，"
        f"時段={signal.get('session', '')}，"
        f"MXF時間={signal.get('mxf_time', '')}，"
        f"mtx_bvav={signal.get('mtx_bvav', '')}，"
        f"mtx_bvav_avg={signal.get('mtx_bvav_avg', '')}，"
        f"avg_support={signal.get('avg_support', '')}，"
        f"跟單浮動={signal.get('follow_points', '')}點，"
        f"規則={signal.get('rule', '')}，"
        f"原因={signal.get('reason', '')}"
    )
    send_mxf_aligned_message(message)


def evaluate_h_mxf_aligned_follow() -> dict | None:
    """Evaluate H/MXF aligned follow and return an enter/exit signal."""
    with LOCK:
        latest_1m = _latest_1m_row()
        close = to_float(latest_1m.get("Close")) if latest_1m else None
        if latest_1m is None or close is None:
            return None

        position = _latest_h_position()
        h_key = _position_key(position)
        state = _read_state()
        active = state.get("active_follow")

        if active:
            side = str(active.get("side") or "")
            entry_price = to_float(active.get("entry_price"))
            if side not in {"bull", "bear"} or entry_price is None:
                state["active_follow"] = None
                _write_state(state)
                return None

            follow_points = _points(side, entry_price, close)
            max_follow_points = max(float(active.get("max_follow_points", 0) or 0), follow_points)
            active["max_follow_points"] = round(max_follow_points, 2)
            state["active_follow"] = active
            h_changed = str(active.get("h_position_key") or "") != h_key
            stopped = _stop_hit(side, entry_price, latest_1m)
            if not h_changed and not stopped:
                _write_state(state)
                return None

            reason = "H 主單已出場/反向/換倉，H/MXF 順勢跟單同步出場"
            if stopped:
                reason = f"H/MXF 順勢跟單逆行達 {STOP_LOSS_POINTS:g} 點停損"

            mxf = _mxf_at_or_before(position["timestamp"]) if position else {}
            signal = _build_signal(
                action="exit",
                position=position or {
                    "timestamp": _parse_dt(active.get("h_position_timestamp")) or datetime.now(),
                    "side": side,
                    "price": active.get("h_entry_price", ""),
                },
                mxf=mxf or {},
                rule=str(active.get("rule") or "morning_avg_supports_h"),
                reason=reason,
                side=side,
                close=close,
                entry_price=entry_price,
                follow_points=round(follow_points, 1),
                max_follow_points=round(max_follow_points, 1),
            )
            state["active_follow"] = None
            state["last_exit"] = signal
            _write_state(state)
            _append_alert(signal)
            _send_signal_message(signal)
            return signal

        if not position:
            return None

        if state.get("last_checked_h_key") == h_key:
            return None

        mxf = _mxf_at_or_before(position["timestamp"])
        if mxf is None:
            return None

        side = str(position["side"])
        session = _session_name(position["timestamp"])
        avg_support = _support_value(side, to_float(mxf.get("mtx_bvav_avg")))
        if session == "morning" and avg_support is not None and avg_support > 0:
            rule = "morning_avg_supports_h_stop_250"
            reason = (
                "早盤 H 新倉且 mtx_bvav_avg 順著 H，第二帳號同向跟單；"
                f"逆行 {STOP_LOSS_POINTS:g} 點停損，H 出場同步出場"
            )
            signal = _build_signal(
                action="enter",
                position=position,
                mxf=mxf,
                rule=rule,
                reason=reason,
                side=side,
                close=close,
                entry_price=close,
                follow_points=0,
                max_follow_points=0,
            )
            state["active_follow"] = {
                "side": side,
                "quantity": FOLLOW_QUANTITY,
                "entry_price": close,
                "entry_time": signal["timestamp"],
                "h_position_key": h_key,
                "h_position_timestamp": position["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "h_entry_price": position["price"],
                "rule": rule,
                "max_follow_points": 0,
            }
            state["last_checked_h_key"] = h_key
            state["last_signal"] = signal
            _write_state(state)
            _append_alert(signal)
            _send_signal_message(signal)
            return signal

        state["last_checked_h_key"] = h_key
        state["last_reject"] = {
            "timestamp": now_str(),
            "h_position_key": h_key,
            "session": session,
            "side": side,
            "avg_support": avg_support,
            "reason": "MXF aligned follow rule not matched",
        }
        _write_state(state)
        return None


if __name__ == "__main__":
    result = evaluate_h_mxf_aligned_follow()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
