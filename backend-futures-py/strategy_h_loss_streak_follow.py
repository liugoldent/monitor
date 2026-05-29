"""策略名稱：H 連輸 2 次一次性跟單。

用途：第二帳號實單策略，webhook 會把 enter/exit 訊號交給
`execute_h_loss_streak_follow_signal()` 下單。

進場規則：
- H 策略最近已完成交易剛好連續虧損 2 次。
- 下一筆 H 新倉出現後，且該 H 新倉仍在新鮮時間內，就用第二帳號同向跟 1 口。
- 若第二帳號已有 H 獲利突破加碼或反向護欄部位，則不開新的連輸跟單。

出場規則：
- 這筆跟單跟著 H 第三筆交易出場、反向或換倉同步出場。
- 出場後本策略回到空手；不論第三筆贏或輸，同一輪連輸跟單只做一次。
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
PROFIT_BREAKOUT_STATE_PATH = STRATEGY_STATE_DIR / "h_profit_breakout_add_state.json"
REVERSE_GUARD_STATE_PATH = STRATEGY_STATE_DIR / "h_reverse_guard_state.json"
STATE_PATH = STRATEGY_STATE_DIR / "h_loss_streak_follow_state.json"
ALERT_PATH = STRATEGY_ALERT_DIR / "h_loss_streak_follow_alert.csv"

ENTRY_LOSS_STREAK = 2
FOLLOW_QUANTITY = 1
MAX_ENTRY_AGE_MINUTES = 20

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
    "consecutive_loss_count",
    "reason",
]

LOCK = RLock()
send_loss_streak_message = build_shortcycle_send_discord_message(str(MXF_VALUE_CSV_PATH))


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_state() -> dict:
    return _read_json(STATE_PATH)


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_alert(row: list[object]) -> None:
    exists = ALERT_PATH.exists()
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def _iter_exiting_pnls() -> list[float]:
    if not H_TRADE_CSV_PATH.exists():
        return []

    pnls: list[float] = []
    with H_TRADE_CSV_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("action") != "exiting":
                continue
            pnl = to_float(row.get("pnl"))
            if pnl is not None:
                pnls.append(pnl)
    return pnls


def _get_consecutive_loss_count() -> int:
    loss_count = 0
    for pnl in reversed(_iter_exiting_pnls()):
        if pnl < 0:
            loss_count += 1
            continue
        break
    return loss_count


def _position_key(position: dict | None) -> str:
    if not position:
        return ""
    return f"{position.get('timestamp', '')}|{position.get('side', '')}|{position.get('price', '')}"


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _latest_market_time(latest_1m: dict | None) -> datetime | None:
    if not latest_1m:
        return None
    return _parse_time(latest_1m.get("TradingView Time") or latest_1m.get("Record Time"))


def _is_fresh_h_position(position: dict, latest_1m: dict | None) -> bool:
    entry_time = _parse_time(position.get("timestamp"))
    market_time = _latest_market_time(latest_1m)
    if entry_time is None or market_time is None:
        return False
    if market_time < entry_time:
        return False
    return (market_time - entry_time).total_seconds() <= MAX_ENTRY_AGE_MINUTES * 60


def _unrealized_points(side: str, entry_price: float, close: float) -> float:
    if side == "bull":
        return close - entry_price
    return entry_price - close


def _side_text(side: str) -> str:
    if side == "bull":
        return "多單"
    if side == "bear":
        return "空單"
    return side


def _other_second_account_position_active() -> bool:
    profit_state = _read_json(PROFIT_BREAKOUT_STATE_PATH)
    reverse_state = _read_json(REVERSE_GUARD_STATE_PATH)
    return bool(profit_state.get("active_add") or reverse_state.get("active_guard"))


def _send_signal_message(signal: dict) -> None:
    action = str(signal.get("action") or "")
    side = str(signal.get("side") or "")
    action_text = "進場" if action == "enter" else "出場"
    message = (
        "策略=H連輸2次一次性跟單(strategy_h_loss_streak_follow)；"
        f"第二帳號{action_text}：{_side_text(side)} {signal.get('quantity', FOLLOW_QUANTITY)}口，"
        f"價格={signal.get('close', signal.get('entry_price', ''))}，"
        f"H方向={_side_text(str(signal.get('h_side') or side))}，"
        f"H進場={signal.get('h_entry_price', '')}，"
        f"H浮動={signal.get('h_unrealized_points', '')}點，"
        f"連輸={signal.get('consecutive_loss_count', '')}，"
        f"原因={signal.get('reason', '')}"
    )
    send_loss_streak_message(message)


def evaluate_h_loss_streak_follow() -> dict | None:
    """Evaluate the one-shot loss-streak follow strategy."""
    with LOCK:
        position = _latest_h_position()
        latest_1m = _latest_csv_row(WEBHOOK_1M_CSV_PATH)
        close = to_float(latest_1m.get("Close")) if latest_1m else None
        loss_count = _get_consecutive_loss_count()
        state = _read_state()
        active = state.get("active_follow")
        h_key = _position_key(position)

        if not active and state.get("cycle_consumed"):
            state["cycle_consumed"] = False
            state["consumed_loss_streak"] = ""
            state["updated_at"] = now_str()
            _write_state(state)

        if active:
            active_side = str(active.get("side") or "")
            active_h_key = str(active.get("h_position_key") or "")
            h_position_changed = active_h_key != h_key
            if not h_position_changed:
                return None

            entry_price = to_float(active.get("entry_price"))
            h_side = str(position.get("side") or "") if position else ""
            h_entry = to_float(position.get("price")) if position else None
            h_unrealized = (
                _unrealized_points(h_side, h_entry, close)
                if close is not None and h_side in {"bull", "bear"} and h_entry is not None
                else ""
            )
            signal = {
                "action": "exit",
                "side": active_side,
                "quantity": int(active.get("quantity", FOLLOW_QUANTITY) or FOLLOW_QUANTITY),
                "entry_price": "" if entry_price is None else entry_price,
                "close": "" if close is None else close,
                "h_position_timestamp": position.get("timestamp", "") if position else "",
                "h_side": h_side,
                "h_entry_price": "" if h_entry is None else h_entry,
                "h_unrealized_points": "" if h_unrealized == "" else round(float(h_unrealized), 1),
                "consecutive_loss_count": loss_count,
                "reason": "H 主單已出場/反向/換倉，連輸跟單同步出場；第三次交易已完成，回到平常加碼/護欄狀態",
            }
            state["active_follow"] = None
            state["cycle_consumed"] = False
            state["consumed_loss_streak"] = ""
            state["last_completed_follow"] = {**signal, "timestamp": now_str()}
            state["last_exit"] = {**signal, "timestamp": now_str()}
            state["updated_at"] = now_str()
            _write_state(state)
            _append_alert([
                now_str(),
                "exit",
                active_side,
                signal["quantity"],
                signal["entry_price"],
                signal["close"],
                signal["h_position_timestamp"],
                signal["h_side"],
                signal["h_entry_price"],
                signal["h_unrealized_points"],
                loss_count,
                signal["reason"],
            ])
            _send_signal_message(signal)
            return signal

        if not position or close is None:
            return None

        side = str(position.get("side") or "")
        if side not in {"bull", "bear"}:
            return None
        if loss_count != ENTRY_LOSS_STREAK:
            return None
        if state.get("cycle_consumed"):
            return None
        if state.get("last_entry_h_key") == h_key:
            return None
        if _other_second_account_position_active():
            return None
        if not _is_fresh_h_position(position, latest_1m):
            return None

        h_entry = float(position["price"])
        h_unrealized = _unrealized_points(side, h_entry, close)
        reason = f"H 已完成連輸 {ENTRY_LOSS_STREAK} 次，第二帳號閒置，下一筆 H 新倉一次性同向跟單"
        signal = {
            "action": "enter",
            "side": side,
            "quantity": FOLLOW_QUANTITY,
            "entry_price": close,
            "close": close,
            "h_position_timestamp": position.get("timestamp", ""),
            "h_side": side,
            "h_entry_price": h_entry,
            "h_unrealized_points": round(h_unrealized, 1),
            "consecutive_loss_count": loss_count,
            "reason": reason,
        }
        state["active_follow"] = {
            "side": side,
            "quantity": FOLLOW_QUANTITY,
            "entry_time": now_str(),
            "entry_price": close,
            "h_position_key": h_key,
        }
        state["last_entry_h_key"] = h_key
        state["cycle_consumed"] = True
        state["consumed_loss_streak"] = loss_count
        state["updated_at"] = now_str()
        _write_state(state)
        _append_alert([
            now_str(),
            "enter",
            side,
            FOLLOW_QUANTITY,
            close,
            close,
            position.get("timestamp", ""),
            side,
            h_entry,
            round(h_unrealized, 1),
            loss_count,
            reason,
        ])
        _send_signal_message(signal)
        return signal


if __name__ == "__main__":
    result = evaluate_h_loss_streak_follow()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
