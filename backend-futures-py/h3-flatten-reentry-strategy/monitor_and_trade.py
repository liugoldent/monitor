"""Consume newly received raw Relay messages without replaying old signals."""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from filelock import FileLock

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
sys.path.insert(0, str(BACKEND))
from ef_trade_runtime import Notifications, append_order, now_local, save_state
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog
import auto_trade
from h_signal import parse_h_event_direction
from trade_records import entry_record, append_entry

SOURCE = BACKEND / "telegram-relay-records/telegram_signal_events.jsonl"
STATE = BASE / "runtime/state.json"
ORDERS = BASE / "records/live_order_attempts.csv"
ENTRIES = BASE / "records/entries.csv"
DUPLICATE_WINDOW_SECONDS = 10


def persist(state):
    if not save_state(STATE, state):
        raise OSError("浩克策略狀態保存失敗")


def process_event(event, state, notify):
    direction = parse_h_event_direction(event)
    if direction is None:
        return
    key = f"{event['chat_id']}:{event['message_id']}"
    if key in state.get("seen", []):
        return
    state.setdefault("seen", []).append(key)
    state["seen"] = state["seen"][-10000:]
    # Compare arrival times, not order completion times: execution can take >10s.
    try:
        received_at = datetime.fromisoformat(event["received_at"])
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=now_local().tzinfo)
        received_timestamp = received_at.timestamp()
    except (KeyError, TypeError, ValueError):
        received_timestamp = now_local().timestamp()
    previous = state.get("last_signal", {})
    duplicate = (previous.get("direction") == direction
                 and previous.get("received_timestamp") is not None
                 and 0 <= received_timestamp - previous["received_timestamp"]
                 <= DUPLICATE_WINDOW_SECONDS)
    if duplicate:
        persist(state)  # Also consume the duplicate's distinct Telegram ID.
        append_order(ORDERS, event="duplicate_filtered", trigger=key,
                     detail="10秒內連續同方向訊號，跳過平倉及進場")
        return
    state["last_signal"] = {"key": key, "direction": direction, "status": "pending",
                            "received_timestamp": received_timestamp}
    persist(state)  # Consume before any order; restart never replays the signal.

    def record(phase, result):
        if phase == "flatten":
            state.pop("current_entry", None)
            persist(state)
        if phase == "entry" and result.quantity:
            entry = entry_record(result, key, now_local().isoformat())
            append_entry(ENTRIES, entry)
            state["current_entry"] = entry
            persist(state)
        append_order(ORDERS, event=phase, trigger=key,
                     target_position=result.target_position,
                     previous_position=result.previous_position,
                     actual_position=result.actual_position, side=result.side or "",
                     quantity=result.quantity, detail="庫存及委託已確認")

    try:
        result = auto_trade.execute_signal(direction, guard=state.setdefault("guard", {}),
                                          persist=lambda: persist(state), record=record)
    except Exception as exc:
        state["last_signal"]["status"] = "failed_no_retry"
        persist(state)
        append_order(ORDERS, event="failed", trigger=key, detail=type(exc).__name__)
        notify(f"🚨【浩克3｜API_KEY3】委託未完成：{type(exc).__name__}；本訊號不重送，請核對庫存及委託。")
        return
    state["last_signal"]["status"] = "confirmed"
    persist(state)
    entry = state.get("current_entry", {})
    entry_info = (f"\n進場時間：{entry['entry_time']}\n進場點位：{entry['entry_price']}（第一口成交價）"
                  if entry.get("fill_status") == "complete" else
                  "\n進場時間／點位：券商成交明細未齊，待核對")
    notify(f"✅【浩克3｜API_KEY3】先平倉再進場完成；TMF 部位 {result.actual_position:+d}口，H_UNIT={auto_trade.position_unit()}{entry_info}")


def main():
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))
    auto_trade.position_unit()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(BASE / "runtime/monitor.lock"), timeout=0):
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        notify = Notifications(lambda: os.getenv("DISCORD_H_STRATEGY_WEBHOOK_URL", ""),
                               BASE / "records/notifications.jsonl")
        api = auto_trade.initialize_broker_session()
        inventory = auto_trade.shared.check_startup_broker(api)
        watchdog = BrokerReconnectWatchdog(float(os.getenv("H3_REENTRY_RECONNECT_TIMEOUT_SECONDS", "120")))
        api.set_event_callback(watchdog.on_event)
        # Start at EOF on every start: no historical or downtime orders.
        offset = SOURCE.stat().st_size if SOURCE.exists() else 0
        identity = SOURCE.stat().st_ino if SOURCE.exists() else None
        persist(state)
        notify(f"✅【浩克3策略啟動｜API_KEY3】TMF 庫存 {inventory:+d}口；H_UNIT={auto_trade.position_unit()}。等待新訊號。")
        poll = max(0.5, float(os.getenv("H3_REENTRY_POLL_SECONDS", "2")))
        while True:
            disconnected, expired = watchdog.status()
            if expired:
                raise RuntimeError("永豐重連逾時，交由 Docker 重啟")
            recovered = watchdog.consume_recovery()
            if SOURCE.exists():
                stat = SOURCE.stat()
                if stat.st_ino != identity or stat.st_size < offset:
                    offset = 0
                identity = stat.st_ino
                if disconnected or recovered:
                    offset = stat.st_size
                else:
                    with SOURCE.open("rb") as handle:
                        handle.seek(offset)
                        while True:
                            line = handle.readline()
                            if not line or not line.endswith(b"\n"):
                                break
                            offset = handle.tell()
                            try:
                                event = json.loads(line)
                            except (ValueError, UnicodeError):
                                continue
                            process_event(event, state, notify)
            time.sleep(poll)


if __name__ == "__main__":
    main()
