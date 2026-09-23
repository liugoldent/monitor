"""Shadow monitor for EF Hysteresis Again; never submits broker orders."""
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

from ef_trade_runtime import Notifications, now_local, save_state  # noqa: E402
from strategy import (ALL_STRATEGIES, PORTFOLIO_E, PORTFOLIO_F,  # noqa: E402
                      load_signal_rows, parse_position_row, parse_signal_row)

# The preceding import name collides with the source strategy directory when
# executed directly. Load this folder's decision module explicitly instead.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ef_hysteresis_again_rule", BASE / "strategy.py")
if _spec is None or _spec.loader is None:
    raise RuntimeError("無法載入 Hysteresis Again 決策模組")
_rule = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _rule
_spec.loader.exec_module(_rule)
decide = _rule.decide
should_lock_long = _rule.should_lock_long
should_lock_short = _rule.should_lock_short

ENV_PATH = BACKEND / ".env"
SOURCE_PATH = BACKEND / "tv_doc" / "six_strategy_signal_events.csv"
STATE_PATH = BASE / "runtime" / "state.json"
EVENT_PATH = BASE / "records" / "decisions.csv"
NOTICE_PATH = BASE / "records" / "notifications.jsonl"
LOCK_PATH = BASE / "runtime" / "monitor.lock"
WEBHOOK_ENV = "DISCORD_EF_HYSTERESIS_AGAIN_WEBHOOK_URL"


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


def persist(state: dict) -> None:
    if not save_state(STATE_PATH, state):
        raise OSError("無法保存第三策略狀態")


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
    notify("🌅【第三策略｜01:00影子清倉】\n"
           f"影子目標：{position_text(previous)} → 空手\n"
           "本策略不連線券商、不送委託；08:45後依重新突破規則判斷。")


def process_rows(state: dict, rows: list[dict[str, str]], notify) -> None:
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

        positions[event.strategy_code] = event.new_position
        previous = current
        decision = decide(positions, PORTFOLIO_E, PORTFOLIO_F, current,
                          long_locked, short_locked)
        if morning:
            decision = _rule.Decision(0, decision.e_net, decision.f_net, False, False,
                                      "01:00～08:45只更新E/F狀態，不建立影子部位")
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
        append_decision(event, previous, decision, lock_initialized)
        message = (
            "📊【第三策略｜EF Hysteresis Again｜影子】\n"
            f"訊號：{event.strategy_name or event.strategy_code} "
            f"{event.previous_position:+d} → {event.new_position:+d}\n"
            f"E淨部位：{decision.e_net:+d}；F淨部位：{decision.f_net:+d}\n"
            f"多方鎖定：{'是' if decision.long_locked else '否'}"
            f"；空方鎖定：{'是' if decision.short_locked else '否'}"
            f"{'（本日首次判斷）' if lock_initialized else ''}\n"
            f"影子目標：{position_text(previous)} → {position_text(current)}\n"
            f"原因：{decision.reason}\n"
            "僅記錄與通知，不連線券商、不送實單。"
        )
        print(message, flush=True)
        notify(message)

    state["raw_positions"] = positions
    state["source_row_count"] = len(rows)
    persist(state)


def main() -> None:
    load_env(ENV_PATH)
    poll = max(0.5, float(os.getenv("EF_HYSTERESIS_AGAIN_POLL_SECONDS", "2")))
    notifier = Notifications(webhook_url, NOTICE_PATH)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("第三策略已有另一個監控實例") from exc
    try:
        rows = load_signal_rows(SOURCE_PATH)
        state = (json.loads(STATE_PATH.read_text(encoding="utf-8"))
                 if STATE_PATH.exists() else initialize_state(rows))
        notifier("✅【開始監控｜第三策略 EF Hysteresis Again】\n"
                 "固定門檻：進場2、續抱1。\n"
                 "01:00清倉；若08:45後首筆訊號前E/F已達同向2/2，"
                 "多空皆須先脫離門檻，再重新達標才進場。\n"
                 "模式：影子監控，絕不送出券商委託。")
        while True:
            clock_flatten(state, now_local(), notifier)
            process_rows(state, load_signal_rows(SOURCE_PATH), notifier)
            time.sleep(poll)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
