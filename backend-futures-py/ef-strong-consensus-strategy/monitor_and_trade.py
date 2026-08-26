from __future__ import annotations

import csv
import fcntl
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from auto_trade import execute_target_position
from strategy import (
    Decision,
    build_trade_rows,
    evaluate_strategy,
    load_latest_positions,
    load_latest_recorded_close,
    position_text,
    signal_event_key,
    simulated_order_action,
    validate_min_group_net,
    validate_position_unit,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"
SOURCE_PATH = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
PRICE_PATH = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
RECORDS_DIR = BASE_DIR / "records"
POSITION_PATH = RECORDS_DIR / "ef_strong_position.json"
TRADE_PATH = RECORDS_DIR / "ef_strong_trade.csv"
DECISION_PATH = RECORDS_DIR / "ef_strong_decisions.csv"
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "ef_strong_state.json"
LOCK_PATH = RUNTIME_DIR / "ef_strong.lock"
TZ = ZoneInfo("Asia/Taipei")
TRADE_FIELDS = ["timestamp", "action", "side", "price", "pnl", "quantity"]
DECISION_FIELDS = [
    "timestamp",
    "source_events",
    "e_net",
    "f_net",
    "threshold",
    "previous_target",
    "target_position",
    "reason",
]
DISCORD_WEBHOOK_ENV = "DISCORD_EF_STRONG_WEBHOOK_UTL"
ENABLE_ORDERS_ENV = "EF_STRONG_ENABLE_ORDERS"
ORDER_ERROR_NOTIFICATION_INTERVAL_SECONDS = 60
MAX_ORDER_ERROR_NOTIFICATIONS = 5


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def now() -> datetime:
    return datetime.now(TZ)


def now_text() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def save_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def append_csv(path: Path, fields: list[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def read_source_rows() -> list[dict[str, str]]:
    if not SOURCE_PATH.exists():
        return []
    with SOURCE_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def evaluate_records(threshold: int, unit: int) -> Decision:
    return evaluate_strategy(
        load_latest_positions(SOURCE_PATH),
        min_group_net=threshold,
        position_unit=unit,
    )


def latest_open_trade() -> dict[str, str] | None:
    if not TRADE_PATH.exists():
        return None
    open_trade = None
    with TRADE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("action") == "enter":
                open_trade = dict(row)
            elif row.get("action") == "exiting":
                open_trade = None
    return open_trade


def append_trade_transition(previous: int, target: int, observed_at: datetime) -> None:
    open_trade = latest_open_trade()
    previous_entry_price = None
    if open_trade:
        try:
            previous_entry_price = float(open_trade.get("price") or "")
        except ValueError:
            previous_entry_price = None
    price = load_latest_recorded_close(PRICE_PATH, observed_at)
    for row in build_trade_rows(
        timestamp=observed_at.strftime("%Y-%m-%d %H:%M:%S"),
        previous_position=previous,
        target_position=target,
        price=price,
        previous_entry_price=previous_entry_price,
    ):
        append_csv(TRADE_PATH, TRADE_FIELDS, row)


def discord_webhook() -> str:
    return os.getenv(DISCORD_WEBHOOK_ENV, "").strip()


def send_discord(content: str) -> bool:
    webhook = discord_webhook()
    if not webhook:
        print(f"⚠️ 未設定 {DISCORD_WEBHOOK_ENV}")
        return False
    try:
        response = requests.post(
            webhook,
            params={"wait": "true"},
            json={"username": "NotifierBot", "content": content},
            timeout=15,
        )
        response.raise_for_status()
        return bool(response.json().get("id"))
    except (requests.RequestException, ValueError) as exc:
        safe_error = str(exc).replace(webhook, "<Discord webhook>")
        print(f"❌ Discord通知失敗：{safe_error}")
        return False


def decision_text(decision: Decision, threshold: int) -> str:
    if not decision.ready:
        return f"尚未就緒：{decision.reason}；缺少{', '.join(decision.missing_strategies)}"
    return (
        f"E淨部位={decision.e_net}，F淨部位={decision.f_net}，"
        f"強共識門檻={threshold}，目標={position_text(decision.target_position)}；"
        f"{decision.reason}"
    )


def strategy_signal_status_text(
    *,
    trigger: str,
    decision: Decision,
    threshold: int,
    previous_position: int,
    batch_index: int,
    batch_total: int,
) -> str:
    if not decision.ready or decision.target_position is None:
        status = "資料尚未齊全，暫不動作"
        target = "尚未產生"
    elif decision.target_position == previous_position:
        status = f"目標未變，維持{position_text(previous_position)}，無需動作"
        target = position_text(decision.target_position)
    else:
        status = (
            f"目標改變，準備由{position_text(previous_position)}"
            f"調整為{position_text(decision.target_position)}"
        )
        target = position_text(decision.target_position)
    return (
        "📨【策略訊號狀態｜純EF強共識】\n"
        f"時間：{now_text()}\n"
        f"訊號：{trigger}\n"
        f"批次：{batch_index}/{batch_total}\n"
        f"目前策略部位：{position_text(previous_position)}\n"
        f"本次策略目標：{target}\n"
        f"狀態：{status}\n"
        f"判斷：{decision_text(decision, threshold)}\n"
        "備註：此處顯示純EF策略口數；本策略不使用H3。"
    )


def send_strategy_signal_status_notifications(
    new_rows: list[dict[str, str]],
    *,
    decision: Decision,
    threshold: int,
    previous_position: int,
) -> bool:
    """Notify once for every newly received E/F signal, even with no action."""
    if not new_rows:
        return True
    all_delivered = True
    total = len(new_rows)
    for index, row in enumerate(new_rows, start=1):
        trigger = (
            f"{row.get('strategy_code') or row.get('raw_strategy_code') or '未知策略'} "
            f"{row.get('previous_position')}→{row.get('new_position')}"
        )
        message = strategy_signal_status_text(
            trigger=trigger,
            decision=decision,
            threshold=threshold,
            previous_position=previous_position,
            batch_index=index,
            batch_total=total,
        )
        print(message)
        if not send_discord(message):
            all_delivered = False
    return all_delivered


def order_failure_notification_text(
    *,
    failed_at: str,
    target_position: int,
    error_text: str,
    notification_number: int,
) -> str:
    return (
        "❌【實單失敗｜純EF強共識】\n"
        f"時間：{failed_at}\n"
        f"提醒：{notification_number}/{MAX_ORDER_ERROR_NOTIFICATIONS}\n"
        f"目標：{position_text(target_position)}\n"
        f"錯誤：{error_text}\n"
        "結果：本目標不再重送，請人工下單；"
        "策略目標改變後才會再嘗試一次。"
    )


def process_order_failure_reminder(
    state: dict | None = None,
    current_time: datetime | None = None,
) -> bool:
    """Send one due reminder and permanently stop after the fifth notice."""
    active_state = state if state is not None else load_json(STATE_PATH, {})
    failure = active_state.get("active_order_failure")
    if not isinstance(failure, dict):
        return False
    try:
        target_position = int(failure["target_position"])
        notification_count = int(failure.get("notification_count", 0))
    except (KeyError, TypeError, ValueError):
        return False
    if notification_count >= MAX_ORDER_ERROR_NOTIFICATIONS:
        return False
    next_notification_at = failure.get("next_notification_at")
    if not isinstance(next_notification_at, str):
        return False
    try:
        next_time = datetime.strptime(
            next_notification_at,
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=TZ)
    except ValueError:
        return False
    reminder_time = current_time or now()
    if reminder_time < next_time:
        return False

    notification_number = notification_count + 1
    error_text = str(failure.get("error") or "未知錯誤")
    failed_at = str(failure.get("failed_at") or next_notification_at)
    notification_sent = send_discord(
        order_failure_notification_text(
            failed_at=failed_at,
            target_position=target_position,
            error_text=error_text,
            notification_number=notification_number,
        )
    )
    reminder_text = reminder_time.strftime("%Y-%m-%d %H:%M:%S")
    failure["notification_count"] = notification_number
    failure["last_notification_at"] = reminder_text
    if notification_number >= MAX_ORDER_ERROR_NOTIFICATIONS:
        failure["next_notification_at"] = None
        failure["reminders_completed_at"] = reminder_text
        print("實單失敗已提醒5次，停止後續通知，請人工下單")
    else:
        failure["next_notification_at"] = (
            reminder_time
            + timedelta(seconds=ORDER_ERROR_NOTIFICATION_INTERVAL_SECONDS)
        ).strftime("%Y-%m-%d %H:%M:%S")
    active_state["active_order_failure"] = failure
    save_json_atomic(STATE_PATH, active_state)
    return notification_sent


def write_position(decision: Decision, threshold: int, unit: int, trigger: str) -> None:
    save_json_atomic(
        POSITION_PATH,
        {
            "strategy": "EF Strong Consensus",
            "uses_h": False,
            "threshold": threshold,
            "U": unit,
            "e_net": decision.e_net,
            "f_net": decision.f_net,
            "target_position": decision.target_position,
            "ready": decision.ready,
            "reason": decision.reason,
            "trigger": trigger,
            "updated_at": now_text(),
        },
    )


def process_batch(
    state: dict,
    rows: list[dict[str, str]],
    new_rows: list[dict[str, str]],
    threshold: int,
    unit: int,
) -> None:
    decision = evaluate_records(threshold, unit)
    trigger = "、".join(
        f"{row.get('strategy_code') or row.get('raw_strategy_code')} "
        f"{row.get('previous_position')}→{row.get('new_position')}"
        for row in new_rows
    ) or "啟動時重建"
    write_position(decision, threshold, unit, trigger)
    previous = state.get("last_simulated_target")
    if previous is None:
        previous = 0
    previous = int(previous)
    send_strategy_signal_status_notifications(
        new_rows,
        decision=decision,
        threshold=threshold,
        previous_position=previous,
    )
    if not decision.ready or decision.target_position is None:
        print(decision_text(decision, threshold))
        return

    target = decision.target_position
    append_csv(
        DECISION_PATH,
        DECISION_FIELDS,
        {
            "timestamp": now_text(),
            "source_events": trigger,
            "e_net": decision.e_net,
            "f_net": decision.f_net,
            "threshold": threshold,
            "previous_target": previous,
            "target_position": target,
            "reason": decision.reason,
        },
    )
    orders_enabled = env_flag(ENABLE_ORDERS_ENV)
    if target == previous and not orders_enabled:
        print(f"[{now_text()}] {decision_text(decision, threshold)}｜維持不動")
        return

    observed_at = now()
    if orders_enabled:
        last_order_attempt_target = state.get(
            "last_order_attempt_target",
            state.get("last_order_error_target"),
        )
        try:
            normalized_attempt_target = int(last_order_attempt_target)
        except (TypeError, ValueError):
            normalized_attempt_target = None
        if normalized_attempt_target == target:
            print(
                "相同實單目標已嘗試過，不論上次成功或失敗都不重送："
                f"{position_text(target)}"
            )
            return
        attempt_time = now()
        attempt_time_text = attempt_time.strftime("%Y-%m-%d %H:%M:%S")
        # Save before contacting the broker so a crash cannot duplicate an order.
        state["last_order_attempt_target"] = target
        state["last_order_attempt_at"] = attempt_time_text
        state.pop("active_order_failure", None)
        save_json_atomic(STATE_PATH, state)
        try:
            order_result = execute_target_position(target)
        except Exception as exc:
            error_text = str(exc)
            state["last_order_error_at"] = attempt_time_text
            state["last_order_error_target"] = target
            state["last_order_error"] = error_text
            state["active_order_failure"] = {
                "target_position": target,
                "error": error_text,
                "failed_at": attempt_time_text,
                "notification_count": 1,
                "last_notification_at": attempt_time_text,
                "next_notification_at": (
                    attempt_time
                    + timedelta(seconds=ORDER_ERROR_NOTIFICATION_INTERVAL_SECONDS)
                ).strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_json_atomic(STATE_PATH, state)
            message = order_failure_notification_text(
                failed_at=attempt_time_text,
                target_position=target,
                error_text=error_text,
                notification_number=1,
            )
            print(message)
            send_discord(message)
            return
        executed_previous = order_result.previous_position
        side = "買進" if order_result.side == "buy" else "賣出"
        result = (
            f"已送出{side} TMF {order_result.quantity}口"
            if order_result.order_sent
            else "永豐實際部位已符合目標，未送單"
        )
        message = (
            "🚨【實單執行｜純EF強共識】\n"
            f"時間：{now_text()}\n結果：{result}\n"
            f"實際部位：{position_text(executed_previous)} → "
            f"{position_text(order_result.actual_position)}（已向永豐重新查詢確認）\n"
            f"觸發：{trigger}\n判斷：{decision_text(decision, threshold)}"
        )
        print(message)
        send_discord(message)
        append_trade_transition(executed_previous, target, observed_at)
        state["last_simulated_target"] = target
        state["last_executed_target"] = target
        state["last_executed_at"] = now_text()
        state.pop("last_order_error_at", None)
        state.pop("last_order_error_target", None)
        state.pop("last_order_error", None)
        state.pop("active_order_failure", None)
    else:
        action, side, quantity = simulated_order_action(previous, target)
        message = (
            "🧪【模擬下單｜純EF強共識】\n"
            f"時間：{now_text()}\n"
            f"動作：{action}，{side}微型台指近一（TMF）{quantity}口\n"
            f"模擬部位：{position_text(previous)} → {position_text(target)}\n"
            f"觸發：{trigger}\n"
            f"判斷：{decision_text(decision, threshold)}\n"
            "備註：本策略完全不使用H。"
        )
        print(message)
        if not send_discord(message):
            return
        append_trade_transition(previous, target, observed_at)
        state["last_simulated_target"] = target


def main() -> None:
    load_env_file(ENV_PATH)
    threshold = validate_min_group_net(int(os.getenv("EF_STRONG_MIN_GROUP_NET", "2")))
    unit = validate_position_unit(int(os.getenv("EF_STRONG_POSITION_UNIT", "1")))
    poll_seconds = max(0.5, float(os.getenv("EF_STRONG_POLL_SECONDS", "2")))
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("純EF強共識策略已有另一個實例執行中") from exc

    state = load_json(
        STATE_PATH,
        {"source_row_count": None, "last_simulated_target": None},
    )
    rows = read_source_rows()
    orders_enabled = env_flag(ENABLE_ORDERS_ENV)
    first_start = state.get("source_row_count") is None
    if first_start:
        decision = evaluate_records(threshold, unit)
        write_position(decision, threshold, unit, "首次啟動重建")
        if decision.ready and decision.target_position is not None:
            if orders_enabled or env_flag("EF_STRONG_SIMULATE_ON_START", False):
                process_batch(state, rows, [], threshold, unit)
            else:
                state["last_simulated_target"] = decision.target_position
        state["source_row_count"] = len(rows)
        save_json_atomic(STATE_PATH, state)
    elif orders_enabled:
        # A restart or a switch from simulation to live mode must reconcile the
        # second account immediately. The adapter first reads the real TMF net
        # position, so an already-correct account produces no duplicate order.
        process_batch(state, rows, [], threshold, unit)
        save_json_atomic(STATE_PATH, state)

    mode_text = "永豐實單" if orders_enabled else "Discord模擬下單"
    send_discord(
        "✅【開始監控｜純EF強共識】\n"
        f"時間：{now_text()}\n強共識門檻：E、F各至少淨{threshold}票\n"
        f"單位：{unit}口\n模式：{mode_text}\n本策略不使用H。"
    )
    print(f"=== 純 EF 強共識 {mode_text}監控 ===")
    print(f"source={SOURCE_PATH} threshold={threshold} U={unit}")

    while True:
        if orders_enabled:
            process_order_failure_reminder(state)
        rows = read_source_rows()
        previous_count = int(state.get("source_row_count") or 0)
        if len(rows) < previous_count:
            previous_count = len(rows)
            state["source_row_count"] = previous_count
            save_json_atomic(STATE_PATH, state)
        elif len(rows) > previous_count:
            new_rows = rows[previous_count:]
            process_batch(state, rows, new_rows, threshold, unit)
            state["source_row_count"] = len(rows)
            state["last_event_key"] = signal_event_key(rows[-1], len(rows))
            save_json_atomic(STATE_PATH, state)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
