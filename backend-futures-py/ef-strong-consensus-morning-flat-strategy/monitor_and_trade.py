from __future__ import annotations

import argparse
import faulthandler
import sys
import csv
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from filelock import FileLock, Timeout

from auto_trade import (BrokerOrderError, broker_error_summary,
                        execute_target_position, initialize_broker_session)
from hysteresis_strategy import evaluate_hysteresis_event, hysteresis_target
from strategy import (
    ALL_STRATEGIES,
    ConsensusDecision,
    PORTFOLIO_E,
    PORTFOLIO_F,
    PriceBar,
    latest_morning_boundary,
    load_signal_rows,
    morning_boundaries,
    next_minute_open,
    normalized_positions,
    parse_position_row,
    parse_signal_row,
    position_text,
    signal_is_in_morning_block,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
from ef_trade_runtime import (Notifications, ORDER_FIELDS, append_order, perform_order,
                              save_state)
ENV_PATH = BACKEND_DIR / ".env"
SOURCE_PATH = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
RECORDS_DIR = BASE_DIR / "records"
POSITION_PATH = RECORDS_DIR / "ef_strong_morning_flat_position.json"
DECISION_PATH = RECORDS_DIR / "ef_strong_morning_flat_decisions.csv"
TRADE_PATH = RECORDS_DIR / "ef_strong_morning_flat_shadow_trade.csv"
ORDER_ATTEMPT_PATH = RECORDS_DIR / "live_order_attempts.csv"
CLOCK_EVENT_PATH = RECORDS_DIR / "ef_strong_morning_flat_clock_events.csv"
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "ef_strong_morning_flat_state.json"
LOCK_PATH = RUNTIME_DIR / "ef_strong_morning_flat.lock"
try:
    TZ = ZoneInfo("Asia/Taipei")
except ZoneInfoNotFoundError:
    TZ = timezone(timedelta(hours=8), name="Asia/Taipei")

DECISION_FIELDS = [
    "timestamp",
    "kind",
    "source_row",
    "strategy_code",
    "strategy_name",
    "raw_previous_position",
    "raw_new_position",
    "execution_time",
    "execution_price",
    "e_net",
    "f_net",
    "threshold",
    "previous_position",
    "target_position",
    "relation",
    "reason",
]
TRADE_FIELDS = [
    "timestamp",
    "action",
    "side",
    "price",
    "pnl_points",
    "pnl_twd",
    "quantity",
    "trigger",
]
ORDER_ATTEMPT_FIELDS = ORDER_FIELDS
CLOCK_EVENT_FIELDS = [
    "scheduled_at", "triggered_at", "completed_at", "trigger_delay_seconds",
    "deadline_at", "started_before_deadline", "completed_before_deadline",
    "mode", "previous_target", "target_position", "result",
]
ENABLE_ORDERS_ENV = "EF_HYSTERESIS_MORNING_FLAT_ENABLE_ORDERS"
POSITION_UNIT_ENV = "EF_HYSTERESIS_MORNING_FLAT_POSITION_UNIT"
LEGACY_POSITION_UNIT_ENV = "EF_STRONG_MORNING_FLAT_POSITION_UNIT"
MAX_POSITION_UNIT = 20
LEGACY_SHADOW_STATE_FIELDS = (
    "source_row_count",
    "raw_positions",
    "position",
    "entry_price",
    "last_flat_time",
    "threshold",
    "hold_threshold",
    "started_at",
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def reload_env_file(path: Path) -> None:
    """Reload the bind-mounted credential file after the operator changes it."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def env_stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def wait_for_broker_session(env_path: Path) -> None:
    """Fail closed before reading signals; retry only after credentials change."""
    stamp = env_stamp(env_path)
    while True:
        try:
            initialize_broker_session()
            return
        except Exception as exc:
            summary = broker_error_summary(exc)
            message = (
                f"🚨【強共識】Shioaji 啟動登入失敗：{summary}。"
                "服務安全待命，不讀取或消耗新訊號；更新 .env 憑證後將重新登入。"
            )
            print(message, file=sys.stderr, flush=True)
            send_discord(message)
        while env_stamp(env_path) == stamp:
            time.sleep(5)
        stamp = env_stamp(env_path)
        reload_env_file(env_path)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def position_unit() -> int:
    try:
        unit = int(os.getenv(POSITION_UNIT_ENV) or os.getenv(LEGACY_POSITION_UNIT_ENV, "1"))
    except ValueError as exc:
        raise ValueError(f"{POSITION_UNIT_ENV}必須是1到{MAX_POSITION_UNIT}的整數") from exc
    if not 1 <= unit <= MAX_POSITION_UNIT:
        raise ValueError(f"{POSITION_UNIT_ENV}必須是1到{MAX_POSITION_UNIT}的整數")
    return unit


def scaled_target(base_target: int) -> int:
    if base_target not in {-1, 0, 1} or isinstance(base_target, bool):
        raise ValueError(f"策略基礎目標只能是-1、0或1，目前為{base_target!r}")
    return base_target * position_unit()


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def text_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def save_json_atomic(path: Path, value: dict) -> bool:
    return save_state(path, value)


def append_csv(path: Path, fields: list[str], row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def webhook_url() -> str:
    return (
        os.getenv("DISCORD_EF_HYSTERESIS_MORNING_FLAT_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_EFHYSTERESIS_MORNING_FLAT_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_EFSTRONG_MORNING_FLAT_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_EF_STRONG_MORNING_FLAT_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_MXF_ALERT_WEBHOOK_URL", "").strip()
    )


_notifications = None


def send_discord(content: str) -> bool:
    global _notifications
    if _notifications is None:
        _notifications = Notifications(webhook_url, RECORDS_DIR / "notifications.jsonl")
    return _notifications(content)


def append_order_event(
    *,
    attempt_id: str,
    event: str,
    trigger: str,
    target: int,
    previous: object = "",
    actual: object = "",
    side: object = "",
    quantity: object = "",
    detail: str = "",
) -> None:
    append_order(ORDER_ATTEMPT_PATH, clock=now_local, attempt_id=attempt_id,
                 event=event, trigger=trigger, target_position=target,
                 previous_position=previous, actual_position=actual,
                 side=side, quantity=quantity, detail=detail)


def discard_legacy_shadow_state(state: dict) -> bool:
    """Remove obsolete next-minute-price simulation state from production."""
    changed = False
    for field in LEGACY_SHADOW_STATE_FIELDS:
        if field in state:
            state.pop(field)
            changed = True
    return changed


def record_transition(
    state: dict,
    *,
    previous: int,
    target: int,
    price: float,
    timestamp: datetime,
    trigger: str,
    persist: bool = True,
) -> None:
    if previous == target:
        return
    entry_price = state.get("entry_price")
    if previous:
        pnl_points: float | str = ""
        pnl_twd: float | str = ""
        try:
            pnl_points = round((price - float(entry_price)) * previous, 2)
            pnl_twd = round(float(pnl_points) * 10 * position_unit(), 2)
        except (TypeError, ValueError):
            pass
        if persist:
            append_csv(
                TRADE_PATH,
                TRADE_FIELDS,
                {
                    "timestamp": text_time(timestamp),
                    "action": "exiting",
                    "side": "bull" if previous > 0 else "bear",
                    "price": price,
                    "pnl_points": pnl_points,
                    "pnl_twd": pnl_twd,
                    "quantity": position_unit(),
                    "trigger": trigger,
                },
            )
    if target and persist:
        append_csv(
            TRADE_PATH,
            TRADE_FIELDS,
            {
                "timestamp": text_time(timestamp),
                "action": "enter",
                "side": "bull" if target > 0 else "bear",
                "price": price,
                "pnl_points": "",
                "pnl_twd": "",
                "quantity": position_unit(),
                "trigger": trigger,
            },
        )
    state["position"] = target
    state["entry_price"] = price if target else None


def release_order_lock(state: dict) -> bool:
    """Retain uncertain order history without blocking the next new event."""
    attempt = state.get("attempt", {})
    if attempt.get("status") not in {"pending", "failed"}:
        return True
    state["last_unconfirmed_attempt"] = attempt.copy()
    if attempt.get("key", "").startswith("01:00_live_clock_flat"):
        state["manual_flat_required"] = attempt.copy()
    attempt["status"] = "failed_no_retry" if attempt["status"] == "failed" else "interrupted_no_retry"
    return save_json_atomic(STATE_PATH, state) is not False


def execute_live_target(
    state: dict,
    target: int,
    *,
    trigger: str,
    force_reconcile: bool = False,
) -> str:
    if not env_flag(ENABLE_ORDERS_ENV):
        return "影子模式，未送實單"

    current_time = now_local()
    if target and signal_is_in_morning_block(current_time, current_time):
        return "01:00～08:45禁止進場，本筆未送單，等待開盤後新訊號"

    broker_target = scaled_target(target)
    attempt_id = uuid.uuid4().hex

    if not release_order_lock(state):
        return "❌ 無法保存委託歷史，本筆未送單、不自動重送；下一筆新訊號仍會檢查"
    if trigger == "startup_reconcile":
        return "啟動不補單，等待新EF訊號"
    order_key = (f"{trigger}/{now_local():%Y-%m-%d}"
                 if trigger == "01:00_live_clock_flat" else trigger)
    # Deduplicate the event, not its target: a NEW event may have the same target.
    if state.get("attempt", {}).get("key") == order_key:
        append_order_event(
            attempt_id=attempt_id,
            event="skipped_duplicate",
            trigger=trigger,
            target=broker_target,
            detail="同一訊號已嘗試過，防重送",
        )
        return "同一訊號已嘗試過，不重送；下一筆新訊號照常處理"
    attempted_at = text_time(now_local())
    state["last_order_attempt_target"] = broker_target
    state["last_order_attempt_at"] = attempted_at
    state["last_order_trigger"] = trigger
    def prepared(data):
        state["attempt"].update(data)
        state["attempt"]["broker_phase"] = "prepared"
        if save_json_atomic(STATE_PATH, state) is False:
            raise OSError("無法保存本次庫存與預計下單")

    def submitted(checkpoint, result):
        raw_status = getattr(
            getattr(getattr(result, "trade", None), "status", None), "status", ""
        )
        state["attempt"]["broker_status"] = str(getattr(raw_status, "value", raw_status) or "")
        state["attempt"]["broker_trade_id"] = getattr(
            getattr(getattr(result, "trade", None), "order", None), "id", ""
        )
        checkpoint(result)

    def calculation_text(result_detail: str) -> str:
        attempt = state.get("attempt", {})
        previous = attempt.get("broker_before_position")
        side = attempt.get("broker_side")
        quantity = attempt.get("broker_quantity")
        current_text = position_text(previous) if isinstance(previous, int) else "查詢失敗"
        if isinstance(quantity, int):
            planned = ("無需下單" if quantity == 0 else
                       f"{'買進' if side == 'buy' else '賣出'} TMF {quantity}口")
        else:
            planned = "無法計算"
        return (
            f"2. 目前券商庫存：{current_text}\n"
            f"3. 本次預計下單：{planned}\n"
            f"4. 收到策略後最終口數：{position_text(broker_target)}\n"
            f"5. 實際送單結果：{result_detail}"
        )
    try:
        result = perform_order(
            state, key=order_key, target=broker_target,
            persist=lambda: save_json_atomic(STATE_PATH, state),
            execute=lambda: execute_target_position(
                broker_target, on_prepared=prepared),
            execute_checkpointed=lambda checkpoint: execute_target_position(
                broker_target, on_prepared=prepared,
                on_submitted=lambda result: submitted(checkpoint, result)),
            record=lambda **row: append_order(ORDER_ATTEMPT_PATH, clock=now_local, **row),
            clock=now_local,
        )
    except Exception as exc:
        state["last_order_error_target"] = target
        state["last_order_error_at"] = attempted_at
        error = str(exc) if isinstance(exc, BrokerOrderError) else type(exc).__name__
        state["last_order_error"] = error
        release_order_lock(state)
        save_json_atomic(STATE_PATH, state)
        if trigger == "01:00_live_clock_flat":
            return calculation_text(
                f"🚨 清倉送單失敗或回傳不明：{error}；本筆不重送，新訊號照常處理"
            )
        return calculation_text(
            f"❌ 本次送單失敗或回傳不明：{error}；本筆不重送，新訊號照常處理"
        )
    state["last_executed_target"] = broker_target
    state["last_executed_at"] = text_time(now_local())
    if getattr(result, "confirmed", True):
        state["last_confirmed_broker_position"] = result.actual_position
        state["last_confirmed_broker_at"] = state["last_executed_at"]
    for field in ("last_order_error_target", "last_order_error_at", "last_order_error"):
        state.pop(field, None)
    save_json_atomic(STATE_PATH, state)
    if result.quantity:
        status = state.get("attempt", {}).get("broker_status") or "API已回傳"
        detail = (f"已送出{'買進' if result.side == 'buy' else '賣出'} TMF "
                  f"{result.quantity}口（狀態：{status}；未回查成交）")
    else:
        detail = "券商庫存已符合最終口數，無需送單"
    return calculation_text(detail)


def write_position(state: dict, reason: str) -> None:
    raw_positions = normalized_positions(state.get("raw_positions"))
    threshold = int(state.get("threshold") or 2)
    hold_threshold = int(state.get("hold_threshold", 1))
    current = int(state.get("position") or 0)
    target, e_net, f_net, relation = hysteresis_target(
        raw_positions,
        current,
        entry_threshold=threshold,
        hold_threshold=hold_threshold,
    )
    save_json_atomic(
        POSITION_PATH,
        {
            "strategy": "EF Hysteresis Consensus + Morning Flat",
            "mode": (
                "live_api_key" if env_flag(ENABLE_ORDERS_ENV) else "shadow_only"
            ),
            "position_unit": position_unit(),
            "broker_target_position": scaled_target(target),
            "rule": "E/F each reach entry threshold; hold while both retain hold threshold; one contract; 01:00 flatten",
            "e_net": e_net,
            "f_net": f_net,
            "raw_consensus_target": target,
            "raw_hysteresis_target": target,
            "relation": relation,
            "shadow_position": int(state.get("position") or 0),
            "entry_price": state.get("entry_price"),
            "threshold": threshold,
            "hold_threshold": hold_threshold,
            "last_flat_time": state.get("last_flat_time", ""),
            "last_reason": reason,
            "raw_positions": raw_positions,
            "updated_at": text_time(now_local()),
        },
    )


def append_signal_decision(decision: ConsensusDecision) -> None:
    append_csv(
        DECISION_PATH,
        DECISION_FIELDS,
        {
            "timestamp": text_time(decision.event.timestamp),
            "kind": "signal",
            "source_row": decision.event.row_number,
            "strategy_code": decision.event.strategy_code,
            "strategy_name": decision.event.strategy_name,
            "raw_previous_position": decision.event.previous_position,
            "raw_new_position": decision.event.new_position,
            "execution_time": text_time(decision.execution_time),
            "execution_price": decision.execution_price,
            "e_net": decision.e_net,
            "f_net": decision.f_net,
            "threshold": decision.threshold,
            "previous_position": decision.previous_position,
            "target_position": decision.target_position,
            "relation": decision.relation,
            "reason": decision.reason,
        },
    )


def position_breakdown(
    positions: object,
    codes: tuple[str, ...],
    expected_net: int,
) -> str:
    if not positions:
        return f"明細未提供（合計{expected_net:+d}）"
    values = dict(positions or ())
    details = " + ".join(f"{code}({int(values.get(code, 0)):+d})" for code in codes)
    return f"{details} = {expected_net:+d}"


def decision_message(decision: ConsensusDecision, live_result: str) -> str:
    previous_final = scaled_target(decision.previous_position)
    target_final = scaled_target(decision.target_position)
    action = (
        "目標未變"
        if decision.previous_position == decision.target_position
        else f"{position_text(previous_final)} → {position_text(target_final)}"
    )
    return (
        "🚨【策略訊號｜EF Hysteresis＋01:00清倉】\n"
        f"訊號時間：{text_time(decision.event.timestamp)}\n"
        f"策略目標部位：{position_text(target_final)}\n"
        f"模擬成交：{text_time(decision.execution_time)} @ {decision.execution_price:g}\n"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"E淨部位：{decision.e_net}；F淨部位：{decision.f_net}\n"
        f"E明細：{position_breakdown(getattr(decision, 'e_positions', ()), PORTFOLIO_E, decision.e_net)}\n"
        f"F明細：{position_breakdown(getattr(decision, 'f_positions', ()), PORTFOLIO_F, decision.f_net)}\n"
        f"組合部位：{action}\n"
        f"原因：{decision.reason}\n"
        f"執行：{live_result}\n"
        f"備註：目前U={position_unit()}。"
    )


def immediate_live_message(decision: ConsensusDecision, live_result: str) -> str:
    previous_final = scaled_target(decision.previous_position)
    target_final = scaled_target(decision.target_position)
    action = (
        "目標未變"
        if decision.previous_position == decision.target_position
        else f"{position_text(previous_final)} → {position_text(target_final)}"
    )
    return (
        "🚨【強共識｜EF訊號與送單計算】\n"
        f"1. EF策略與進出場訊號：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code}) {decision.event.previous_position} → {decision.event.new_position}\n"
        f"收到時間：{text_time(decision.event.timestamp)}\n"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"E淨部位：{decision.e_net}；F淨部位：{decision.f_net}\n"
        f"E明細：{position_breakdown(getattr(decision, 'e_positions', ()), PORTFOLIO_E, decision.e_net)}\n"
        f"F明細：{position_breakdown(getattr(decision, 'f_positions', ()), PORTFOLIO_F, decision.f_net)}\n"
        f"組合目標：{action}\n"
        f"原因：{decision.reason}\n"
        f"{live_result}"
    )


def initialize_live_cursor(state: dict, rows: list[dict[str, str]]) -> None:
    saved_count = state.get("live_source_row_count")
    try:
        if saved_count is not None and 0 <= int(saved_count) <= len(rows):
            return
    except (TypeError, ValueError):
        pass
    positions = {code: 0 for code in ALL_STRATEGIES}
    for row in rows:
        parsed = parse_position_row(row)
        if parsed is not None:
            positions[parsed[0]] = parsed[1]
    state["live_raw_positions"] = positions
    state["live_source_row_count"] = len(rows)
    # A fresh production monitor starts flat and consumes only future events.
    # Legacy shadow state must never become a live broker target.
    state["live_target_position"] = int(state.get("live_target_position") or 0)
    state["live_cursor_initialized_at"] = text_time(now_local())
    save_json_atomic(STATE_PATH, state)


def process_live_rows(
    state: dict,
    rows: list[dict[str, str]],
    threshold: int,
    hold_threshold: int = 1,
) -> None:
    if not env_flag(ENABLE_ORDERS_ENV):
        return
    state_before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    previous_count = int(state.get("live_source_row_count") or 0)
    positions = normalized_positions(state.get("live_raw_positions"))
    current = int(state.get("live_target_position") or 0)
    for row_number, row in enumerate(rows[previous_count:], start=previous_count + 1):
        event = parse_signal_row(row, row_number)
        if event is None:
            parsed = parse_position_row(row)
            if parsed is not None:
                positions[parsed[0]] = parsed[1]
            state["live_source_row_count"] = row_number
            continue

        intended_time = event.timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        intended_bar = PriceBar(
            bar_time=intended_time,
            record_time=event.timestamp,
            open=0,
            close=0,
        )
        decision = evaluate_hysteresis_event(
            positions,
            current,
            event,
            intended_bar,
            entry_threshold=threshold,
            hold_threshold=hold_threshold,
        )
        state["live_raw_positions"] = positions
        state["live_source_row_count"] = row_number
        state["live_target_position"] = decision.target_position
        save_json_atomic(STATE_PATH, state)
        live_result = execute_live_target(
            state,
            decision.target_position,
            trigger=f"immediate_ef_signal_row_{row_number}",
        )
        message = immediate_live_message(decision, live_result)
        print(message)
        send_discord(message)
        current = decision.target_position
    state["live_raw_positions"] = positions
    state["live_source_row_count"] = len(rows)
    state["live_target_position"] = current
    if json.dumps(state, ensure_ascii=False, sort_keys=True) != state_before:
        save_json_atomic(STATE_PATH, state)


def apply_live_clock_flatten(state: dict, current_time: datetime) -> bool:
    boundary = current_time.replace(hour=1, minute=0, second=0, microsecond=0)
    reopen = current_time.replace(hour=8, minute=45, second=0, microsecond=0)
    if not boundary <= current_time < reopen:
        return False
    if str(state.get("last_live_flat_time") or "").startswith(boundary.strftime("%Y-%m-%d")):
        return False
    triggered_at = current_time
    deadline = boundary + timedelta(seconds=30)
    trigger_delay = (triggered_at - boundary).total_seconds()
    previous = int(state.get("live_target_position") or 0)
    state["live_target_position"] = 0
    state["last_live_flat_time"] = text_time(boundary)
    state["last_live_flat_triggered_at"] = text_time(triggered_at)
    state["last_live_flat_trigger_delay_seconds"] = trigger_delay
    save_json_atomic(STATE_PATH, state)
    if env_flag(ENABLE_ORDERS_ENV):
        result = execute_live_target(
            state,
            0,
            trigger="01:00_live_clock_flat",
            force_reconcile=True,
        )
        mode = "live_api_key"
    else:
        result = "Discord／影子模式：已記錄01:00目標空手，未連線永豐、未送委託"
        mode = "shadow_only"
    completed_at = max(now_local(), triggered_at)
    started_ok = triggered_at < deadline
    completed_ok = completed_at < deadline
    state["last_live_flat_completed_at"] = text_time(completed_at)
    state["last_live_flat_started_before_deadline"] = started_ok
    state["last_live_flat_completed_before_deadline"] = completed_ok
    save_json_atomic(STATE_PATH, state)
    append_csv(CLOCK_EVENT_PATH, CLOCK_EVENT_FIELDS, {
        "scheduled_at": text_time(boundary),
        "triggered_at": text_time(triggered_at),
        "completed_at": text_time(completed_at),
        "trigger_delay_seconds": f"{trigger_delay:.3f}",
        "deadline_at": text_time(deadline),
        "started_before_deadline": started_ok,
        "completed_before_deadline": completed_ok,
        "mode": mode,
        "previous_target": scaled_target(previous),
        "target_position": 0,
        "result": result,
    })
    deadline_text = (
        "✅ 01:00:30前已完成下單／對帳流程"
        if completed_ok
        else "🚨 已超過01:00:30完成期限，請立即人工核對"
    )
    message = (
        "🌅【01:00時鐘清倉｜EF Hysteresis】\n"
        f"排程時間：{text_time(boundary)}\n"
        f"實際觸發：{text_time(triggered_at)}（延遲{trigger_delay:.3f}秒）\n"
        f"流程完成：{text_time(completed_at)}\n"
        f"期限：{text_time(deadline)}；{deadline_text}\n"
        "策略目標口數：空手（實際庫存以執行結果為準）\n"
        f"組合目標：{position_text(scaled_target(previous))} → 空手\n"
        f"執行：{result}\n"
        "08:45不恢復舊目標，等待新E/F訊號；"
        "後續訊號仍以當下券商庫存重新計算。"
    )
    print(message)
    send_discord(message)
    return True


def apply_flatten_bar(
    state: dict,
    boundary: PriceBar,
    *,
    persist: bool = True,
    notify: bool = True,
) -> bool:
    previous = int(state.get("position") or 0)
    changed = previous != 0
    record_transition(
        state,
        previous=previous,
        target=0,
        price=boundary.open,
        timestamp=boundary.bar_time,
        trigger="01:00_morning_flat",
        persist=persist,
    )
    state["last_flat_time"] = text_time(boundary.bar_time)
    reason = "01:00清空Hysteresis組合部位；08:45不自動恢復，等待新EF訊號"
    if persist:
        append_csv(
            DECISION_PATH,
            DECISION_FIELDS,
            {
                "timestamp": text_time(boundary.bar_time),
                "kind": "scheduled_flatten",
                "execution_time": text_time(boundary.bar_time),
                "execution_price": boundary.open,
                "threshold": int(state.get("threshold") or 2),
                "previous_position": previous,
                "target_position": 0,
                "relation": "morning_flat",
                "reason": reason,
            },
        )
        write_position(state, reason)
    if notify and not env_flag(ENABLE_ORDERS_ENV):
        live_result = (
            "實單已由01:00時鐘排程獨立處理"
            if env_flag(ENABLE_ORDERS_ENV)
            else "影子模式，未送實單"
        )
        message = (
            "🌅【01:00清倉｜EF Hysteresis】\n"
            f"時間：{text_time(boundary.bar_time)}\n"
            "策略目標部位：空手\n"
            f"模擬成交價：{boundary.open:g}\n"
            f"組合部位：{position_text(scaled_target(previous))} → 空手\n"
            "08:45不自動恢復，等待新的E/F訊號。\n"
            f"執行：{live_result}"
        )
        print(message)
        send_discord(message)
    return changed


def apply_due_flatten(state: dict, bars: list[PriceBar], through: datetime) -> None:
    try:
        last_flat = datetime.strptime(
            str(state.get("last_flat_time") or ""), "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        last_flat = None
    for boundary in morning_boundaries(bars, after=last_flat, through=through):
        apply_flatten_bar(state, boundary)


def initialize_state(
    rows: list[dict[str, str]],
    bars: list[PriceBar],
    cutoff: datetime,
    threshold: int,
    hold_threshold: int = 1,
    previous_state: dict | None = None,
) -> dict:
    boundary = latest_morning_boundary(bars, cutoff)
    replay_start = datetime.min if boundary is None else boundary.bar_time
    raw_positions = {code: 0 for code in ALL_STRATEGIES}
    state: dict = {
        "source_row_count": 0,
        "raw_positions": raw_positions,
        "position": 0,
        "entry_price": None,
        "threshold": threshold,
        "hold_threshold": hold_threshold,
        "last_flat_time": "" if boundary is None else text_time(boundary.bar_time),
        "started_at": text_time(cutoff),
    }
    for row_number, row in enumerate(rows, start=1):
        event = parse_signal_row(row, row_number)
        if event is None:
            if not str(row.get("received_at") or "").strip():
                parsed = parse_position_row(row)
                if parsed is not None:
                    raw_positions[parsed[0]] = parsed[1]
            state["source_row_count"] = row_number
            continue
        execution_bar = next_minute_open(bars, event.timestamp)
        if execution_bar is None or execution_bar.record_time > cutoff:
            break
        if execution_bar.bar_time <= replay_start:
            raw_positions[event.strategy_code] = event.new_position
        else:
            current = int(state.get("position") or 0)
            decision = evaluate_hysteresis_event(
                raw_positions,
                current,
                event,
                execution_bar,
                entry_threshold=threshold,
                hold_threshold=hold_threshold,
            )
            record_transition(
                state,
                previous=current,
                target=decision.target_position,
                price=execution_bar.open,
                timestamp=execution_bar.bar_time,
                trigger="startup_rebuild",
                persist=False,
            )
        state["source_row_count"] = row_number
    state["raw_positions"] = raw_positions
    # Rebuilding strategy history must never erase an unresolved broker attempt.
    for key, value in (previous_state or {}).items():
        if key == "attempt" or key.startswith(("last_order_", "last_executed_")):
            state[key] = value
    save_json_atomic(STATE_PATH, state)
    write_position(state, "startup rebuild")
    return state


def process_new_rows(
    state: dict,
    rows: list[dict[str, str]],
    bars: list[PriceBar],
    cutoff: datetime,
    threshold: int,
    hold_threshold: int = 1,
) -> None:
    state_before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    previous_count = int(state.get("source_row_count") or 0)
    raw_positions = normalized_positions(state.get("raw_positions"))
    for row_number, row in enumerate(rows[previous_count:], start=previous_count + 1):
        event = parse_signal_row(row, row_number)
        if event is None:
            if not str(row.get("received_at") or "").strip():
                parsed = parse_position_row(row)
                if parsed is not None:
                    raw_positions[parsed[0]] = parsed[1]
            state["source_row_count"] = row_number
            continue
        execution_bar = next_minute_open(bars, event.timestamp)
        if execution_bar is None or execution_bar.record_time > cutoff:
            break
        apply_due_flatten(state, bars, execution_bar.record_time)
        previous = int(state.get("position") or 0)
        decision = evaluate_hysteresis_event(
            raw_positions,
            previous,
            event,
            execution_bar,
            entry_threshold=threshold,
            hold_threshold=hold_threshold,
        )
        record_transition(
            state,
            previous=previous,
            target=decision.target_position,
            price=execution_bar.open,
            timestamp=execution_bar.bar_time,
            trigger="ef_signal",
        )
        state["raw_positions"] = raw_positions
        state["source_row_count"] = row_number
        append_signal_decision(decision)
        write_position(state, decision.reason)
        live_result = (
            "實單已於收到訊號時立即處理；本則為影子成交補記"
            if env_flag(ENABLE_ORDERS_ENV)
            else "影子模式，未送實單"
        )
        if not env_flag(ENABLE_ORDERS_ENV):
            message = decision_message(decision, live_result)
            print(message)
            send_discord(message)
    apply_due_flatten(state, bars, cutoff)
    state["raw_positions"] = raw_positions
    if json.dumps(state, ensure_ascii=False, sort_keys=True) != state_before:
        save_json_atomic(STATE_PATH, state)


def main() -> None:
    faulthandler.enable(all_threads=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--retry-failed", action="store_true", help="相容舊版：清理委託狀態；目前失敗不鎖單，不需此參數")
    args = parser.parse_args()
    load_env_file(ENV_PATH)
    # Production entry point is always live, including with a legacy false .env.
    os.environ[ENABLE_ORDERS_ENV] = "true"
    poll_seconds = max(0.5, float(
        os.getenv("EF_HYSTERESIS_MORNING_FLAT_POLL_SECONDS")
        or os.getenv("EF_STRONG_MORNING_FLAT_POLL_SECONDS", "2")
    ))
    threshold = int(
        os.getenv("EF_HYSTERESIS_ENTRY_GROUP_NET")
        or os.getenv("EF_STRONG_MORNING_FLAT_MIN_GROUP_NET", "2")
    )
    hold_threshold = int(os.getenv("EF_HYSTERESIS_HOLD_GROUP_NET", "1"))
    if not 1 <= threshold <= 6:
        raise ValueError("EF_HYSTERESIS_ENTRY_GROUP_NET必須是1到6")
    if not 0 <= hold_threshold <= threshold:
        raise ValueError("EF_HYSTERESIS_HOLD_GROUP_NET必須介於0與進場門檻")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("EF Hysteresis＋01:00清倉已有另一個實例執行中") from exc

    try:
        # Establish the process-long native session before reading or consuming
        # any signal.  Startup failure therefore cannot create an unsent but
        # consumed trading event.
        wait_for_broker_session(ENV_PATH)
        rows = load_signal_rows(SOURCE_PATH)
        state = load_json(STATE_PATH, {})
        if discard_legacy_shadow_state(state):
            save_json_atomic(STATE_PATH, state)
        if args.retry_failed:
            append_order_event(attempt_id=uuid.uuid4().hex, event="operator_retry",
                               trigger="operator_retry", target=0)
            for field in ("attempt", "last_order_attempt_target", "last_order_error",
                          "last_order_error_target", "last_order_error_at"):
                state.pop(field, None)
            if not save_json_atomic(STATE_PATH, state):
                raise RuntimeError("無法儲存解除鎖定狀態")
        unit = position_unit()
        startup_result = ""
        initialize_live_cursor(state, rows)
        startup_base_target = int(state.get("live_target_position") or 0)
        clock_flatten_applied = apply_live_clock_flatten(state, now_local())
        if clock_flatten_applied:
            startup_base_target = 0
        elif env_flag(ENABLE_ORDERS_ENV):
            startup_base_target = int(state.get("live_target_position") or 0)
        if env_flag(ENABLE_ORDERS_ENV) and not clock_flatten_applied:
            startup_result = execute_live_target(
                state,
                startup_base_target,
                trigger="startup_reconcile",
            )
        startup_message = (
            "✅【開始監控｜EF Hysteresis＋01:00清倉】\n"
            f"時間：{text_time(now_local())}\n"
            f"策略目標部位：{position_text(scaled_target(startup_base_target))}\n"
            f"規則：E/F兩組皆達{threshold}票同向才進場；持倉後兩組皆保留至少{hold_threshold}票才續抱；U={unit}。\n"
            "01:00清倉；08:45不自動恢復，等新EF訊號再判斷。\n"
            "每筆新訊號查當下券商庫存，以最終口數減庫存計算本次下單；啟動不補單。\n"
            "執行：收到新訊號立即查實際庫存並送差額委託，結果以券商回報為準。\n"
            f"模式：{'API_KEY永豐實單' if env_flag(ENABLE_ORDERS_ENV) else '影子模式'}。"
        )
        if startup_result:
            startup_message += f"\n啟動對帳：{startup_result}"
        print(startup_message)
        send_discord(startup_message)

        while True:
            # Check the hard clock boundary before file I/O and signal processing.
            # With the default two-second poll this normally starts by 01:00:02.
            apply_live_clock_flatten(state, now_local())
            rows = load_signal_rows(SOURCE_PATH)
            # Catch a boundary crossed while reading the shared signal file.
            apply_live_clock_flatten(state, now_local())
            initialize_live_cursor(state, rows)
            process_live_rows(state, rows, threshold, hold_threshold)
            time.sleep(poll_seconds)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
