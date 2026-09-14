"""Pure EF account 2: 04:59 flat, wait for new signals after 08:45."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
from datetime import datetime, time as day_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock

from auto_trade import execute_target_position
from strategy import Calendar, integer, latest_closure, pure_position

RETRY_SECONDS = 10

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
sys.path.insert(0, str(BACKEND))
from ef_trade_runtime import (Notifications as SharedNotifications, append_order, perform_order,
                              result_text, execution_message, save_state)


def now_local() -> datetime:
    return datetime.now(ZoneInfo("Asia/Taipei")).replace(tzinfo=None)


def load_env(path: Path) -> None:
    if path.exists():
        from dotenv import load_dotenv
        load_dotenv(path, override=False)


def flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def save(path: Path, data: dict) -> None:
    if not save_state(path, data):
        raise OSError("狀態檔無法安全寫入，基於安全未送單")


def webhook_url() -> str:
    return os.getenv("DISCORD_EF_hedge_WEBHOOK_URL", "").strip()


class Notifications(SharedNotifications):
    def __init__(self):
        super().__init__(webhook_url, BASE / "records/notifications.jsonl")


class Monitor:
    def __init__(self, *, root=BASE, live=False, source=None, executor=None,
                 notify=None, clock=now_local, calendar_path=None):
        self.root, self.live, self.clock = Path(root), live, clock
        self.mode = "live" if live else "shadow"
        self.path = self.root / "runtime" / f"{self.mode}_state.json"
        self.records = self.root / "records" / f"{self.mode}_events.csv"
        self.calendar_path = Path(calendar_path or os.getenv("EF_HEDGE_CALENDAR_PATH")
                                  or BASE / "config/calendar.json")
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if self.state and self.state.get("mode") != self.mode:
            raise ValueError("實單與模擬狀態不可混用")
        if self.state and self.state.get("strategy") != "pure_ef_morning_flat_v1":
            raise ValueError("偵測到舊避險狀態；請先核對並平掉永豐2舊部位，再封存 runtime 狀態後啟動新策略")
        self.state["mode"] = self.mode
        self.state["strategy"] = "pure_ef_morning_flat_v1"
        started = self.clock()
        calendar = Calendar.load(self.calendar_path)
        closure = latest_closure(calendar, started)
        previous_attempt = self.state.pop("attempt", None)
        if previous_attempt:
            self.event("startup_discard_previous_attempt", previous=previous_attempt)
        signal_path = Path(os.getenv("EF_HEDGE_SIGNAL_CSV") or BACKEND / "tv_doc/six_strategy_signal_events.csv")
        if signal_path.exists():
            with signal_path.open(encoding="utf-8-sig", newline="") as handle:
                self.state["startup_signal_rows"] = sum(1 for _ in csv.DictReader(handle))
        else:
            self.state["startup_signal_rows"] = 0
        self.state["boot"] = started.isoformat()
        self.state["ready_since"] = started.isoformat()
        self.state.pop("source", None)
        self.state["session_flat"] = False
        # Starting/restarting is never a catch-up or liquidation trigger.
        self.state["flat_cycle"] = closure.start.isoformat() if closure else "initial"
        if day_time(4, 59) <= started.time() < day_time(5):
            self.state["flat_cycle"] = None
        self.persist()
        self.source = source or self.read_source
        self.execute = executor or execute_target_position
        self.notify = notify or (lambda message: None)
        self.last_alert = None

    def persist(self):
        save(self.path, self.state)

    def event(self, kind: str, **data):
        self.records.parent.mkdir(parents=True, exist_ok=True)
        header = not self.records.exists()
        with self.records.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if header:
                writer.writerow(["time", "mode", "event", "details"])
            writer.writerow([self.clock().isoformat(), self.mode, kind,
                             json.dumps(data, ensure_ascii=False)])

    def alert(self, message: str):
        if message != self.last_alert:
            self.event("alert", message=message)
            self.notify(f"[純 EF 04:59 清倉/{self.mode}] {message}")
            self.last_alert = message

    def read_source(self, now: datetime) -> dict:
        calendar = Calendar.load(self.calendar_path)
        closure = latest_closure(calendar, now)
        boot = max(datetime.fromisoformat(self.state["boot"]),
                   datetime.fromisoformat(self.state.get("ready_since", self.state["boot"])))
        since = closure.reopen if closure else boot.replace(microsecond=0)
        if self.state.get("session_flat"):
            since = max(since, datetime.fromisoformat(self.state["ready_since"]))
        path = Path(os.getenv("EF_HEDGE_SIGNAL_CSV") or BACKEND / "tv_doc/six_strategy_signal_events.csv")
        return pure_position(path, now, since, calendar,
                             integer(os.getenv("EF_HEDGE_SOURCE_UNIT", "1")), boot,
                             initial_from_signal=not self.state.get("session_flat", False),
                             start_index=self.state["startup_signal_rows"])

    def action(self, key: str, target: int | None, contract: str, deadline: datetime,
               delta: int | None = None) -> bool:
        attempt = self.state.get("attempt", {})
        if attempt.get("status") in {"pending", "failed"}:
            # Persisted time also throttles recovery after a process restart.
            retry_at = datetime.fromisoformat(attempt.get("retry_at") or attempt["at"])
            if "retry_at" not in attempt:
                retry_at += timedelta(seconds=RETRY_SECONDS)
            urgent_flat = key.endswith("/flat") and attempt.get("key") != key
            if self.clock() < retry_at and not urgent_flat:
                return False
            append_order(self.root / "records" / f"{self.mode}_order_attempts.csv",
                         clock=self.clock, attempt_id=attempt.get("id", ""),
                         event="automatic_reconcile", trigger=key, target_position=target,
                         detail="前次未確認；重新查券商庫存與未結委託，依待處理訊號計算差額")
            # perform_order persists the new intent before executing. The executor
            # always checks broker orders/inventory before submitting any delta.
            if attempt.get("key") == key and attempt.get("target") is not None:
                target, delta = attempt["target"], None
            self.state.pop("attempt")
        elif attempt.get("key") == key and attempt.get("status") == "done":
            return True
        def record(**row):
            append_order(self.root / "records" / f"{self.mode}_order_attempts.csv",
                         clock=self.clock, **row)
        try:
            if self.live:
                if delta is None:
                    result = perform_order(
                        self.state, key=key, target=target, persist=self.persist,
                        execute=lambda: self.execute(target, deadline=deadline, clock=self.clock),
                        record=record, clock=self.clock,
                    )
                else:
                    current = {"key": key, "id": uuid.uuid4().hex, "status": "pending",
                               "target": None, "delta": delta, "at": self.clock().isoformat()}
                    self.state["attempt"] = current
                    self.persist()
                    def resolve_target(value):
                        current["target"] = value
                        self.persist()
                        record(attempt_id=current["id"], event="attempt_started", trigger=key,
                               target_position=value, detail=f"新訊號差額 {delta}；已保存券商對帳目標")
                    result = self.execute(None, delta=delta, on_target=resolve_target,
                                          deadline=deadline, clock=self.clock)
                    target = current["target"]
                    if result.actual_position != target:
                        raise RuntimeError("券商實際部位未達目標")
                    record(attempt_id=current["id"], event="order_sent_confirmed", trigger=key,
                           target_position=target, previous_position=result.previous_position,
                           actual_position=result.actual_position, side=result.side,
                           quantity=result.quantity, detail=result_text(result))
                    current["status"] = "done"
                    self.persist()
                actual, quantity = result.actual_position, result.quantity
                detail = result_text(result)
            else:
                target = self.state.get("position", 0) + delta if delta is not None else target
                actual = target
                quantity = abs(target - self.state.get("position", 0))
                self.state["attempt"] = {"key": key, "status": "done", "target": target}
                detail = "影子模式，未送實單"
                record(attempt_id=uuid.uuid4().hex, event="shadow_target", trigger=key,
                       target_position=target, quantity=quantity, detail=detail)
            self.last_alert = None
            self.state["position"] = actual
            self.persist()
            self.event("confirmed" if self.live else "shadow_target", target=target,
                       quantity=quantity, contract=contract, key=key)
            self.notify(execution_message("純EF＋04:59清倉", self.mode, key, target,
                                          detail, clock=self.clock))
            return True
        except Exception as exc:
            failed = self.state.get("attempt", {})
            failed["status"] = "failed"
            failed["retry_at"] = (self.clock() + timedelta(seconds=RETRY_SECONDS)).isoformat()
            self.state["attempt"] = failed
            self.persist()
            self.event("order_failed", key=key, error_type=type(exc).__name__)
            self.alert(f"目標 {target} 口委託未確認（{type(exc).__name__}）；"
                       f"持續監控，{RETRY_SECONDS} 秒後自動重新對帳並繼續逐筆處理訊號")
            return False

    def tick(self, now: datetime | None = None):
        now = now or self.clock()
        calendar = Calendar.load(self.calendar_path)
        closure = latest_closure(calendar, now)
        cycle = closure.start.isoformat() if closure else "initial"
        contract = "TMFR1"
        self.state["contract"] = contract
        # Flatten on the clock, before opening/reading any signal source.
        if self.state.get("flat_cycle") != cycle:
            self.state["target"] = 0
            self.persist()
            if not calendar.is_open(now) or now >= self.session_deadline(now):
                self.alert("等待可交易時段補做清倉；清倉確認前不接受新訊號")
                return
            if not self.action(cycle + "/flat", 0, contract, self.session_deadline(now)):
                return
            self.state["flat_cycle"] = cycle
            self.state["ready_since"] = self.clock().isoformat()
            self.state["session_flat"] = True
            self.state.pop("source", None)
            self.persist()
        # Weekends/holidays remain flat; calendar updates are read on every tick.
        if closure and now < closure.reopen:
            return
        if not calendar.is_open(now) or now >= self.session_deadline(now):
            return
        source = self.source(now)
        maximum = integer(os.getenv("EF_HEDGE_MAX_CONTRACTS", "12"))
        if not 0 <= maximum <= 240:
            raise ValueError("純 EF 口數超過 EF_HEDGE_MAX_CONTRACTS；不截斷口數下單")

        def signal_order(value):
            stamp, index = value.rsplit("/", 1)
            return datetime.fromisoformat(stamp), int(index)

        cursor = self.state.get("source", {}).get("last_signal")
        steps = [step for step in source.get("steps", [])
                 if not cursor or signal_order(step["last_signal"]) > signal_order(cursor)]
        # Each accepted event is executed and checkpointed separately. Never
        # collapse a burst of entries/exits into its final net position.
        for step in steps:
            deadline = self.session_deadline(now)
            if deadline.time() == day_time(4, 59, 40):
                deadline = deadline.replace(second=0)
            if self.clock() >= deadline:
                return
            delta = (step["new_position"] - step["previous_position"]) * step["unit"]
            if delta:
                key = f"signal/{step['last_signal']}"
                if not self.action(key, None, contract, deadline, delta=delta):
                    return
            self.state["source"] = step
            self.persist()

    @staticmethod
    def session_deadline(now: datetime) -> datetime:
        if now.time() < day_time(5):
            return datetime.combine(now.date(), day_time(4, 59, 40))
        if now.time() < day_time(13, 45):
            return datetime.combine(now.date(), day_time(13, 44, 40))
        return datetime.combine(now.date() + timedelta(days=1), day_time(4, 59, 40))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="執行一次目前時鐘檢查")
    parser.add_argument("--retry-failed", action="store_true", help="對帳後立即解除重試等待；預設會自動重新對帳")
    args = parser.parse_args()
    load_env(BACKEND / ".env")
    # This entry point always runs account 2 in live trading mode.
    live = True
    (BASE / "runtime").mkdir(parents=True, exist_ok=True)
    # Shared lock across shadow/live prevents mode changes while another monitor runs.
    with FileLock(str(BASE / "runtime/monitor.lock"), timeout=0):
        monitor = Monitor(live=live, notify=Notifications())
        if args.retry_failed and monitor.state.get("attempt", {}).get("status") in {"pending", "failed"}:
            monitor.event("operator_retry", previous=monitor.state["attempt"])
            append_order(BASE / "records/live_order_attempts.csv", event="operator_retry",
                         attempt_id=uuid.uuid4().hex, trigger="operator_retry", target_position=0)
            monitor.state.pop("attempt")
            monitor.persist()
        startup_message = (
            "✅【開始監控｜永豐2 純EF＋04:59清倉】\n"
            f"時間：{monitor.clock():%Y-%m-%d %H:%M:%S}\n"
            "版本：new-signals-only-v2；每次啟動只處理啟動後新訊號。\n"
            "04:59清倉；08:45不恢復舊部位，等待新EF訊號；週末與連假保持空手。\n"
            f"模式：{'API_KEY2 永豐實單' if live else 'shadow（僅記錄目標，不實際下單）'}。\n"
            "啟動不下單、不補舊訊號；收到新訊號才下單，04:59清倉。"
        )
        print(startup_message, flush=True)
        monitor.notify(startup_message)
        if not webhook_url():
            print("未設定 DISCORD_EF_hedge_WEBHOOK_URL，無法發送 Discord 通知", flush=True)
        while True:
            try:
                monitor.tick()
            except Exception as exc:
                # No raw broker exceptions or credentials in logs/Discord.
                detail = str(exc)[:500] if isinstance(exc, (ValueError, FileNotFoundError)) else type(exc).__name__
                monitor.alert(f"監控檢查失敗：{detail}；請檢查日曆、來源與設定")
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                monitor.notify.flush()
                print(json.dumps(monitor.state, ensure_ascii=False, indent=2))
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
