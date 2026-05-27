"""H profit breakout add-on strategy.

This strategy emits add-on signals only. It does not place orders.
Rules:
- H bull must be at least +750 points before the first add-on long.
- H bear must be at least +750 points before the first add-on short.
- Bull add-on entry requires an MA_N200 upside breakout confirmed by two closes.
- Bear add-on entry requires an MA_P200 downside breakout confirmed by two closes.
- Add-on exits when price closes back through the same MA, or when H changes to
  the opposite direction / a new H position.
- When flat, H profit >= 750 points is enough to allow a fresh add-on entry.
- The add-on's own +750 point gate is reserved for future pyramiding logic; this
  signal strategy currently holds at most one add-on at a time.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from threading import RLock

from strategy_common import build_shortcycle_send_discord_message, now_str, read_last_n_rows, to_float


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
H_TRADE_CSV_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_CSV_PATH = TV_DOC_DIR / "mxf_value.csv"
WEBHOOK_1M_CSV_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
STATE_PATH = TV_DOC_DIR / "h_profit_breakout_add_state.json"
ALERT_PATH = TV_DOC_DIR / "h_profit_breakout_add_alert.csv"

PROFIT_GATE_POINTS = 750.0
CONFIRM_CLOSES = 2
ADD_QUANTITY = 1

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
    "add_unrealized_points",
    "ma_name",
    "ma_value",
    "reason",
]

LOCK = RLock()
send_profit_breakout_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))


def _read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_alert(row: list[object]) -> None:
    exists = ALERT_PATH.exists()
    with ALERT_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(ALERT_HEADER)
        writer.writerow(row)


def _latest_h_position() -> dict | None:
    if not H_TRADE_CSV_PATH.exists():
        return None

    current: dict | None = None
    with H_TRADE_CSV_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            action = row.get("action")
            if action == "enter":
                price = to_float(row.get("price"))
                if price is None:
                    continue
                current = {
                    "timestamp": row.get("timestamp", ""),
                    "side": row.get("side", ""),
                    "price": price,
                }
            elif action == "exiting":
                current = None
    return current


def _position_key(position: dict) -> str:
    return f"{position.get('timestamp', '')}|{position.get('side', '')}|{position.get('price', '')}"


def _side_text(side: str) -> str:
    if side == "bull":
        return "多單"
    if side == "bear":
        return "空單"
    return side


def _unrealized_points(side: str, entry_price: float, close: float) -> float:
    if side == "bull":
        return close - entry_price
    return entry_price - close


def _ma_name(side: str) -> str:
    return "MA_N200" if side == "bull" else "MA_P200"


def _ma_value(side: str, row: dict) -> float | None:
    return to_float(row.get(_ma_name(side)))


def _is_above_entry_ma(side: str, row: dict) -> bool | None:
    close = to_float(row.get("Close"))
    ma = _ma_value(side, row)
    if close is None or ma is None:
        return None
    if side == "bull":
        return close > ma
    return close < ma


def _is_stop_hit(side: str, row: dict) -> bool:
    close = to_float(row.get("Close"))
    ma = _ma_value(side, row)
    if close is None or ma is None:
        return False
    if side == "bull":
        return close < ma
    return close > ma


def _has_confirmed_breakout(side: str, rows: list[dict]) -> bool:
    if len(rows) < CONFIRM_CLOSES + 1:
        return False

    previous = _is_above_entry_ma(side, rows[-(CONFIRM_CLOSES + 1)])
    confirmed = [_is_above_entry_ma(side, row) for row in rows[-CONFIRM_CLOSES:]]
    return previous is False and all(value is True for value in confirmed)


def _reset_for_h_position(state: dict, h_key: str) -> dict:
    if state.get("h_position_key") == h_key:
        return state
    return {
        "h_position_key": h_key,
        "active_add": None,
        "last_add_reached_profit_gate": False,
        "updated_at": now_str(),
    }


def _send_signal_message(signal: dict) -> None:
    action = signal.get("action", "")
    side = str(signal.get("side") or "")
    action_text = "進場" if action == "enter" else "出場"
    message = (
        "策略=H獲利突破加碼(strategy_h_profit_breakout_add)；"
        f"加碼{action_text}：{_side_text(side)} {signal.get('quantity', ADD_QUANTITY)}口，"
        f"價格={signal.get('close', signal.get('entry_price', ''))}，"
        f"H方向={_side_text(str(signal.get('h_side') or ''))}，"
        f"H進場={signal.get('h_entry_price', '')}，"
        f"H浮動={signal.get('h_unrealized_points', '')}點，"
        f"{signal.get('ma_name', '')}={signal.get('ma_value', '')}，"
        f"原因={signal.get('reason', '')}"
    )
    send_profit_breakout_message(message)


def evaluate_h_profit_breakout_add() -> dict | None:
    """Evaluate the add-on strategy and return an enter/exit signal if any."""
    with LOCK:
        position = _latest_h_position()
        latest_rows = read_last_n_rows(str(WEBHOOK_1M_CSV_PATH), CONFIRM_CLOSES + 1)
        if len(latest_rows) < CONFIRM_CLOSES + 1:
            return None

        latest = latest_rows[-1]
        close = to_float(latest.get("Close"))
        if close is None:
            return None

        state = _read_state()
        active = state.get("active_add")

        if active:
            active_side = str(active.get("side") or "")
            if active_side not in {"bull", "bear"}:
                state["active_add"] = None
                _write_state(state)
                return None

            add_entry = to_float(active.get("entry_price"))
            if add_entry is None:
                state["active_add"] = None
                _write_state(state)
                return None

            active_h_key = str(active.get("h_position_key") or "")
            h_key = _position_key(position) if position else ""
            h_position_changed = active_h_key != h_key
            ma = _ma_value(active_side, latest)
            if ma is None:
                return None

            add_unrealized = _unrealized_points(active_side, add_entry, close)
            max_unrealized = max(float(active.get("max_unrealized_points", 0) or 0), add_unrealized)
            active["max_unrealized_points"] = round(max_unrealized, 2)
            state["active_add"] = active

            stop_hit = _is_stop_hit(active_side, latest)
            if not h_position_changed and not stop_hit:
                state["updated_at"] = now_str()
                _write_state(state)
                return None

            h_side = str(position.get("side") or "") if position else ""
            h_entry = to_float(position.get("price")) if position else None
            h_unrealized = (
                _unrealized_points(h_side, h_entry, close)
                if h_side in {"bull", "bear"} and h_entry is not None
                else None
            )
            reached_gate = max_unrealized >= PROFIT_GATE_POINTS
            reason = (
                "H 主單已出場/反向/換倉，跟隨出場"
                if h_position_changed
                else f"跌破/站回 {_ma_name(active_side)} 停損；本口最高浮盈 {max_unrealized:.1f} 點"
            )
            signal = {
                "action": "exit",
                "side": active_side,
                "quantity": int(active.get("quantity", ADD_QUANTITY) or ADD_QUANTITY),
                "entry_price": add_entry,
                "close": close,
                "h_position_timestamp": position.get("timestamp", "") if position else "",
                "h_side": h_side,
                "h_entry_price": "" if h_entry is None else h_entry,
                "h_unrealized_points": "" if h_unrealized is None else round(h_unrealized, 1),
                "add_unrealized_points": round(add_unrealized, 1),
                "ma_name": _ma_name(active_side),
                "ma_value": round(ma, 2),
                "reason": reason,
            }
            state["active_add"] = None
            state["last_add_reached_profit_gate"] = reached_gate
            state["last_exit"] = {**signal, "reached_profit_gate": reached_gate, "timestamp": now_str()}
            state["updated_at"] = now_str()
            _write_state(state)
            _append_alert([
                now_str(),
                "exit",
                active_side,
                signal["quantity"],
                add_entry,
                close,
                position.get("timestamp", "") if position else "",
                h_side,
                "" if h_entry is None else h_entry,
                "" if h_unrealized is None else round(h_unrealized, 1),
                round(add_unrealized, 1),
                _ma_name(active_side),
                round(ma, 2),
                signal["reason"],
            ])
            _send_signal_message(signal)
            return signal

        if not position:
            return None

        side = str(position.get("side") or "")
        if side not in {"bull", "bear"}:
            return None

        ma = _ma_value(side, latest)
        if ma is None:
            return None

        h_entry = float(position["price"])
        h_unrealized = _unrealized_points(side, h_entry, close)
        h_key = _position_key(position)
        state = _reset_for_h_position(state, h_key)

        gate_ok = h_unrealized >= PROFIT_GATE_POINTS
        gate_reason = f"H 浮盈 {h_unrealized:.1f} 點 >= {PROFIT_GATE_POINTS:g} 點"

        if not gate_ok or not _has_confirmed_breakout(side, latest_rows):
            state["updated_at"] = now_str()
            _write_state(state)
            return None

        signal = {
            "action": "enter",
            "side": side,
            "quantity": ADD_QUANTITY,
            "entry_price": close,
            "close": close,
            "h_position_timestamp": position.get("timestamp", ""),
            "h_side": side,
            "h_entry_price": h_entry,
            "h_unrealized_points": round(h_unrealized, 1),
            "add_unrealized_points": 0,
            "ma_name": _ma_name(side),
            "ma_value": round(ma, 2),
            "reason": f"{gate_reason}，且連續 {CONFIRM_CLOSES} 根 close 確認突破 {_ma_name(side)}",
        }
        state["active_add"] = {
            "side": side,
            "quantity": ADD_QUANTITY,
            "entry_time": now_str(),
            "entry_price": close,
            "h_position_key": h_key,
            "max_unrealized_points": 0,
            "gate_source": "h",
        }
        state["updated_at"] = now_str()
        _write_state(state)
        _append_alert([
            now_str(),
            "enter",
            side,
            ADD_QUANTITY,
            close,
            close,
            position.get("timestamp", ""),
            side,
            h_entry,
            round(h_unrealized, 1),
            0,
            _ma_name(side),
            round(ma, 2),
            signal["reason"],
        ])
        _send_signal_message(signal)
        return signal


if __name__ == "__main__":
    result = evaluate_h_profit_breakout_add()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
