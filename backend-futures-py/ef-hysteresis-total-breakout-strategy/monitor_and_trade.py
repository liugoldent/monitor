"""Shadow monitor for EF Hysteresis pre-open TOTAL breakout."""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
import time
from datetime import date, datetime, time as day_time, timedelta
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

_spec = importlib.util.spec_from_file_location("ef_hysteresis_total_rule", BASE / "strategy.py")
if _spec is None or _spec.loader is None:
    raise RuntimeError("無法載入 TOTAL 突破決策模組")
_rule = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _rule
_spec.loader.exec_module(_rule)

ENV_PATH = BACKEND / ".env"
SOURCE_PATH = BACKEND / "tv_doc" / "six_strategy_signal_events.csv"
STATE_PATH = BASE / "runtime" / "state.json"
EVENT_PATH = BASE / "records" / "decisions.csv"
NOTICE_PATH = BASE / "records" / "notifications.jsonl"
LOCK_PATH = BASE / "runtime" / "monitor.lock"
WEBHOOK_ENV = "DISCORD_EF_HYSTERESIS_TOTAL_BREAKOUT_WEBHOOK_URL"
BASELINE_RULE_VERSION = 2
NIGHT_CLOSE = day_time(5)
DAY_REOPEN = day_time(8, 45)


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
        raise OSError("無法保存 TOTAL 突破策略狀態")


def position_text(value: int) -> str:
    return "空手" if value == 0 else f"{'多' if value > 0 else '空'}{abs(value)}口"


def cycle_date(stamp: datetime):
    return stamp.date() if stamp.time() >= DAY_REOPEN else stamp.date() - timedelta(days=1)


def positions_before(rows, cutoff: datetime) -> dict[str, int]:
    """Reconstruct the last received EF state strictly before a clock cutoff."""
    positions = {code: 0 for code in ALL_STRATEGIES}
    for row_number, row in enumerate(rows, start=1):
        event = parse_signal_row(row, row_number)
        if event is not None:
            if event.timestamp < cutoff:
                positions[event.strategy_code] = event.new_position
        elif not str(row.get("received_at") or "").strip():
            # The initial, untimed snapshot predates the actionable event log.
            parsed = parse_position_row(row)
            if parsed:
                positions[parsed[0]] = parsed[1]
    return positions


def freeze_night_close_baseline(state: dict, rows, trading_date: date) -> bool:
    """Fix E+F at the last state before 05:00, including after a restart."""
    cycle = trading_date.isoformat()
    if (state.get("baseline_cycle") == cycle
            and state.get("baseline_rule_version") == BASELINE_RULE_VERSION):
        return False
    cutoff = datetime.combine(trading_date, NIGHT_CLOSE)
    positions = positions_before(rows, cutoff)
    baseline = _rule.capture_baseline(positions, PORTFOLIO_E, PORTFOLIO_F)
    state.update({"baseline_cycle": cycle, "baseline_rule_version": BASELINE_RULE_VERSION,
                  "baseline_asof": cutoff.isoformat(), "bull_baseline": baseline.bull,
                  "bear_baseline": baseline.bear})
    persist(state)
    return True


def migrate_saved_target(state: dict, rows, now: datetime) -> None:
    """Re-evaluate today's already seen shadow events under the new baseline."""
    if state.get("target_rule_version") == BASELINE_RULE_VERSION:
        return
    if day_time(1) <= now.time() < NIGHT_CLOSE:
        return
    trading_date = (now.date() if now.time() >= NIGHT_CLOSE
                    else now.date() - timedelta(days=1))
    freeze_night_close_baseline(state, rows, trading_date)
    baseline = _rule.Baseline(int(state["bull_baseline"]), int(state["bear_baseline"]))
    reopen = datetime.combine(trading_date, DAY_REOPEN)
    positions = positions_before(rows, reopen)
    target = 0
    cursor = min(int(state.get("source_row_count", 0)), len(rows))
    for row_number, row in enumerate(rows[:cursor], start=1):
        event = parse_signal_row(row, row_number)
        if event is None or not reopen <= event.timestamp <= now:
            continue
        positions[event.strategy_code] = event.new_position
        target = _rule.decide(positions, PORTFOLIO_E, PORTFOLIO_F,
                              target, baseline).target
    state["target"] = target
    state["target_rule_version"] = BASELINE_RULE_VERSION
    persist(state)


def append_decision(event, previous, decision, baseline, initialized) -> None:
    EVENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = not EVENT_PATH.exists()
    with EVENT_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if header:
            writer.writerow(["processed_at", "received_at", "row", "strategy_code",
                             "previous_target", "target", "e_net", "f_net", "total",
                             "bull_baseline", "bear_baseline", "baseline_initialized", "reason"])
        writer.writerow([now_local().isoformat(), event.timestamp.isoformat(), event.row_number,
                         event.strategy_code, previous, decision.target, decision.e_net,
                         decision.f_net, decision.total, baseline.bull, baseline.bear,
                         initialized, decision.reason])


def initialize_state(rows) -> dict:
    positions = {code: 0 for code in ALL_STRATEGIES}
    for row in rows:
        parsed = parse_position_row(row)
        if parsed:
            positions[parsed[0]] = parsed[1]
    state = {
        "schema_version": 2,
        "strategy": "ef_hysteresis_total_breakout_shadow_v2",
        "raw_positions": positions,
        "source_row_count": len(rows),
        "target": 0,
        "baseline_cycle": None,
        "baseline_rule_version": BASELINE_RULE_VERSION,
        "baseline_asof": None,
        "target_rule_version": BASELINE_RULE_VERSION,
        "bull_baseline": 0,
        "bear_baseline": 0,
        "last_flat_date": None,
        "started_at": now_local().isoformat(),
    }
    persist(state)
    return state


def clock_flatten(state, now, notify) -> None:
    if not day_time(1) <= now.time() < day_time(8, 45):
        return
    date_text = now.date().isoformat()
    if state.get("last_flat_date") == date_text:
        return
    previous = int(state.get("target", 0))
    state.update({"target": 0, "baseline_cycle": None, "baseline_asof": None,
                  "bull_baseline": 0, "bear_baseline": 0, "last_flat_date": date_text})
    persist(state)
    notify("🌅【TOTAL突破策略｜01:00影子清倉】\n"
           f"影子目標：{position_text(previous)} → 空手\n"
           "01:00～05:00繼續更新E/F；以05:00前最後狀態固定TOTAL基準。\n"
           "本策略不連線券商、不送委託。")


def process_rows(state, rows, notify) -> None:
    count = int(state.get("source_row_count", 0))
    positions = {code: int(state.get("raw_positions", {}).get(code, 0))
                 for code in ALL_STRATEGIES}
    current = int(state.get("target", 0))
    baseline_cycle = state.get("baseline_cycle")
    baseline = _rule.Baseline(int(state.get("bull_baseline", 0)),
                              int(state.get("bear_baseline", 0)))

    for row_number, row in enumerate(rows[count:], start=count + 1):
        event = parse_signal_row(row, row_number)
        state["source_row_count"] = row_number
        if event is None:
            parsed = parse_position_row(row)
            if parsed:
                positions[parsed[0]] = parsed[1]
            continue

        morning = day_time(1) <= event.timestamp.time() < day_time(8, 45)
        cycle = cycle_date(event.timestamp).isoformat()
        initialized = False
        if not morning and (baseline_cycle != cycle or
                            state.get("baseline_rule_version") != BASELINE_RULE_VERSION):
            initialized = freeze_night_close_baseline(state, rows, date.fromisoformat(cycle))
            baseline_cycle = state["baseline_cycle"]
            baseline = _rule.Baseline(int(state["bull_baseline"]),
                                      int(state["bear_baseline"]))

        positions[event.strategy_code] = event.new_position
        previous = current
        decision = _rule.decide(positions, PORTFOLIO_E, PORTFOLIO_F, current, baseline)
        if morning:
            decision = _rule.Decision(0, decision.e_net, decision.f_net, decision.total,
                                      "01:00～08:45只更新E/F，不建立影子部位")
        current = decision.target
        state.update({
            "raw_positions": positions.copy(), "target": current,
            "baseline_cycle": baseline_cycle, "bull_baseline": baseline.bull,
            "bear_baseline": baseline.bear, "last_event": event.timestamp.isoformat(),
            "source_row_count": row_number,
        })
        persist(state)
        append_decision(event, previous, decision, baseline, initialized)
        message = (
            "📈【EF Hysteresis TOTAL突破｜影子】\n"
            f"訊號：{event.strategy_name or event.strategy_code} "
            f"{event.previous_position:+d} → {event.new_position:+d}\n"
            f"E：{decision.e_net:+d}；F：{decision.f_net:+d}；TOTAL：{decision.total:+d}\n"
            f"05:00前基準：多方{baseline.bull}／空方{baseline.bear}"
            f"{'（本交易週期剛建立）' if initialized else ''}\n"
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
    poll = max(0.5, float(os.getenv("EF_HYSTERESIS_TOTAL_BREAKOUT_POLL_SECONDS", "2")))
    notifier = Notifications(webhook_url, NOTICE_PATH)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("TOTAL突破策略已有另一個監控實例") from exc
    try:
        rows = load_signal_rows(SOURCE_PATH)
        state = (json.loads(STATE_PATH.read_text(encoding="utf-8"))
                 if STATE_PATH.exists() else initialize_state(rows))
        current_time = now_local()
        migrate_saved_target(state, rows, current_time)
        if current_time.time() >= NIGHT_CLOSE:
            freeze_night_close_baseline(state, rows, current_time.date())
        notifier("✅【開始監控｜EF Hysteresis TOTAL突破】\n"
                 "進場門檻2、續抱門檻1；01:00影子清倉。\n"
                 "基準取05:00前最後EF狀態；08:45後E/F達2/2且方向TOTAL嚴格突破基準才進場。\n"
                 "模式：影子監控，絕不送出券商委託。")
        while True:
            current_time = now_local()
            clock_flatten(state, current_time, notifier)
            rows = load_signal_rows(SOURCE_PATH)
            if current_time.time() >= NIGHT_CLOSE:
                freeze_night_close_baseline(state, rows, current_time.date())
            process_rows(state, rows, notifier)
            time.sleep(poll)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
