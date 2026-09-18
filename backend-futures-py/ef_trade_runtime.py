"""Execution lifecycle, audit schema and Discord transport shared by both EF strategies."""
from __future__ import annotations

import csv
import json
import os
import queue
import threading
import time as timer
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ORDER_FIELDS = [
    "timestamp", "attempt_id", "event", "trigger", "target_position",
    "previous_position", "actual_position", "side", "quantity", "detail",
]


def save_state(path, value):
    """Durable atomic replacement with bounded retries for OneDrive locks."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(8):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            return True
        except OSError:
            if attempt < 7:
                timer.sleep(0.05 * (attempt + 1))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return False


def now_local():
    return datetime.now(ZoneInfo("Asia/Taipei")).replace(tzinfo=None)


def order_deadline(now, target):
    if now.time() < time(5):
        return datetime.combine(now.date(), time(4, 59, 40) if target == 0 else time(4, 59))
    if now.time() < time(13, 45):
        return datetime.combine(now.date(), time(13, 44, 40))
    if now.time() < time(15):
        return now  # No order during the afternoon break.
    return datetime.combine(now.date() + timedelta(days=1), time(4, 59, 40) if target == 0 else time(4, 59))


def check_order_deadline(deadline, clock, error_type):
    now = clock()
    if (now >= deadline or time(5) <= now.time() < time(8, 45)
            or time(13, 45) <= now.time() < time(15)):
        raise error_type("已超過本次下單期限，不追補休市前委託")


def append_order(path, *, clock=now_local, **row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ORDER_FIELDS)
        if header:
            writer.writeheader()
        writer.writerow({field: row.get(field, clock().strftime("%Y-%m-%d %H:%M:%S")
                                       if field == "timestamp" else "") for field in ORDER_FIELDS})


def position_text(value):
    return "空手" if value == 0 else f"{'多' if value > 0 else '空'}{abs(value)}口"


def result_text(result):
    if not getattr(result, "confirmed", True):
        return (f"已呼叫{'買進' if result.side == 'buy' else '賣出'} TMF {result.quantity}口委託；"
                "API已返回，未回查成交，不自動重送")
    if result.quantity:
        return (f"✅ 已送{'買進' if result.side == 'buy' else '賣出'} TMF {result.quantity}口；"
                f"實際部位{position_text(result.previous_position)} → "
                f"{position_text(result.actual_position)}（已回查確認）")
    return f"帳戶已是{position_text(result.actual_position)}，無需送單"


def execution_message(label, mode, trigger, target, detail, *, clock=now_local):
    return (f"🚨【委託結果｜{label}】\n時間：{clock():%Y-%m-%d %H:%M:%S}\n"
            f"策略目標部位：{position_text(target)}\n"
            f"模式：{mode}\n觸發：{trigger}\n執行：{detail}")


def perform_order(state, *, key, target, persist, execute, record, clock=now_local,
                  execute_checkpointed=None):
    """Persist intent before calling the broker; uncertain attempts require reconciliation."""
    if state.get("attempt", {}).get("status") in {"pending", "failed"}:
        raise RuntimeError("仍有未確認委託；請對帳後使用 --retry-failed")
    attempt = {"key": key, "id": uuid.uuid4().hex, "status": "pending",
               "target": target, "at": clock().isoformat()}
    state["attempt"] = attempt
    def event(kind, **data):
        record(attempt_id=attempt["id"], event=kind, trigger=key,
               target_position=target, **data)
    completed = False
    def checkpoint(result):
        nonlocal completed
        if completed:
            return
        confirmed = getattr(result, "confirmed", True)
        if confirmed and result.actual_position != target:
            raise RuntimeError("券商實際部位未達目標")
        event(("order_sent_confirmed" if confirmed else "submitted") if result.quantity else "no_order_needed",
              previous_position=result.previous_position, actual_position=result.actual_position,
              side=result.side or "", quantity=result.quantity, detail=result_text(result))
        attempt["status"] = "done" if confirmed else "submitted"
        if persist() is False:
            raise OSError("送單後狀態無法安全寫入，請對帳")
        completed = True
    try:
        if persist() is False:
            raise OSError("狀態檔無法安全寫入，基於安全未送單")
        event("attempt_started", detail="準備登入永豐、查詢TMF部位並對帳")
        result = execute_checkpointed(checkpoint) if execute_checkpointed else execute()
        checkpoint(result)
        return result
    except Exception as exc:
        attempt["status"] = "failed"
        attempt["error_type"] = type(exc).__name__
        persist()
        event("failed", detail=str(exc) if type(exc).__name__ == "BrokerOrderError" else type(exc).__name__)
        raise


class Notifications:
    """Same bounded asynchronous transport and delivery audit for both accounts."""
    def __init__(self, webhook, records, *, post=None):
        self.webhook, self.records, self.post = webhook, Path(records), post
        self.messages = queue.Queue(maxsize=100)
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    def audit(self, status, content):
        try:
            self.records.parent.mkdir(parents=True, exist_ok=True)
            with self.records.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"timestamp": now_local().isoformat(),
                                         "status": status, "content": content}, ensure_ascii=False) + "\n")
        except OSError:
            print("Discord 通知紀錄寫入失敗", flush=True)

    def __call__(self, message):
        try:
            self.messages.put_nowait(message)
            return True
        except queue.Full:
            self.audit("queue_full", message)
            print("Discord 通知佇列已滿", flush=True)
            return False

    def worker(self):
        import requests
        while True:
            message = self.messages.get()
            try:
                url = self.webhook()
                if not url:
                    self.audit("missing_webhook", message)
                    continue
                for offset in range(0, len(message), 1900):
                    response = (self.post or requests.post)(
                        url, params={"wait": "true"},
                        json={"username": "NotifierBot", "content": message[offset:offset + 1900]}, timeout=10)
                    response.raise_for_status()
                self.audit("sent", message)
            except Exception:
                self.audit("failed", message)
                print("Discord 通知失敗，詳情請查看本機紀錄", flush=True)
            finally:
                self.messages.task_done()

    def flush(self):
        self.messages.join()
