"""策略名稱：H 遠月同向一口護欄。

用途：
- 遠月帳號跟 H1 主策略同方向、固定 1 口進場。
- webhook 1 分 K 收到最新 close 後，檢查遠月同向單是否浮虧超過 175 點。
- 遠月浮虧超過 175 點，或 H 主單出場/反向/換倉時，產生遠月出場訊號。
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
WEBHOOK_1M_CSV_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
STATE_PATH = STRATEGY_STATE_DIR / "h_next_month_loss_guard_state.json"
ALERT_PATH = STRATEGY_ALERT_DIR / "h_next_month_loss_guard_alert.csv"

LOSS_GUARD_POINTS = 175.0
FIXED_GUARD_QUANTITY = 1

ALERT_HEADER = [
    "timestamp",
    "action",
    "side",
    "quantity",
    "entry_price",
    "close",
    "guard_points",
    "h_position_timestamp",
    "h_side",
    "h_entry_price",
    "h_unrealized_points",
    "reason",
]

LOCK = RLock()
send_guard_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))


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


def _latest_webhook_close() -> float | None:
    latest_1m = _latest_csv_row(WEBHOOK_1M_CSV_PATH)
    return to_float(latest_1m.get("Close")) if latest_1m else None


def _latest_h_position() -> dict | None:
    current: dict | None = None
    for row in _read_csv_rows(H_TRADE_CSV_PATH):
        action = str(row.get("action") or "").strip().lower()
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


def _points(side: str, entry_price: float, close: float) -> float:
    return close - entry_price if side == "bull" else entry_price - close


def _build_signal(
    *,
    action: str,
    side: str,
    close: float | None,
    position: dict | None,
    reason: str,
    entry_price: object = "",
    guard_points: object = "",
    h_unrealized: object = "",
) -> dict:
    h_side = str(position.get("side") or "") if position else ""
    h_entry = to_float(position.get("price")) if position else None
    return {
        "timestamp": now_str(),
        "strategy": "h_next_month_loss_guard",
        "strategy_label": "H遠月一口護欄",
        "action": action,
        "side": side,
        "quantity": FIXED_GUARD_QUANTITY,
        "entry_price": entry_price,
        "close": "" if close is None else close,
        "guard_points": guard_points,
        "h_position_timestamp": position.get("timestamp", "") if position else "",
        "h_side": h_side,
        "h_entry_price": "" if h_entry is None else h_entry,
        "h_unrealized_points": h_unrealized,
        "reason": reason,
    }


def _send_signal_message(signal: dict) -> None:
    action_text = {"enter": "進場", "exit": "出場"}.get(str(signal.get("action") or ""), "")
    message = (
        "策略=H遠月一口護欄(strategy_h_next_month_loss_guard)；"
        f"遠月{action_text}：{_side_text(str(signal.get('side') or ''))} "
        f"{FIXED_GUARD_QUANTITY}口，"
        f"進場={signal.get('entry_price', '')}，"
        f"最新={signal.get('close', '')}，"
        f"遠月浮動={signal.get('guard_points', '')}點，"
        f"H方向={_side_text(str(signal.get('h_side') or ''))}，"
        f"H進場={signal.get('h_entry_price', '')}，"
        f"H浮動={signal.get('h_unrealized_points', '')}點，"
        f"原因={signal.get('reason', '')}"
    )
    send_guard_message(message)


def mark_h_next_month_guard_entry(side: str, entry_price: float | None = None, reason: str = "H1 同步進場") -> None:
    side = str(side or "").strip().lower()
    if side not in {"bull", "bear"}:
        return

    with LOCK:
        position = _latest_h_position()
        close = _latest_webhook_close()
        if entry_price is None:
            entry_price = close
        if entry_price is None and position:
            entry_price = to_float(position.get("price"))
        if entry_price is None:
            return

        h_unrealized = ""
        if position and close is not None:
            h_side = str(position.get("side") or "")
            h_entry = to_float(position.get("price"))
            if h_side in {"bull", "bear"} and h_entry is not None:
                h_unrealized = round(_points(h_side, h_entry, close), 1)

        signal = _build_signal(
            action="enter",
            side=side,
            close=close,
            position=position,
            h_unrealized=h_unrealized,
            reason=reason,
            entry_price=entry_price,
            guard_points=0,
        )
        state = _read_state()
        state["active_guard"] = {
            "side": side,
            "quantity": FIXED_GUARD_QUANTITY,
            "entry_time": signal["timestamp"],
            "entry_price": entry_price,
            "h_position_key": _position_key(position),
            "h_position_timestamp": position.get("timestamp", "") if position else "",
            "h_side": position.get("side", "") if position else "",
            "h_entry_price": position.get("price", "") if position else "",
        }
        state["last_entry"] = signal
        state["last_signal"] = signal
        _write_state(state)
        _append_alert(signal)
        _send_signal_message(signal)


def mark_h_next_month_guard_exit(reason: str = "遠月護欄出場") -> None:
    with LOCK:
        state = _read_state()
        active = state.get("active_guard")
        if not active:
            return

        position = _latest_h_position()
        close = _latest_webhook_close()
        side = str(active.get("side") or "")
        entry_price = to_float(active.get("entry_price"))
        guard_points = ""
        if close is not None and side in {"bull", "bear"} and entry_price is not None:
            guard_points = round(_points(side, entry_price, close), 1)

        signal = _build_signal(
            action="exit",
            side=side,
            close=close,
            position=position,
            h_unrealized="",
            reason=reason,
            entry_price="" if entry_price is None else entry_price,
            guard_points=guard_points,
        )
        state["active_guard"] = None
        state["last_exit"] = signal
        state["last_signal"] = signal
        _write_state(state)
        _append_alert(signal)


def evaluate_h_next_month_loss_guard() -> dict | None:
    """Return an exit signal when the active far-month 1-lot guard must close."""
    with LOCK:
        state = _read_state()
        active = state.get("active_guard")
        if not active:
            return None

        close = _latest_webhook_close()
        position = _latest_h_position()
        h_key = _position_key(position)
        active_h_key = str(active.get("h_position_key") or "")
        side = str(active.get("side") or "")
        entry_price = to_float(active.get("entry_price"))

        h_unrealized = ""
        if position and close is not None:
            h_side = str(position.get("side") or "")
            h_entry = to_float(position.get("price"))
            if h_side in {"bull", "bear"} and h_entry is not None:
                h_unrealized = round(_points(h_side, h_entry, close), 1)

        if active_h_key != h_key:
            signal = _build_signal(
                action="exit",
                side=side,
                close=close,
                position=position,
                h_unrealized=h_unrealized,
                reason="H 主單已出場/反向/換倉，遠月一口同步出場",
                entry_price="" if entry_price is None else entry_price,
                guard_points="",
            )
            state["last_signal"] = signal
            _write_state(state)
            _append_alert(signal)
            _send_signal_message(signal)
            return signal

        if close is None or side not in {"bull", "bear"} or entry_price is None:
            return None

        guard_points = _points(side, entry_price, close)
        state["last_guard_points"] = round(guard_points, 2)
        state["last_checked_close"] = close
        _write_state(state)

        if guard_points > -LOSS_GUARD_POINTS:
            return None

        signal = _build_signal(
            action="exit",
            side=side,
            close=close,
            position=position,
            h_unrealized=h_unrealized,
            reason=f"遠月浮虧達 {abs(guard_points):.1f} 點，超過 {LOSS_GUARD_POINTS:g} 點護欄",
            entry_price=entry_price,
            guard_points=round(guard_points, 1),
        )
        state["last_signal"] = signal
        _write_state(state)
        _append_alert(signal)
        _send_signal_message(signal)
        return signal


if __name__ == "__main__":
    result = evaluate_h_next_month_loss_guard()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
