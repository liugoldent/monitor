"""EF Hysteresis Again monitor and API_KEY TMF order execution."""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, time as day_time, timedelta
from pathlib import Path

from filelock import FileLock, Timeout

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
SOURCE_STRATEGY = BACKEND / "ef-strong-consensus-morning-flat-strategy"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(SOURCE_STRATEGY))

from ef_trade_runtime import (Notifications, append_order, now_local, order_deadline,
                              perform_order, save_state)  # noqa: E402
from shioaji_reconnect_watchdog import BrokerReconnectWatchdog  # noqa: E402
from strategy import (ALL_STRATEGIES, PORTFOLIO_E, PORTFOLIO_F,  # noqa: E402
                      load_signal_rows, parse_position_row, parse_signal_row)

# The preceding import name collides with the source strategy directory when
# executed directly. Load this folder's decision module explicitly instead.
import importlib.util  # noqa: E402
_trade_spec = importlib.util.spec_from_file_location("ef_hysteresis_again_trade", BASE / "auto_trade.py")
if _trade_spec is None or _trade_spec.loader is None:
    raise RuntimeError("無法載入 Again 下單模組")
auto_trade = importlib.util.module_from_spec(_trade_spec)
sys.modules[_trade_spec.name] = auto_trade
_trade_spec.loader.exec_module(auto_trade)
_spec = importlib.util.spec_from_file_location("ef_hysteresis_again_rule", BASE / "strategy.py")
if _spec is None or _spec.loader is None:
    raise RuntimeError("無法載入 Hysteresis Again 決策模組")
_rule = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _rule
_spec.loader.exec_module(_rule)
decide = _rule.decide
exit_on_new_short = _rule.exit_on_new_short
veto_active_opposition = _rule.veto_active_opposition
should_lock_long = _rule.should_lock_long
should_lock_short = _rule.should_lock_short

ENV_PATH = BACKEND / ".env"
SOURCE_PATH = BACKEND / "tv_doc" / "six_strategy_signal_events.csv"
STATE_PATH = BASE / "runtime" / "state.json"
EVENT_PATH = BASE / "records" / "decisions.csv"
NOTICE_PATH = BASE / "records" / "notifications.jsonl"
ORDER_PATH = BASE / "records" / "live_order_attempts.csv"
LOCK_PATH = BASE / "runtime" / "monitor.lock"
WEBHOOK_ENV = "DISCORD_EF_HYSTERESIS_AGAIN_WEBHOOK_URL"
RECONNECT_TIMEOUT_ENV = "EF_HYSTERESIS_AGAIN_RECONNECT_TIMEOUT_SECONDS"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#") and "=" in text:
            key, value = text.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def webhook_url() -> str:
    return os.getenv(WEBHOOK_ENV, "").strip()


def open_broker_for_monitor(notify, *, sleep=time.sleep):
    """Check inventory before signals; let Docker retry transient login failures."""
    try:
        api = auto_trade.initialize_broker_session()
        position = auto_trade.check_startup_broker(api)
    except Exception as exc:
        auto_trade.log_attribute_error(exc)
        message = ("🚨【Again｜啟動查倉失敗】"
                   f"{auto_trade.broker_error_summary(exc)}；尚未讀取新訊號，"
                   "30 秒後由 Docker 重啟再試。")
        print(message, file=sys.stderr, flush=True)
        notify(message)
        sleep(30)
        raise SystemExit(1) from None
    print(f"✅【Again｜啟動查倉】TMF 淨部位：{position} 口（僅查詢，未送單）。",
          flush=True)
    return api, position


def persist(state: dict) -> None:
    if not save_state(STATE_PATH, state):
        raise OSError("無法保存第三策略狀態")


def release_previous_attempt(state: dict) -> None:
    """A prior uncertain call never retries on the same signal."""
    attempt = state.get("attempt", {})
    if attempt.get("status") in {"pending", "failed"}:
        state["last_unconfirmed_attempt"] = attempt.copy()
        attempt["status"] = "interrupted_no_retry" if attempt["status"] == "pending" else "failed_no_retry"
        persist(state)


def execute_target(state: dict, target: int, trigger: str) -> str:
    """Read API_KEY inventory and send its delta once for this new event."""
    broker_target = target * auto_trade.position_unit()
    release_previous_attempt(state)
    if state.get("attempt", {}).get("key") == trigger:
        return "同一訊號已處理，不重送"

    def prepared(data):
        state["attempt"].update(data)
        persist(state)

    try:
        result = perform_order(
            state, key=trigger, target=broker_target, persist=lambda: save_state(STATE_PATH, state),
            execute=lambda: auto_trade.execute_target_position(broker_target, on_prepared=prepared),
            execute_checkpointed=lambda checkpoint: auto_trade.execute_target_position(
                broker_target, deadline=order_deadline(now_local(), broker_target),
                on_prepared=prepared, on_submitted=checkpoint),
            record=lambda **row: append_order(ORDER_PATH, clock=now_local, **row),
            clock=now_local,
        )
    except Exception as exc:
        auto_trade.log_attribute_error(exc)
        summary = auto_trade.broker_error_summary(exc)
        release_previous_attempt(state)
        return f"🚨 券商委託失敗或結果不明：{summary}；本訊號不重送，下一筆新訊號重新查庫存"

    if result.quantity == 0:
        return f"券商 TMF 庫存已是 {position_text(broker_target)}，無需下單"
    return (f"已呼叫{'買進' if result.side == 'buy' else '賣出'} TMF {result.quantity}口委託；"
            f"券商原庫存 {position_text(result.previous_position)}，目標 {position_text(broker_target)}；"
            "API 已回傳，尚未確認成交")


def append_decision(event, previous: int, decision, lock_initialized: bool) -> None:
    EVENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = not EVENT_PATH.exists()
    legacy_columns = False
    if not header:
        with EVENT_PATH.open("r", newline="", encoding="utf-8") as existing:
            legacy_columns = "short_locked" not in next(csv.reader(existing), [])
    with EVENT_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if header:
            writer.writerow(["processed_at", "received_at", "row", "strategy_code",
                             "previous_target", "target", "e_net", "f_net",
                             "long_locked", "short_locked", "lock_initialized", "reason"])
        row = [now_local().isoformat(), event.timestamp.isoformat(), event.row_number,
               event.strategy_code, previous, decision.target, decision.e_net,
               decision.f_net, decision.long_locked]
        if not legacy_columns:
            row.append(decision.short_locked)
        row.extend([lock_initialized, decision.reason])
        writer.writerow(row)


def position_text(value: int) -> str:
    return "空手" if value == 0 else f"{'多' if value > 0 else '空'}{abs(value)}口"


def initialize_state(rows: list[dict[str, str]]) -> dict:
    positions = {code: 0 for code in ALL_STRATEGIES}
    for row in rows:
        parsed = parse_position_row(row)
        if parsed:
            positions[parsed[0]] = parsed[1]
    state = {
        "schema_version": 1,
        "strategy": "ef_hysteresis_again_shadow_v1",
        "raw_positions": positions,
        "source_row_count": len(rows),
        "target": 0,
        "long_locked": False,
        "short_locked": False,
        "lock_initialized_date": None,
        "last_flat_date": None,
        "started_at": now_local().isoformat(),
    }
    persist(state)
    return state


def clock_flatten(state: dict, now: datetime, notify) -> None:
    if not day_time(1) <= now.time() < day_time(8, 45):
        return
    date_text = now.date().isoformat()
    if state.get("last_flat_date") == date_text:
        return
    previous = int(state.get("target", 0))
    state["target"] = 0
    state["long_locked"] = False
    state["short_locked"] = False
    state["lock_initialized_date"] = None
    state["last_flat_date"] = date_text
    persist(state)
    result = execute_target(state, 0, f"01:00/{date_text}")
    notify("🌅【Again｜01:00清倉】\n"
           f"策略目標：{position_text(previous)} → 空手\n"
           f"API_KEY 券商執行：{result}")


def check_daily_inventory(state: dict, now: datetime, api, notify) -> None:
    """Read TMF inventory once per day at or after 08:35; never submit."""
    date_text = now.date().isoformat()
    if now.time() < day_time(8, 35) or state.get("daily_inventory_attempt_date") == date_text:
        return
    state["daily_inventory_attempt_date"] = date_text
    persist(state)
    try:
        position = auto_trade.check_startup_broker(api)
        state["daily_inventory_position"] = position
        state["daily_inventory_checked_at"] = now.isoformat()
        persist(state)
        message = f"✅【Again｜08:35查倉】TMF 淨部位：{position} 口（僅查詢，未送單）。"
        print(message, flush=True)
    except Exception as exc:
        auto_trade.log_attribute_error(exc)
        message = (f"🚨【Again｜08:35查倉失敗】{auto_trade.broker_error_summary(exc)}；"
                   "請人工核對庫存。")
        print(message, file=sys.stderr, flush=True)
    notify(message)


def process_rows(state: dict, rows: list[dict[str, str]], notify,
                 *, submit_orders: bool = True, record_decisions: bool = True) -> None:
    count = int(state.get("source_row_count", 0))
    positions = {code: int(state.get("raw_positions", {}).get(code, 0))
                 for code in ALL_STRATEGIES}
    current = int(state.get("target", 0))
    long_locked = bool(state.get("long_locked", False))
    short_locked = bool(state.get("short_locked", False))
    initialized_date = state.get("lock_initialized_date")

    for row_number, row in enumerate(rows[count:], start=count + 1):
        event = parse_signal_row(row, row_number)
        state["source_row_count"] = row_number
        if event is None:
            parsed = parse_position_row(row)
            if parsed:
                positions[parsed[0]] = parsed[1]
            continue

        morning = day_time(1) <= event.timestamp.time() < day_time(8, 45)
        lock_initialized = False
        cycle_date = (event.timestamp.date() if event.timestamp.time() >= day_time(8, 45)
                      else event.timestamp.date() - timedelta(days=1))
        if not morning and initialized_date != cycle_date.isoformat():
            long_locked = should_lock_long(positions, PORTFOLIO_E, PORTFOLIO_F)
            short_locked = should_lock_short(positions, PORTFOLIO_E, PORTFOLIO_F)
            initialized_date = cycle_date.isoformat()
            lock_initialized = True

        tracked_previous = positions[event.strategy_code]
        positions[event.strategy_code] = event.new_position
        previous = current
        decision = decide(positions, PORTFOLIO_E, PORTFOLIO_F, current,
                          long_locked, short_locked)
        if morning:
            decision = _rule.Decision(0, decision.e_net, decision.f_net, False, False,
                                      "01:00～08:45只更新E/F狀態，不建立影子部位")
        else:
            decision = exit_on_new_short(decision, previous, tracked_previous,
                                         event.new_position)
            decision = veto_active_opposition(decision, positions,
                                              PORTFOLIO_E, PORTFOLIO_F)
        current = decision.target
        long_locked = decision.long_locked
        short_locked = decision.short_locked
        state.update({
            "raw_positions": positions.copy(), "target": current,
            "long_locked": long_locked, "short_locked": short_locked,
            "lock_initialized_date": initialized_date,
            "last_event": event.timestamp.isoformat(), "source_row_count": row_number,
        })
        persist(state)
        if record_decisions:
            append_decision(event, previous, decision, lock_initialized)
        order_result = (execute_target(state, current,
                        f"signal/{event.timestamp.isoformat()}/{row_number}")
                        if submit_orders and not morning else
                        "01:00～08:45只更新 E/F，不送委託" if morning else "啟動狀態重建，未送委託")
        tracked_hint = (f"追蹤前部位：{tracked_previous:+d}（訊號記載：{event.previous_position:+d}）\n"
                        if tracked_previous != event.previous_position else "")
        message = (
            "📊【EF Hysteresis Again｜API_KEY】\n"
            f"訊號：{event.strategy_name or event.strategy_code} "
            f"{event.previous_position:+d} → {event.new_position:+d}\n"
            f"{tracked_hint}"
            f"E淨部位：{decision.e_net:+d}；F淨部位：{decision.f_net:+d}\n"
            f"多方鎖定：{'是' if decision.long_locked else '否'}"
            f"；空方鎖定：{'是' if decision.short_locked else '否'}"
            f"{'（本日首次判斷）' if lock_initialized else ''}\n"
            f"策略目標：{position_text(previous)} → {position_text(current)}\n"
            f"原因：{decision.reason}\n"
            f"券商執行：{order_result}"
        )
        if record_decisions:
            print(message, flush=True)
            notify(message)

    state["raw_positions"] = positions
    state["source_row_count"] = len(rows)
    persist(state)


def rebuild_startup_state(state: dict, rows: list[dict[str, str]], now: datetime) -> None:
    """Recreate today's signal target, consuming old rows without broker calls."""
    last_live_flat_date = (state.get("last_flat_date")
                           if state.get("strategy") == "ef_hysteresis_again_live_v2" else None)
    cycle = now.date() if now.time() >= day_time(8, 45) else now.date() - timedelta(days=1)
    reopen = datetime.combine(cycle, day_time(8, 45))
    positions = {code: 0 for code in ALL_STRATEGIES}
    cursor = 0
    for row_number, row in enumerate(rows, start=1):
        event = parse_signal_row(row, row_number)
        if event is not None and event.timestamp >= reopen:
            break
        parsed = parse_position_row(row)
        if parsed:
            positions[parsed[0]] = parsed[1]
        cursor = row_number
    preserved = {key: value for key, value in state.items()
                 if key in {"attempt", "last_unconfirmed_attempt", "started_at"}}
    state.clear()
    state.update(preserved)
    state.update({"schema_version": 2, "strategy": "ef_hysteresis_again_live_v2",
                  "raw_positions": positions, "source_row_count": cursor, "target": 0,
                  "long_locked": False, "short_locked": False,
                  "lock_initialized_date": None,
                  "last_flat_date": last_live_flat_date})
    process_rows(state, rows, lambda _: None, submit_orders=False,
                 record_decisions=False)
    if day_time(1) <= now.time() < day_time(8, 45):
        state.update({"target": 0, "long_locked": False, "short_locked": False,
                      "lock_initialized_date": None})
    persist(state)


def main() -> None:
    load_env(ENV_PATH)
    poll = max(0.5, float(os.getenv("EF_HYSTERESIS_AGAIN_POLL_SECONDS", "2")))
    reconnect_timeout = float(os.getenv(RECONNECT_TIMEOUT_ENV, "120"))
    notifier = Notifications(webhook_url, NOTICE_PATH)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("第三策略已有另一個監控實例") from exc
    try:
        # Login and read inventory before consuming the first live signal.
        api, broker_position = open_broker_for_monitor(notifier)
        watchdog = BrokerReconnectWatchdog(reconnect_timeout)
        api.set_event_callback(watchdog.on_event)
        rows = load_signal_rows(SOURCE_PATH)
        state = (json.loads(STATE_PATH.read_text(encoding="utf-8"))
                 if STATE_PATH.exists() else initialize_state(rows))
        rebuild_startup_state(state, rows, now_local())
        clock_flatten(state, now_local(), notifier)
        if now_local().time() >= day_time(8, 35):
            state["daily_inventory_attempt_date"] = now_local().date().isoformat()
            state["daily_inventory_position"] = broker_position
            state["daily_inventory_checked_at"] = now_local().isoformat()
            persist(state)
        notifier("✅【開始監控｜第三策略 EF Hysteresis Again】\n"
                 "固定門檻：進場2、續抱1。\n"
                 "持多時追蹤到0→-1反向訊號，當筆目標出場。\n"
                 "任一追蹤策略仍為-1時不建立或持有多單；仍為+1時不建立或持有空單。\n"
                 "01:00清倉；若08:45後首筆訊號前E/F已達同向2/2，"
                 "多空皆須先脫離門檻，再重新達標才進場。\n"
                 f"模式：API_KEY Shioaji {'模擬帳戶' if auto_trade.BROKER_SIMULATION else '實單'}，U={auto_trade.position_unit()}；"
                 f"啟動券商庫存 {position_text(broker_position)}，策略目標 "
                 f"{position_text(state['target'] * auto_trade.position_unit())}。"
                 "啟動不補單，下一筆新訊號按券商庫存送差額。")
        while True:
            disconnected, expired = watchdog.status()
            if expired:
                raise RuntimeError(
                    f"永豐連線超過 {reconnect_timeout:g} 秒未恢復；退出並交由 Docker 重啟"
                )
            if disconnected:
                time.sleep(poll)
                continue
            if watchdog.consume_recovery():
                rows = load_signal_rows(SOURCE_PATH)
                skipped = len(rows) - int(state.get("source_row_count", 0))
                if skipped > 0:
                    process_rows(state, rows, lambda _: None,
                                 submit_orders=False, record_decisions=False)
                notifier("✅【Again｜永豐連線恢復】"
                         f"\n斷線期間新增訊號 {max(skipped, 0)} 筆已更新策略狀態，未補送舊單。"
                         "下一筆新訊號會重新查券商庫存。")
            clock_flatten(state, now_local(), notifier)
            check_daily_inventory(state, now_local(), api, notifier)
            process_rows(state, load_signal_rows(SOURCE_PATH), notifier)
            time.sleep(poll)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
