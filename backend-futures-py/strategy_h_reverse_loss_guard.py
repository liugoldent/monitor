"""策略名稱：H 175 點遠月鎖損。

用途：產生遠月帳號鎖損通知；webhook 只寫 CSV/Discord 紀錄，不自動送單。

進場規則：
- H 最新持倉單口浮虧達 175 點。
- 每筆 H 持倉都獨立判斷。
- 同一筆 H 持倉最多只進一次遠月鎖損。
- 遠月鎖損單同 H 口數反向進場。

出場規則：
- 不做浮盈出場。
- H 主單出場、反向或換倉時，遠月鎖損單同步出場。
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
STATE_PATH = STRATEGY_STATE_DIR / "h_reverse_loss_guard_state.json"
ALERT_PATH = STRATEGY_ALERT_DIR / "h_reverse_loss_guard_alert.csv"

LOSS_TRIGGER_POINTS = 175.0
DEFAULT_GUARD_QUANTITY = 1

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
    "consecutive_loss_count",
    "reason",
]

LOCK = RLock()
send_reverse_guard_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))


def _coerce_quantity(value: object) -> int:
    try:
        return max(DEFAULT_GUARD_QUANTITY, int(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_GUARD_QUANTITY


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
        action = str(row.get("action") or "").strip().lower()
        side = str(row.get("side") or "").strip().lower()
        price = to_float(row.get("price"))
        if action == "enter" and side in {"bull", "bear"} and price is not None:
            current = {
                "timestamp": row.get("timestamp", ""),
                "side": side,
                "price": price,
                "quantity": _coerce_quantity(row.get("quantity")),
            }
        elif action == "exiting":
            current = None
    return current


def _iter_exiting_pnls() -> list[float]:
    pnls: list[float] = []
    for row in _read_csv_rows(H_TRADE_CSV_PATH):
        if str(row.get("action") or "").strip().lower() != "exiting":
            continue
        value = to_float(row.get("pnl"))
        if value is not None:
            pnls.append(value)
    return pnls


def _consecutive_loss_count() -> int:
    count = 0
    for pnl in reversed(_iter_exiting_pnls()):
        if pnl < 0:
            count += 1
            continue
        break
    return count


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


def _points(side: str, entry_price: float, close: float) -> float:
    return close - entry_price if side == "bull" else entry_price - close


def _position_quantity(position: dict | None) -> int:
    if not position:
        return DEFAULT_GUARD_QUANTITY
    h_quantity = _coerce_quantity(position.get("quantity", DEFAULT_GUARD_QUANTITY))
    return h_quantity


def _guard_quantity(position: dict | None) -> int:
    return _position_quantity(position)


def _build_signal(
    *,
    action: str,
    side: str,
    quantity: int,
    close: float | None,
    position: dict | None,
    h_unrealized: float | None,
    loss_count: int,
    reason: str,
    entry_price: object = "",
    guard_points: object = "",
) -> dict:
    h_side = str(position.get("side") or "") if position else ""
    h_entry = to_float(position.get("price")) if position else None
    return {
        "timestamp": now_str(),
        "action": action,
        "side": side,
        "quantity": quantity,
        "entry_price": entry_price,
        "close": "" if close is None else close,
        "guard_points": guard_points,
        "h_position_timestamp": position.get("timestamp", "") if position else "",
        "h_side": h_side,
        "h_entry_price": "" if h_entry is None else h_entry,
        "h_unrealized_points": "" if h_unrealized is None else round(h_unrealized, 1),
        "consecutive_loss_count": loss_count,
        "reason": reason,
    }


def _send_signal_message(signal: dict) -> None:
    action = str(signal.get("action") or "")
    action_text = {"enter": "進場", "exit": "出場"}.get(action, action)
    message = (
        "策略=H 175點遠月鎖損(strategy_h_reverse_loss_guard，僅通知不送單)；"
        f"鎖損單{action_text}：{_side_text(str(signal.get('side') or ''))} "
        f"{signal.get('quantity', DEFAULT_GUARD_QUANTITY)}口，"
        f"價格={signal.get('close', signal.get('entry_price', ''))}，"
        f"鎖損單浮動={signal.get('guard_points', '')}點，"
        f"H方向={_side_text(str(signal.get('h_side') or ''))}，"
        f"H進場={signal.get('h_entry_price', '')}，"
        f"H浮動={signal.get('h_unrealized_points', '')}點，"
        f"H連輸={signal.get('consecutive_loss_count', '')}，"
        f"原因={signal.get('reason', '')}"
    )
    send_reverse_guard_message(message)


def evaluate_h_reverse_loss_guard() -> dict | None:
    """Evaluate the 175-point far-month loss lock and return an enter/exit signal."""
    with LOCK:
        latest_1m = _latest_csv_row(WEBHOOK_1M_CSV_PATH)
        close = to_float(latest_1m.get("Close")) if latest_1m else None
        position = _latest_h_position()
        h_key = _position_key(position)
        loss_count = _consecutive_loss_count()
        state = _read_state()
        active = state.get("active_guard")

        if active:
            active_h_key = str(active.get("h_position_key") or "")
            h_changed = active_h_key != h_key
            if not h_changed:
                return None

            guard_side = str(active.get("side") or "")
            entry_price = to_float(active.get("entry_price"))
            h_side = str(position.get("side") or "") if position else ""
            h_entry = to_float(position.get("price")) if position else None
            h_unrealized = (
                _points(h_side, h_entry, close)
                if close is not None and h_side in {"bull", "bear"} and h_entry is not None
                else None
            )
            guard_points = (
                _points(guard_side, entry_price, close)
                if close is not None and guard_side in {"bull", "bear"} and entry_price is not None
                else ""
            )
            signal = _build_signal(
                action="exit",
                side=guard_side,
                quantity=_coerce_quantity(active.get("quantity", DEFAULT_GUARD_QUANTITY)),
                close=close,
                position=position,
                h_unrealized=h_unrealized,
                loss_count=loss_count,
                reason="H 主單已出場/反向/換倉，175點遠月鎖損同步出場",
                entry_price="" if entry_price is None else entry_price,
                guard_points="" if guard_points == "" else round(float(guard_points), 1),
            )
            state["active_guard"] = None
            state["last_exit"] = signal
            _write_state(state)
            _append_alert(signal)
            _send_signal_message(signal)
            return signal

        if position is None or close is None:
            return None

        h_side = str(position.get("side") or "")
        h_entry = to_float(position.get("price"))
        if h_side not in {"bull", "bear"} or h_entry is None:
            return None

        h_unrealized = _points(h_side, h_entry, close)
        tracked_h_key = str(state.get("tracked_h_position_key") or "")
        if tracked_h_key != h_key:
            state["tracked_h_position_key"] = h_key
            state["last_h_unrealized_points"] = round(h_unrealized, 2)
            if state.get("skipped_stale_h_key") == h_key:
                _write_state(state)
                return None
            if h_unrealized <= -LOSS_TRIGGER_POINTS:
                previous_h_unrealized = 0.0
                state["skipped_stale_h_key"] = ""
                state["stale_skip_reason"] = ""
            else:
                state["skipped_stale_h_key"] = ""
                state["stale_skip_reason"] = ""
                _write_state(state)
                return None
        else:
            previous_h_unrealized = to_float(state.get("last_h_unrealized_points"))
            state["last_h_unrealized_points"] = round(h_unrealized, 2)
            if state.get("skipped_stale_h_key") == h_key:
                _write_state(state)
                return None
            if previous_h_unrealized is None or previous_h_unrealized <= -LOSS_TRIGGER_POINTS:
                _write_state(state)
                return None
            if h_unrealized > -LOSS_TRIGGER_POINTS:
                _write_state(state)
                return None
        if state.get("last_entry_h_key") == h_key:
            _write_state(state)
            return None

        guard_side = _reverse_side(h_side)
        guard_quantity = _guard_quantity(position)
        reason = (
            f"H 浮虧達 {abs(h_unrealized):.1f} 點，超過 {LOSS_TRIGGER_POINTS:g} 點；"
            f"每筆 H 持倉獨立鎖損，同 H 口數 {guard_quantity} 口遠月反向進場"
        )
        signal = _build_signal(
            action="enter",
            side=guard_side,
            quantity=guard_quantity,
            close=close,
            position=position,
            h_unrealized=h_unrealized,
            loss_count=loss_count,
            reason=reason,
            entry_price=close,
            guard_points=0,
        )
        state["active_guard"] = {
            "side": guard_side,
            "quantity": guard_quantity,
            "entry_time": signal["timestamp"],
            "entry_price": close,
            "h_position_key": h_key,
            "h_position_timestamp": position.get("timestamp", ""),
            "h_side": h_side,
            "h_entry_price": h_entry,
            "h_unrealized_points": round(h_unrealized, 1),
        }
        state["last_entry_h_key"] = h_key
        state["last_signal"] = signal
        _write_state(state)
        _append_alert(signal)
        _send_signal_message(signal)
        return signal


def clear_active_guard_after_order_failure(signal: dict) -> None:
    """Clear a just-created guard state when the broker order was not placed."""
    action = str(signal.get("action") or "").strip().lower()
    if action != "enter":
        return

    signal_key = (
        f"{signal.get('h_position_timestamp', '')}|"
        f"{signal.get('h_side', '')}|"
        f"{signal.get('h_entry_price', '')}"
    )
    with LOCK:
        state = _read_state()
        active = state.get("active_guard")
        if not active or str(active.get("h_position_key") or "") != signal_key:
            return

        state["active_guard"] = None
        state["last_entry_h_key"] = ""
        state["last_order_failed"] = {
            "timestamp": now_str(),
            "signal": signal,
            "reason": "broker order was not placed; cleared active guard state for retry",
        }
        _write_state(state)


if __name__ == "__main__":
    result = evaluate_h_reverse_loss_guard()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
