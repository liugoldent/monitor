"""Pure EF account 2: 04:59 flat, wait for new signals after 08:45."""
from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import threading
import time
import uuid
from datetime import datetime, time as day_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock

from auto_trade import execute_target_position
from strategy import Calendar, integer, latest_closure, pure_position

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent


def now_local() -> datetime:
    return datetime.now(ZoneInfo("Asia/Taipei")).replace(tzinfo=None)


def load_env(path: Path) -> None:
    if path.exists():
        from dotenv import load_dotenv
        load_dotenv(path, override=False)


def flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def webhook_url() -> str:
    return os.getenv("DISCORD_EF_hedge_WEBHOOK_URL", "").strip()


class Notifications:
    """Network notifications cannot delay the 04:59 order deadline."""
    def __init__(self):
        self.messages = queue.Queue(maxsize=100)
        threading.Thread(target=self.worker, daemon=True).start()

    def __call__(self, message: str):
        if webhook_url():
            try:
                self.messages.put_nowait(message)
            except queue.Full:
                print("Discord 通知佇列已滿，詳情請查看本機紀錄", flush=True)

    def worker(self):
        import requests
        while True:
            message = self.messages.get()
            try:
                response = requests.post(webhook_url(), json={"content": message[:1900]},
                                         timeout=10)
                response.raise_for_status()
            except Exception:
                # Requests exceptions can contain the secret webhook URL.
                print("Discord 通知失敗，詳情請查看本機紀錄", flush=True)
            finally:
                self.messages.task_done()


class Monitor:
    def __init__(self, *, root=BASE, live=False, source=None, executor=None,
                 notify=None, clock=now_local, calendar_path=None):
        self.root, self.live, self.clock = Path(root), live, clock
        self.mode = "live" if live else "shadow"
        self.path = self.root / "runtime" / f"{self.mode}_state.json"
        self.records = self.root / "records" / f"{self.mode}_events.csv"
        self.calendar_path = Path(calendar_path or os.getenv("EF_HEDGE_CALENDAR_PATH")
                                  or BASE / "config/calendar.json")
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        if self.state and self.state.get("mode") != self.mode:
            raise ValueError("實單與模擬狀態不可混用")
        if self.state and self.state.get("strategy") != "pure_ef_morning_flat_v1":
            raise ValueError("偵測到舊避險狀態；請先核對並平掉永豐2舊部位，再封存 runtime 狀態後啟動新策略")
        self.state["mode"] = self.mode
        self.state["strategy"] = "pure_ef_morning_flat_v1"
        if "boot" not in self.state:
            self.state["boot"] = self.clock().isoformat()
            self.persist()
        self.source = source or self.read_source
        self.execute = executor or execute_target_position
        self.notify = notify or (lambda message: None)
        self.last_alert = None
        self.last_audit = None

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
        since = closure.reopen if closure else boot
        path = Path(os.getenv("EF_HEDGE_SIGNAL_CSV") or BACKEND / "tv_doc/six_strategy_signal_events.csv")
        return pure_position(path, now, since, calendar,
                             integer(os.getenv("EF_HEDGE_SOURCE_UNIT", "1")), boot)

    def action(self, key: str, target: int, contract: str, deadline: datetime) -> bool:
        attempt = self.state.get("attempt", {})
        if attempt.get("key") == key:
            if attempt.get("status") == "done":
                return True
            self.alert("上次委託失敗或結果不明，已停止本動作重送；請對帳後使用 --retry-failed")
            return False
        # Persist before any network call. A crash never silently repeats an order.
        self.state["attempt"] = {"key": key, "status": "pending", "target": target,
                                 "contract": contract, "at": self.clock().isoformat()}
        self.persist()
        self.event("intent", **self.state["attempt"])
        try:
            if self.live:
                result = self.execute(target, deadline=deadline, clock=self.clock)
                actual, quantity = result.actual_position, result.quantity
                if actual != target:
                    raise ValueError("券商實際部位未達目標")
            else:
                actual = target
                quantity = abs(target - self.state.get("position", 0))
            self.state["position"] = actual
            self.state["attempt"]["status"] = "done"
            self.persist()
            self.event("confirmed" if self.live else "shadow_target", target=target,
                       quantity=quantity, contract=contract, key=key)
            if quantity:
                self.notify(f"[純 EF 04:59 清倉/{self.mode}] {key}\n"
                            f"第二帳戶目標 {target:+d} 口，異動 {quantity} 口；合約 {contract or '未設定'}")
            return True
        except Exception as exc:
            self.state["attempt"]["status"] = "failed"
            self.state["attempt"]["error_type"] = type(exc).__name__
            self.persist()
            self.event("order_failed", key=key, error_type=type(exc).__name__)
            self.alert(f"{key} 委託未確認（{type(exc).__name__}），請查券商庫存與委託")
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
            self.state["ready_since"] = now.isoformat()
            self.persist()
            self.last_audit = now
        # Weekends/holidays remain flat; calendar updates are read on every tick.
        if closure and now < closure.reopen:
            return
        if not calendar.is_open(now) or now >= self.session_deadline(now):
            return
        if self.state.get("attempt", {}).get("status") in {"pending", "failed"}:
            self.alert("仍有未確認委託；請對帳後使用 --retry-failed")
            return
        source = self.source(now)
        target = integer(source["net_position"])
        maximum = integer(os.getenv("EF_HEDGE_MAX_CONTRACTS", "12"))
        if not 0 <= maximum <= 240 or abs(target) > maximum:
            raise ValueError("純 EF 口數超過 EF_HEDGE_MAX_CONTRACTS；不截斷口數下單")
        audit = self.last_audit is None or (now - self.last_audit).total_seconds() >= 300
        if target != self.state.get("target", 0) or audit:
            deadline = self.session_deadline(now)
            # Entry must stop exactly at 04:59, even if CSV reading/login is slow.
            if deadline.time() == day_time(4, 59, 40):
                deadline = deadline.replace(second=0)
            if self.clock() >= deadline:
                return
            key = f"signal/{now.isoformat()}/{target}"
            if self.action(key, target, contract, deadline):
                self.state["target"] = target
                self.state["source"] = source
                self.persist()
                self.last_audit = now

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
    parser.add_argument("--retry-failed", action="store_true", help="對帳後解除失敗動作鎖定；執行器仍檢查未結束委託")
    args = parser.parse_args()
    load_env(BACKEND / ".env")
    live = flag("EF_PURE_FLAT_ENABLE_ORDERS")
    (BASE / "runtime").mkdir(parents=True, exist_ok=True)
    # Shared lock across shadow/live prevents mode changes while another monitor runs.
    with FileLock(str(BASE / "runtime/monitor.lock"), timeout=0):
        monitor = Monitor(live=live, notify=Notifications())
        if args.retry_failed and monitor.state.get("attempt", {}).get("status") in {"pending", "failed"}:
            monitor.event("operator_retry", previous=monitor.state["attempt"])
            monitor.state.pop("attempt")
            monitor.persist()
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
                print(json.dumps(monitor.state, ensure_ascii=False, indent=2))
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
