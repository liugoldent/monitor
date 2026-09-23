"""Pure EF account 2: 01:00 flat, wait for new signals after 08:45."""
from __future__ import annotations

import argparse
import csv
import faulthandler
import json
import os
import sys
import time
import uuid
from datetime import datetime, time as day_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock

from auto_trade import (broker_error_summary, confirm_flat, execute_target_position,
                        initialize_broker_session)
from strategy import Calendar, STRATEGIES, integer, latest_closure, pure_position

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
POSITION_UNIT_ENV = "EF_MORNING_WEEKEND_HEDGE_UNIT"
MAX_POSITION_UNIT = 20
MAX_BASE_POSITION = 1
sys.path.insert(0, str(BACKEND))
from ef_trade_runtime import Notifications as SharedNotifications, append_order, save_state


def now_local() -> datetime:
    return datetime.now(ZoneInfo("Asia/Taipei")).replace(tzinfo=None)


def load_env(path: Path) -> None:
    if path.exists():
        from dotenv import load_dotenv
        load_dotenv(path, override=False)


def reload_env(path: Path) -> None:
    if path.exists():
        from dotenv import load_dotenv
        load_dotenv(path, override=True)


def env_stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def wait_for_broker_session(env_path: Path, notify) -> None:
    """Fail closed without consuming signals until broker credentials change."""
    stamp = env_stamp(env_path)
    while True:
        try:
            initialize_broker_session()
            return
        except Exception as exc:
            summary = broker_error_summary(exc)
            message = (f"🚨【永豐2】Shioaji 啟動登入失敗：{summary}。"
                       "服務安全待命，不讀取或消耗新訊號；更新 .env 憑證後將重新登入。")
            print(message, file=sys.stderr, flush=True)
            notify(message)
        # Keep the container healthy and quiet instead of entering Docker's
        # rapid restart loop. Retry only after the bind-mounted file changes.
        while env_stamp(env_path) == stamp:
            time.sleep(5)
        stamp = env_stamp(env_path)
        reload_env(env_path)


def flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def save(path: Path, data: dict) -> None:
    if not save_state(path, data):
        raise OSError("狀態檔無法安全寫入，基於安全未送單")


def webhook_url() -> str:
    return os.getenv("DISCORD_EF_hedge_WEBHOOK_URL", "").strip()


def position_unit() -> int:
    try:
        unit = integer(os.getenv(POSITION_UNIT_ENV, "1"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{POSITION_UNIT_ENV}必須是1到{MAX_POSITION_UNIT}的整數") from exc
    if not 1 <= unit <= MAX_POSITION_UNIT:
        raise ValueError(f"{POSITION_UNIT_ENV}必須是1到{MAX_POSITION_UNIT}的整數")
    return unit


class Notifications(SharedNotifications):
    def __init__(self):
        super().__init__(webhook_url, BASE / "records/notifications.jsonl")


def signal_message(step: dict) -> str:
    code = step["strategy_code"]
    name = step.get("strategy_name") or code
    previous = step.get("signal_previous_position", step.get("previous_position", 0))
    new = step["new_position"]
    actions = {
        (0, 1): "多單進場", (0, -1): "空單進場",
        (1, 0): "多單出場", (-1, 0): "空單出場",
        (1, -1): "多單出場 → 空單進場", (-1, 1): "空單出場 → 多單進場",
    }
    action = actions.get((previous, new), "部位不變")
    return (f"來源策略：{name} ({code})\n"
            f"訊號動作：{action}（{previous} → {new}）")


def position_text(position: int) -> str:
    if position > 0:
        return f"多 {position} 口"
    if position < 0:
        return f"空 {abs(position)} 口"
    return "空手 0 口"


def order_text(side: str | None, quantity: int) -> str:
    if not quantity:
        return "無需下單"
    return f"{'買進' if side == 'buy' else '賣出'} {quantity} 口"


class Monitor:
    def __init__(self, *, root=BASE, live=False, source=None, executor=None,
                 notify=None, clock=now_local, calendar_path=None, flat_checker=None):
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
        # Preserve the completed daily reset when upgrading the schedule mid-session.
        # A different clock on the same cycle must not erase today's positions.
        for field in ("last_reset_cycle", "flat_cycle"):
            value = self.state.get(field)
            if isinstance(value, str) and "T04:59:00" in value:
                self.state[field] = value.replace("T04:59:00", "T01:00:00")
        started = self.clock()
        calendar = Calendar.load(self.calendar_path)
        closure = latest_closure(calendar, started)
        self.flat_checker = flat_checker or (confirm_flat if live else lambda: True)
        # One-time v4 migration takes the saved snapshot, never replays CSV.
        if "positions" not in self.state:
            self.state["positions"] = self.state.get("day_signal_positions", dict.fromkeys(STRATEGIES, 0)).copy()
            self.state["last_reset_cycle"] = self.state.get("flat_cycle") or (closure.start.isoformat() if closure else "initial")
            self.state["migration"] = "v5_from_saved_snapshot"
        # Upgrade the former 12-leg snapshot without changing tracked holdings
        # or replaying old signals. This new leg has no orders tracked yet.
        if set(self.state["positions"]) == set(STRATEGIES) - {"CFCTX15m"}:
            self.state["positions"]["CFCTX15m"] = 0
        if set(self.state["positions"]) != set(STRATEGIES):
            raise ValueError(f"JSON positions 必須包含全部{len(STRATEGIES)}個策略")
        for code, value in self.state["positions"].items():
            if integer(value) not in {-1, 0, 1}:
                raise ValueError(f"JSON {code} 部位必須是 -1/0/1")
            self.state["positions"][code] = integer(value)
        self.state["schema_version"] = 5
        if self.state.get("attempt", {}).get("status") == "attempted":
            self.state["attempt"]["status"] = "interrupted_no_retry"
            self.state["attempt"]["interrupted_at"] = started.isoformat()
        self.state.pop("blocked_reason", None)
        self.state.pop("blocked_signal_cursor", None)
        signal_path = Path(os.getenv("EF_HEDGE_SIGNAL_CSV") or BACKEND / "tv_doc/six_strategy_signal_events.csv")
        if signal_path.exists():
            with signal_path.open(encoding="utf-8-sig", newline="") as handle:
                self.state["startup_signal_rows"] = sum(1 for _ in csv.DictReader(handle))
        else:
            self.state["startup_signal_rows"] = 0
        self.state["boot"] = started.isoformat()
        self.state["ready_since"] = started.isoformat()
        # Keep the submission checkpoint across a crash/restart.
        self.state.setdefault("session_flat", False)
        # Starting/restarting is never a catch-up or liquidation trigger.
        self.state["flat_cycle"] = closure.start.isoformat() if closure else "initial"
        if day_time(1, 0) <= started.time() < day_time(5):
            self.state["flat_cycle"] = None
        self.persist()
        self.source = source or self.read_source
        self.execute = executor or execute_target_position
        self.notify = notify or (lambda message: None)
        self.last_alert = None

    def persist(self):
        # Legacy positions are read only during migration in __init__.
        # Keep one authoritative position map and only the signal checkpoint.
        self.state.pop("day_signal_positions", None)
        if isinstance(self.state.get("source"), dict):
            self.state["source"] = {
                key: value for key, value in self.state["source"].items()
                if key not in {"positions", "net_position"}
            }
        save(self.path, self.state)

    def target_heading(self, step: dict | None = None) -> str:
        base_net = sum(self.state["positions"].values()) if step is not None else 0
        base_target = 1 if base_net > 0 else -1 if base_net < 0 else 0
        target = base_target * position_unit()
        position = f"{'多' if target > 0 else '空'}{abs(target)}口" if target else "空手（0口）"
        return (f"[13策略淨方向：{base_net:+d}｜Clamp目標：{position}"
                f"｜目前U={position_unit()}]\n")

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
            self.notify(f"[純 EF 01:00 清倉/{self.mode}] {message}")
            self.last_alert = message

    def read_source(self, now: datetime) -> dict:
        calendar = Calendar.load(self.calendar_path)
        # Day and night are one session, including the hours after midnight.
        day = now.date() if now.time() >= day_time(8, 45) else now.date() - timedelta(days=1)
        since = datetime.combine(day, day_time(8, 45))
        self.state["trading_day"] = day.isoformat()
        path = Path(os.getenv("EF_HEDGE_SIGNAL_CSV") or BACKEND / "tv_doc/six_strategy_signal_events.csv")
        # CSV supplies new events only; JSON positions are authoritative.
        # Source legs always remain unscaled -1/0/+1 directions.  The account
        # target is scaled exactly once after the net direction is clamped to
        # -1/0/+1.
        return pure_position(path, now, since, calendar, 1,
                             start_index=self.state["startup_signal_rows"])

    def reset_if_due(self, now, closure):
        if not closure or now < closure.start.replace(hour=5, minute=5):
            return True
        cycle = closure.start.isoformat()
        if self.state.get("last_reset_cycle") == cycle:
            return True
        # Retry the read-only check at most once a minute; never send a retry order.
        retry = self.state.get("reset_check_after")
        if retry and now < datetime.fromisoformat(retry) and now < closure.reopen:
            return False
        self.state["reset_check_after"] = (now + timedelta(minutes=1)).isoformat()
        self.persist()
        confirmed = True
        try:
            if not self.flat_checker():
                raise ValueError("永豐2仍有TMF庫存")
        except Exception as exc:
            confirmed = False
            self.state["reset_status"] = "blocked"
            self.state["manual_flat_required"] = {"cycle": cycle, "reason": type(exc).__name__}
            self.persist()
            self.alert(f"🚨 清倉未確認完成（{type(exc).__name__}），請早上核對永豐2庫存並手動清倉；"
                       "不自動重送。開盤後策略部位歸零，照常接收新EF訊號，不因本次清倉失敗暫停。")
            if now < closure.reopen:
                return False
        previous = self.state["positions"].copy()
        self.state["positions"] = dict.fromkeys(STRATEGIES, 0)
        self.state["last_reset_cycle"] = cycle
        self.state["reset_status"] = "confirmed_flat" if confirmed else "manual_flat_required"
        self.state["reset_at"] = now.isoformat()
        # Signals received while reset was blocked must not become catch-up orders.
        path = Path(os.getenv("EF_HEDGE_SIGNAL_CSV") or BACKEND / "tv_doc/six_strategy_signal_events.csv")
        if confirmed and path.exists():
            with path.open(encoding="utf-8-sig", newline="") as handle:
                self.state["startup_signal_rows"] = sum(1 for _ in csv.DictReader(handle))
        self.state["flat_cycle"] = cycle
        flat_attempt = self.state.get("attempt", {}).get("key", "").endswith("/flat")
        if confirmed or flat_attempt:
            self.state.pop("blocked_reason", None)
            if self.state.get("attempt", {}).get("status") == "attempted":
                self.state["attempt"]["status"] = "resolved_by_confirmed_flat" if confirmed else "manual_flat_required"
        if confirmed:
            self.state.pop("manual_flat_required", None)
        self.state.pop("reset_check_after", None)
        self.persist()
        self.event("daily_reset", previous_positions=previous, cycle=cycle, confirmed_flat=confirmed)
        self.notify(f"【永豐2】已確認TMF空手，{len(STRATEGIES)}個策略JSON部位已歸零。" if confirmed else
                    "🚨【永豐2｜人工清倉待辦】券商庫存尚未確認空手，請手動核對並清倉。"
                    f"\n新交易時段已開始，{len(STRATEGIES)}策略JSON已歸零並恢復新訊號；這不代表實際庫存已清空。")
        return True

    def action(self, key: str, target: int, contract: str, deadline: datetime,
               step: dict | None = None) -> bool:
        # Consume before external side effects: timeout/exception must never
        # cause this signal (or this flat cycle) to be submitted a second time.
        if self.state.get("attempt", {}).get("key") == key:
            return True
        if key.endswith("/flat") and self.state.get("last_flat_attempt") == key:
            return True
        attempt = {"key": key, "id": uuid.uuid4().hex, "status": "attempted",
                   "target_position": target, "at": self.clock().isoformat()}
        self.state["attempt"] = attempt
        if step is not None:
            # Commit intent, position and cursor atomically BEFORE broker side effects.
            self.state["positions"][step["strategy_code"]] = step["new_position"]
            self.state["source"] = step
        if key.endswith("/flat"):
            self.state["last_flat_attempt"] = key
        self.persist()
        label = "01:00清倉" if step is None else f"訊號後目標 {target:+d} 口"
        def record(event, **data):
            append_order(self.root / "records" / f"{self.mode}_order_attempts.csv",
                         clock=self.clock, attempt_id=attempt["id"], event=event,
                         trigger=key, **data)
        def prepared(data):
            attempt.update(data)
            attempt["broker_phase"] = "prepared"
            self.persist()
        def checkpoint(result):
            attempt["status"] = "submitted" if result.submitted else "no_order_needed"
            attempt["submission_returned_at"] = self.clock().isoformat()
            for name in ("previous_position", "target_position", "broker_before_position",
                         "broker_trade_id", "broker_status", "broker_deal_quantity"):
                if hasattr(result, name):
                    attempt[name] = getattr(result, name)
            attempt["broker_phase"] = "api_returned"
            self.persist()
            record(attempt["status"], side=result.side or "", quantity=result.quantity,
                   detail=(f"API已返回，委託ID={attempt.get('broker_trade_id') or '未取得'}，"
                           f"狀態={attempt.get('broker_status') or '未知'}；不代表成交確認"))
        try:
            record("submission_attempt", detail=label)
            if self.live:
                kwargs = ({"on_prepared": prepared, "on_submitted": checkpoint}
                          if self.execute is execute_target_position else {})
                result = self.execute(target, deadline=deadline, clock=self.clock, **kwargs)
                for name in ("previous_position", "target_position", "broker_before_position",
                             "broker_trade_id", "broker_status", "broker_deal_quantity"):
                    if hasattr(result, name):
                        attempt[name] = getattr(result, name)
                attempt["broker_before_position"] = getattr(
                    result, "previous_position", attempt.get("broker_before_position"))
                attempt["broker_side"] = result.side
                attempt["broker_quantity"] = result.quantity
                detail = (f"已送出{order_text(result.side, result.quantity)} TMF 委託"
                          if result.submitted else "目前庫存已符合最終口數，無需下單")
                attempt["status"] = "submitted" if result.submitted else "no_order_needed"
                if "submission_returned_at" not in attempt:
                    record(attempt["status"], side=result.side or "", quantity=result.quantity, detail=detail)
            else:
                detail = "影子模式，未送實單"
                attempt["status"] = "shadow"
                record("shadow", detail=label)
        except Exception as exc:
            attempt["status"] = "failed_no_retry"
            detail = f"本次送單失敗或送出結果不明（{type(exc).__name__}）；不重送，繼續等新訊號"
            if step is None:
                detail = f"🚨 清倉失敗或結果未確認（{type(exc).__name__}）；請早上核對永豐2庫存並手動清倉。" \
                         "不自動重送，08:45後新訊號不因本次清倉失敗暫停。"
            record("failed_no_retry", detail=detail)
        self.persist()
        self.event(attempt["status"], key=key, detail=detail, signal=step)
        source_text = signal_message(step) + "\n" if step is not None else ""
        current = attempt.get("broker_before_position")
        current_text = position_text(current) if isinstance(current, int) else "查詢失敗"
        planned = order_text(attempt.get("broker_side"), integer(attempt.get("broker_quantity", 0)))
        heading = "【永豐2｜EF訊號計算】" if step is not None else "【永豐2｜01:00清倉計算】"
        self.notify(self.target_heading(step) + f"{heading}\n{source_text}"
                    f"目前券商庫存：{current_text}\n"
                    f"本次預計下單：{planned}\n"
                    f"收到策略後最終口數：{position_text(target or 0)}\n"
                    f"結果：{detail}\n觸發：{key}")
        return True

    def tick(self, now: datetime | None = None):
        now = now or self.clock()
        calendar = Calendar.load(self.calendar_path)
        closure = latest_closure(calendar, now)
        cycle = closure.start.isoformat() if closure else "initial"
        contract = "TMFR1"
        self.state["contract"] = contract
        # This runs even outside trading hours, and catches a missed 05:05 after restart.
        if not self.reset_if_due(now, closure):
            return
        # Flatten on the clock, before opening/reading any signal source.
        if self.state.get("flat_cycle") != cycle:
            self.state["target"] = 0
            self.persist()
            if not calendar.is_open(now) or now >= self.session_deadline(now):
                self.alert("等待可交易時段送出清倉委託")
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
        def signal_order(value):
            stamp, index = value.rsplit("/", 1)
            return datetime.fromisoformat(stamp), int(index)

        cursor = self.state.get("source", {}).get("last_signal")
        steps = [step for step in source.get("steps", [])
                 if int(step["last_signal"].rsplit("/", 1)[1]) >= self.state["startup_signal_rows"]
                 and (not cursor or signal_order(step["last_signal"]) > signal_order(cursor))]
        # Each accepted event is executed and checkpointed separately. Never
        # collapse a burst of entries/exits into its final net position.
        for step in steps:
            deadline = self.session_deadline(now)
            if deadline.time() == day_time(1, 0, 40):
                deadline = deadline.replace(second=0)
            if self.clock() >= deadline:
                return
            step["previous_position"] = self.state["positions"][step["strategy_code"]]
            delta = step["new_position"] - step["previous_position"]
            projected_base = sum(self.state["positions"].values()) + delta
            clamped_direction = (MAX_BASE_POSITION if projected_base > 0 else
                                 -MAX_BASE_POSITION if projected_base < 0 else 0)
            target = clamped_direction * position_unit()
            key = f"signal/{step['last_signal']}"
            if not self.action(key, target, contract, deadline, step=step):
                return
            self.state["source"] = step
            self.persist()

    @staticmethod
    def session_deadline(now: datetime) -> datetime:
        if now.time() < day_time(5):
            return datetime.combine(now.date(), day_time(1, 0, 40))
        if now.time() < day_time(13, 45):
            return datetime.combine(now.date(), day_time(13, 44, 40))
        return datetime.combine(now.date() + timedelta(days=1), day_time(1, 0, 40))


def main():
    faulthandler.enable(all_threads=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="執行一次目前時鐘檢查")
    args = parser.parse_args()
    env_path = BACKEND / ".env"
    load_env(env_path)
    # This entry point runs account 2 in live mode, but auto_trade.py currently
    # has its api.place_order call explicitly disabled.
    live = True
    (BASE / "runtime").mkdir(parents=True, exist_ok=True)
    # Shared lock across shadow/live prevents mode changes while another monitor runs.
    with FileLock(str(BASE / "runtime/monitor.lock"), timeout=0):
        # Establish one process-long native session before reading/consuming
        # any signal.  A startup failure can therefore never turn a signal
        # into a consumed-but-unsent order attempt.
        notifications = Notifications()
        wait_for_broker_session(env_path, notifications)
        monitor = Monitor(live=live, notify=notifications)
        startup_message = (
            "✅【開始監控｜永豐2 純EF＋01:00清倉】\n"
            f"時間：{monitor.clock():%Y-%m-%d %H:%M:%S}\n"
            f"版本：json-positions-v5-clamp；{len(STRATEGIES)}策略以JSON部位為準，重啟延續、不補舊單。\n"
            "13策略淨方向採Clamp：正數目標多單、負數目標空單、零則空手。\n"
            f"目前U={position_unit()}；最終目標口數＝Clamp方向×U。\n"
            "每筆新訊號：查券商TMF庫存，下單口數＝最終目標口數−目前庫存。\n"
            "05:05確認空手；未清完通知人工處理，開盤重設策略JSON並照常接新訊號。\n"
            "01:00清倉；08:45不恢復舊部位，等待新EF訊號；週末與連假保持空手。\n"
            f"模式：{'API_KEY2 永豐實單' if live else 'shadow（僅記錄目標，不實際下單）'}。\n"
            "新訊號只送一次，不回查成交、不重試；啟動不補單，01:00送一次清倉委託。"
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
