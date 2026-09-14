"""Wall-clock hedge monitor. Default mode records targets without placing orders."""
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
from strategy import Calendar, hedge_target, integer, signal_position, snapshot_position

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
        self.state["mode"] = self.mode
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
            self.notify(f"[EF 凌晨與週末避險/{self.mode}] {message}")
            self.last_alert = message

    def read_source(self, now: datetime) -> dict:
        snapshot = os.getenv("EF_HEDGE_SOURCE_SNAPSHOT", "").strip()
        if snapshot:
            return snapshot_position(Path(snapshot), now)
        if self.live and not flag("EF_HEDGE_ACCEPT_SIGNAL_ESTIMATE"):
            raise ValueError("尚未確認使用訊號推算庫存；請提供群益快照或明確啟用推算模式")
        path = Path(os.getenv("EF_HEDGE_SIGNAL_CSV") or BACKEND / "tv_doc/six_strategy_signal_events.csv")
        result = signal_position(path, now, integer(os.getenv("EF_HEDGE_SOURCE_UNIT", "1")))
        result["contract"] = os.getenv("EF_HEDGE_CONTRACT", "").strip()
        return result

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
                result = self.execute(target, contract_code=contract, deadline=deadline, clock=self.clock)
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
                self.notify(f"[EF 凌晨與週末避險/{self.mode}] {key}\n"
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
        cycle = self.state.get("cycle")
        if cycle and not cycle.get("released"):
            reopen = datetime.fromisoformat(cycle["reopen"])
            # Re-read calendar on every tick: an added typhoon closure postpones release.
            if now >= reopen and calendar.is_open(now):
                deadline = self.session_deadline(now)
                if self.action(cycle["start"] + "/release", 0, cycle["contract"], deadline):
                    cycle["released"] = True
                    self.persist()
                    self.last_audit = now
            elif (self.state.get("attempt", {}).get("status") != "done" or
                  self.state.get("attempt", {}).get("key") != cycle["start"] + "/enter"):
                self.alert("本次避險建倉尚未確認；休市期間不補單，開市後會嘗試解除實際庫存")
            return

        closure = calendar.closure(now.date(),
                                   integer(os.getenv("EF_HEDGE_WEEKDAY_CAP", "2")),
                                   integer(os.getenv("EF_HEDGE_HOLIDAY_CAP", "1")))
        if closure and closure.start <= now < closure.reopen:
            start = closure.start.isoformat()
            if self.state.get("missed") == start:
                return
            deadline = closure.start + timedelta(seconds=40)
            if now >= deadline:
                self.state["missed"] = start
                self.persist()
                self.alert(f"錯過 {start} 建倉期限，本次不補建避險單")
                return
            if self.state.get("attempt", {}).get("status") in {"pending", "failed"}:
                self.alert("仍有未確認委託；請對帳後使用 --retry-failed")
                return
            source = self.source(now)
            target = hedge_target(source["net_position"], closure.cap)
            maximum = integer(os.getenv("EF_HEDGE_MAX_CONTRACTS", "12"))
            if not 0 <= maximum <= 240 or abs(target) > maximum:
                raise ValueError("避險口數超過 EF_HEDGE_MAX_CONTRACTS；不截斷口數下單")
            contract = source.get("contract", "")
            if self.live and not contract:
                raise ValueError("缺少與群益相同月份的實際 TMF 合約代碼")
            self.state["cycle"] = {"start": start, "reopen": closure.reopen.isoformat(),
                                   "cap": closure.cap, "source": source, "target": target,
                                   "contract": contract, "released": False}
            self.persist()
            self.event("closure", **self.state["cycle"])
            self.notify(f"[EF 凌晨與週末避險/{self.mode}] 群益 EF {source['net_position']:+d} 口"
                        f"（{source['source']}）\n避險目標 {target:+d} 口，淨留最多 {closure.cap} 口"
                        f"\n預計解除 {closure.reopen:%Y-%m-%d %H:%M}")
            self.action(start + "/enter", target, contract, deadline)
            return

        # Dedicated account should be flat outside closure windows, including restart.
        if calendar.is_open(now) and (self.last_audit is None or
                                      (now - self.last_audit).total_seconds() >= 300):
            contract = (cycle or {}).get("contract") or os.getenv("EF_HEDGE_CONTRACT", "").strip()
            key = "flat-audit/" + now.isoformat()
            attempt = self.state.get("attempt", {})
            if attempt.get("status") in {"pending", "failed"}:
                self.alert("仍有未確認委託；請對帳後使用 --retry-failed")
                return
            self.action(key, 0, contract, self.session_deadline(now))
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
    live = flag("EF_HEDGE_ENABLE_ORDERS")
    (BASE / "runtime").mkdir(parents=True, exist_ok=True)
    # Shared lock across shadow/live prevents mode changes while another monitor runs.
    with FileLock(str(BASE / "runtime/monitor.lock"), timeout=0):
        monitor = Monitor(live=live, notify=Notifications())
        if args.retry_failed and monitor.state.get("attempt", {}).get("status") in {"pending", "failed"}:
            monitor.event("operator_retry", previous=monitor.state["attempt"])
            monitor.state.pop("attempt")
            cycle = monitor.state.get("cycle")
            if cycle and not cycle.get("released") and now_local() < datetime.fromisoformat(cycle["reopen"]):
                # Never recreate an entry late. Re-entry is allowed only inside its deadline.
                if now_local() < datetime.fromisoformat(cycle["start"]) + timedelta(seconds=40):
                    monitor.state.pop("cycle")
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
