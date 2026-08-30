from __future__ import annotations

import csv
import json
import os
import time
import uuid
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from filelock import FileLock, Timeout

from auto_trade import execute_target_position
from strategy import (
    ALL_STRATEGIES,
    BoundaryEvent,
    PriceBar,
    SignalDecision,
    apply_signal,
    flatten_positions,
    load_price_bars,
    load_signal_rows,
    net_position,
    next_minute_open,
    normalized_positions,
    observed_day_session,
    observed_night_session,
    parse_position_row,
    parse_signal_row,
    position_text,
    restore_latest_positions,
    session_boundaries,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"
SOURCE_PATH = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
PRICE_PATH = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
RECORDS_DIR = BASE_DIR / "records"
POSITION_PATH = RECORDS_DIR / "ef_dual_session_position.json"
DECISION_PATH = RECORDS_DIR / "ef_dual_session_decisions.csv"
TRADE_PATH = RECORDS_DIR / "ef_dual_session_shadow_trade.csv"
ORDER_PATH = RECORDS_DIR / "ef_dual_session_order_attempts.csv"
CLOCK_PATH = RECORDS_DIR / "ef_dual_session_clock_events.csv"
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "ef_dual_session_state.json"
LOCK_PATH = RUNTIME_DIR / "ef_dual_session.lock"

ENABLE_ORDERS_ENV = "EF_DUAL_SESSION_ENABLE_ORDERS"
RECONCILE_ON_START_ENV = "EF_DUAL_SESSION_RECONCILE_ON_START"
WEBHOOK_ENV = "DISCORD_EF_DUAL_SESSION_WEBHOOK_URL"

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
    "previous_position",
    "target_position",
    "previous_net_position",
    "net_position",
    "phase",
    "reason",
]
TRADE_FIELDS = [
    "timestamp",
    "strategy_code",
    "action",
    "side",
    "price",
    "pnl_points",
    "pnl_twd",
    "quantity",
    "trigger",
]
ORDER_FIELDS = [
    "timestamp",
    "attempt_id",
    "event_key",
    "trigger",
    "event",
    "target_position",
    "previous_position",
    "actual_position",
    "side",
    "quantity",
    "detail",
]
CLOCK_FIELDS = [
    "scheduled_at",
    "triggered_at",
    "event_key",
    "kind",
    "mode",
    "previous_target",
    "target_position",
    "result",
]


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


def save_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def append_csv(path: Path, fields: list[str], row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def webhook_url() -> str:
    return os.getenv(WEBHOOK_ENV, "").strip() or os.getenv(
        "DISCORD_MXF_ALERT_WEBHOOK_URL", ""
    ).strip()


def send_discord(content: str) -> bool:
    webhook = webhook_url()
    if not webhook:
        print(f"⚠️ 未設定 {WEBHOOK_ENV} 或 DISCORD_MXF_ALERT_WEBHOOK_URL")
        return False
    try:
        response = requests.post(
            webhook,
            params={"wait": "true"},
            json={"username": "NotifierBot", "content": content},
            timeout=15,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        safe_error = str(exc).replace(webhook, "<Discord webhook>")
        print(f"❌ Discord通知失敗：{safe_error}")
        return False


def _stored_keys(value: object) -> list[str]:
    source = value if isinstance(value, list) else []
    return [str(item) for item in source if str(item).strip()]


def _entry_prices(state: dict) -> dict[str, float]:
    source = state.get("entry_prices")
    if not isinstance(source, dict):
        return {}
    result: dict[str, float] = {}
    for code, value in source.items():
        if code not in ALL_STRATEGIES:
            continue
        try:
            result[code] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def record_transition(
    state: dict,
    *,
    strategy_code: str,
    previous: int,
    target: int,
    price: float,
    timestamp: datetime,
    trigger: str,
    persist: bool = True,
) -> None:
    if previous == target:
        return
    entries = _entry_prices(state)
    if previous:
        old_entry = entries.get(strategy_code)
        pnl_points: float | str = ""
        pnl_twd: float | str = ""
        if old_entry is not None:
            pnl_points = round((price - old_entry) * previous, 2)
            pnl_twd = round(float(pnl_points) * 10, 2)
        if persist:
            append_csv(
                TRADE_PATH,
                TRADE_FIELDS,
                {
                    "timestamp": text_time(timestamp),
                    "strategy_code": strategy_code,
                    "action": "exiting",
                    "side": "bull" if previous > 0 else "bear",
                    "price": price,
                    "pnl_points": pnl_points,
                    "pnl_twd": pnl_twd,
                    "quantity": 1,
                    "trigger": trigger,
                },
            )
        entries.pop(strategy_code, None)
    if target:
        if persist:
            append_csv(
                TRADE_PATH,
                TRADE_FIELDS,
                {
                    "timestamp": text_time(timestamp),
                    "strategy_code": strategy_code,
                    "action": "enter",
                    "side": "bull" if target > 0 else "bear",
                    "price": price,
                    "pnl_points": "",
                    "pnl_twd": "",
                    "quantity": 1,
                    "trigger": trigger,
                },
            )
        entries[strategy_code] = price
    state["entry_prices"] = entries


def write_position(state: dict, reason: str) -> None:
    raw = normalized_positions(state.get("raw_positions"))
    active = normalized_positions(state.get("active_positions"))
    live_raw = normalized_positions(state.get("live_raw_positions", raw))
    live_active = normalized_positions(state.get("live_active_positions", active))
    save_json_atomic(
        POSITION_PATH,
        {
            "strategy": "EF Dual Session Guard",
            "mode": "live_dedicated_api" if env_flag(ENABLE_ORDERS_ENV) else "shadow_only",
            "rule": (
                "13:44 flatten; 15:00 restore latest EF; "
                "04:59 flatten; no 08:45 restore; wait for new day signal"
            ),
            "raw_net_position": net_position(raw),
            "shadow_net_position": net_position(active),
            "live_raw_net_position": net_position(live_raw),
            "live_target_position": net_position(live_active),
            "raw_positions": raw,
            "shadow_positions": active,
            "live_raw_positions": live_raw,
            "live_positions": live_active,
            "last_reason": reason,
            "updated_at": text_time(now_local()),
        },
    )


def _append_signal_decision(decision: SignalDecision) -> None:
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
            "previous_position": decision.previous_position,
            "target_position": decision.target_position,
            "previous_net_position": decision.previous_net_position,
            "net_position": decision.net_position,
            "phase": decision.phase,
            "reason": decision.reason,
        },
    )


def _signal_message(decision: SignalDecision, live_text: str) -> str:
    return (
        "🛡️【EF雙時段風控｜策略訊號】\n"
        f"收到時間：{text_time(decision.event.timestamp)}\n"
        f"影子成交：{text_time(decision.execution_time)} @ {decision.execution_price:g}\n"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"風控部位：{decision.previous_position} → {decision.target_position}\n"
        f"總倉：{position_text(decision.previous_net_position)} → "
        f"{position_text(decision.net_position)}\n"
        f"時段：{decision.phase}；原因：{decision.reason}\n"
        f"實單：{live_text}"
    )


def _append_order_event(
    *,
    attempt_id: str,
    event_key: str,
    trigger: str,
    event: str,
    target: int,
    previous: object = "",
    actual: object = "",
    side: object = "",
    quantity: object = "",
    detail: str = "",
) -> None:
    append_csv(
        ORDER_PATH,
        ORDER_FIELDS,
        {
            "timestamp": text_time(now_local()),
            "attempt_id": attempt_id,
            "event_key": event_key,
            "trigger": trigger,
            "event": event,
            "target_position": target,
            "previous_position": previous,
            "actual_position": actual,
            "side": side,
            "quantity": quantity,
            "detail": detail,
        },
    )


def execute_live_target(
    state: dict,
    target: int,
    *,
    trigger: str,
    event_key: str,
) -> str:
    if not env_flag(ENABLE_ORDERS_ENV):
        return "影子模式，未送實單"
    if state.get("last_order_attempt_key") == event_key:
        return f"事件{event_key}已嘗試過，防止重送"

    attempt_id = uuid.uuid4().hex
    state["last_order_attempt_key"] = event_key
    state["last_order_attempt_target"] = target
    state["last_order_attempt_at"] = text_time(now_local())
    save_json_atomic(STATE_PATH, state)
    _append_order_event(
        attempt_id=attempt_id,
        event_key=event_key,
        trigger=trigger,
        event="attempt_started",
        target=target,
        detail="準備登入專屬永豐帳戶並對帳TMF淨部位",
    )
    try:
        result = execute_target_position(target)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        state["last_order_error"] = error
        state["last_order_error_at"] = text_time(now_local())
        save_json_atomic(STATE_PATH, state)
        _append_order_event(
            attempt_id=attempt_id,
            event_key=event_key,
            trigger=trigger,
            event="failed",
            target=target,
            detail=error,
        )
        return f"❌ 下單失敗：{error}（同一事件不自動重送）"

    state["last_executed_target"] = result.actual_position
    state["last_executed_at"] = text_time(now_local())
    state.pop("last_order_error", None)
    state.pop("last_order_error_at", None)
    save_json_atomic(STATE_PATH, state)
    _append_order_event(
        attempt_id=attempt_id,
        event_key=event_key,
        trigger=trigger,
        event="order_sent_confirmed" if result.order_sent else "no_order_needed",
        target=target,
        previous=result.previous_position,
        actual=result.actual_position,
        side=result.side or "",
        quantity=result.quantity,
        detail="永豐回查確認完成",
    )
    if not result.order_sent:
        return f"帳戶已是{position_text(result.actual_position)}，無需送單"
    action = "買進" if result.side == "buy" else "賣出"
    return (
        f"✅ 已{action}TMF {result.quantity}口；"
        f"{position_text(result.previous_position)} → "
        f"{position_text(result.actual_position)}（已回查）"
    )


def _transition_positions(
    state: dict,
    previous_positions: dict[str, int],
    target_positions: dict[str, int],
    *,
    price: float,
    timestamp: datetime,
    trigger: str,
    persist: bool,
) -> None:
    for code in ALL_STRATEGIES:
        record_transition(
            state,
            strategy_code=code,
            previous=previous_positions[code],
            target=target_positions[code],
            price=price,
            timestamp=timestamp,
            trigger=trigger,
            persist=persist,
        )


def apply_shadow_boundary(
    state: dict,
    boundary: BoundaryEvent,
    *,
    persist: bool = True,
    notify: bool = True,
) -> None:
    raw = normalized_positions(state.get("raw_positions"))
    active = normalized_positions(state.get("active_positions"))
    previous = dict(active)
    if boundary.kind == "night_restore":
        active, previous_net, target_net = restore_latest_positions(raw, active)
        reason = "15:00恢復當下最新EF十二策略部位"
    else:
        active, previous_net, target_net = flatten_positions(active)
        reason = (
            "13:44清空全部EF風控部位；15:00恢復最新EF部位"
            if boundary.kind == "day_flat"
            else "04:59清空全部EF風控部位；08:45不恢復，等待日盤新訊號"
        )
    _transition_positions(
        state,
        previous,
        active,
        price=boundary.price,
        timestamp=boundary.timestamp,
        trigger=boundary.kind,
        persist=persist,
    )
    state["active_positions"] = active
    keys = _stored_keys(state.get("shadow_boundary_keys"))
    if boundary.key not in keys:
        keys.append(boundary.key)
    state["shadow_boundary_keys"] = keys
    if persist:
        append_csv(
            DECISION_PATH,
            DECISION_FIELDS,
            {
                "timestamp": text_time(boundary.timestamp),
                "kind": boundary.kind,
                "execution_time": text_time(boundary.timestamp),
                "execution_price": boundary.price,
                "previous_net_position": previous_net,
                "net_position": target_net,
                "phase": boundary.kind,
                "reason": reason,
            },
        )
        write_position(state, reason)
    if notify:
        message = (
            "🛡️【EF雙時段風控｜排程】\n"
            f"時間：{text_time(boundary.timestamp)}\n"
            f"事件：{boundary.kind}\n"
            f"影子成交：{boundary.price:g}\n"
            f"總倉：{position_text(previous_net)} → {position_text(target_net)}\n"
            f"原因：{reason}\n"
            + (
                "實單由本機時鐘排程獨立處理。"
                if env_flag(ENABLE_ORDERS_ENV)
                else "只記錄影子績效，不送實單。"
            )
        )
        print(message)
        send_discord(message)


def _rebuild_state(
    rows: list[dict[str, str]], bars: list[PriceBar], cutoff: datetime
) -> dict:
    raw = {code: 0 for code in ALL_STRATEGIES}
    for row in rows:
        if str(row.get("received_at") or "").strip():
            continue
        parsed = parse_position_row(row)
        if parsed is not None:
            raw[parsed[0]] = parsed[1]
    active = dict(raw)
    state: dict = {
        "source_row_count": 0,
        "raw_positions": raw,
        "active_positions": active,
        "entry_prices": {},
        "shadow_boundary_keys": [],
        "started_at": text_time(cutoff),
    }
    actions: list[tuple[datetime, int, str, object]] = []
    for boundary in session_boundaries(bars):
        if boundary.record_time <= cutoff:
            actions.append((boundary.timestamp, 1, "boundary", boundary))
    source_count = 0
    for row_number, row in enumerate(rows, start=1):
        event = parse_signal_row(row, row_number)
        if event is None:
            source_count = row_number
            continue
        execution = next_minute_open(bars, event.timestamp)
        if execution is None or execution.record_time > cutoff:
            break
        actions.append((execution.bar_time, 0, "signal", (event, execution)))
        source_count = row_number

    for _, _, kind, payload in sorted(actions, key=lambda item: (item[0], item[1])):
        if kind == "boundary":
            apply_shadow_boundary(state, payload, persist=False, notify=False)
            raw = normalized_positions(state.get("raw_positions"))
            active = normalized_positions(state.get("active_positions"))
            continue
        event, execution = payload
        decision = apply_signal(raw, active, event, execution)
        record_transition(
            state,
            strategy_code=event.strategy_code,
            previous=decision.previous_position,
            target=decision.target_position,
            price=execution.open,
            timestamp=execution.bar_time,
            trigger="startup_rebuild",
            persist=False,
        )
        state["raw_positions"] = raw
        state["active_positions"] = active
    state["source_row_count"] = source_count
    save_json_atomic(STATE_PATH, state)
    write_position(state, "startup rebuild")
    return state


def process_shadow(
    state: dict,
    rows: list[dict[str, str]],
    bars: list[PriceBar],
    cutoff: datetime,
) -> None:
    processed_keys = set(_stored_keys(state.get("shadow_boundary_keys")))
    actions: list[tuple[datetime, int, str, object]] = []
    for boundary in session_boundaries(bars):
        if boundary.key not in processed_keys and boundary.record_time <= cutoff:
            actions.append((boundary.timestamp, 1, "boundary", boundary))

    previous_count = int(state.get("source_row_count") or 0)
    pending_count = previous_count
    for row_number, row in enumerate(rows[previous_count:], start=previous_count + 1):
        event = parse_signal_row(row, row_number)
        if event is None:
            parsed = parse_position_row(row)
            if parsed is not None:
                raw = normalized_positions(state.get("raw_positions"))
                raw[parsed[0]] = parsed[1]
                state["raw_positions"] = raw
            pending_count = row_number
            continue
        execution = next_minute_open(bars, event.timestamp)
        if execution is None or execution.record_time > cutoff:
            break
        actions.append((execution.bar_time, 0, "signal", (event, execution)))
        pending_count = row_number

    raw = normalized_positions(state.get("raw_positions"))
    active = normalized_positions(state.get("active_positions"))
    for _, _, kind, payload in sorted(actions, key=lambda item: (item[0], item[1])):
        if kind == "boundary":
            apply_shadow_boundary(state, payload)
            raw = normalized_positions(state.get("raw_positions"))
            active = normalized_positions(state.get("active_positions"))
            continue
        event, execution = payload
        decision = apply_signal(raw, active, event, execution)
        record_transition(
            state,
            strategy_code=event.strategy_code,
            previous=decision.previous_position,
            target=decision.target_position,
            price=execution.open,
            timestamp=execution.bar_time,
            trigger="ef_signal",
        )
        state["raw_positions"] = raw
        state["active_positions"] = active
        _append_signal_decision(decision)
        write_position(state, decision.reason)
        live_text = (
            "實單已在received_at收到時即時處理；本則補記下一分鐘影子成交"
            if env_flag(ENABLE_ORDERS_ENV)
            else "影子模式，未送實單"
        )
        message = _signal_message(decision, live_text)
        print(message)
        send_discord(message)
    state["source_row_count"] = pending_count
    state["raw_positions"] = raw
    state["active_positions"] = active
    save_json_atomic(STATE_PATH, state)


def initialize_live_cursor(state: dict, rows: list[dict[str, str]]) -> None:
    if state.get("live_source_row_count") is not None:
        return
    state["live_raw_positions"] = normalized_positions(state.get("raw_positions"))
    state["live_active_positions"] = normalized_positions(state.get("active_positions"))
    state["live_source_row_count"] = len(rows)
    state["live_boundary_keys"] = []
    state["live_cursor_initialized_at"] = text_time(now_local())
    save_json_atomic(STATE_PATH, state)


def process_live_rows(state: dict, rows: list[dict[str, str]]) -> None:
    if not env_flag(ENABLE_ORDERS_ENV):
        return
    previous_count = int(state.get("live_source_row_count") or 0)
    raw = normalized_positions(state.get("live_raw_positions"))
    active = normalized_positions(state.get("live_active_positions"))
    for row_number, row in enumerate(rows[previous_count:], start=previous_count + 1):
        event = parse_signal_row(row, row_number)
        if event is None:
            parsed = parse_position_row(row)
            if parsed is not None:
                raw[parsed[0]] = parsed[1]
            state["live_source_row_count"] = row_number
            continue
        intended = event.timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        synthetic = PriceBar(intended, event.timestamp, 0, 0)
        decision = apply_signal(raw, active, event, synthetic)
        state["live_raw_positions"] = raw
        state["live_active_positions"] = active
        state["live_source_row_count"] = row_number
        save_json_atomic(STATE_PATH, state)
        target = net_position(active)
        live_result = execute_live_target(
            state,
            target,
            trigger=f"immediate_ef_signal_row_{row_number}",
            event_key=f"signal:{row_number}",
        )
        message = (
            "🚨【EF雙時段風控｜即時實單】\n"
            f"收到時間：{text_time(event.timestamp)}\n"
            f"策略：{event.strategy_name or event.strategy_code}\n"
            f"原訊號：{event.previous_position} → {event.new_position}\n"
            f"最新目標：{position_text(target)}\n"
            f"原因：{decision.reason}\n"
            f"執行：{live_result}"
        )
        print(message)
        send_discord(message)
    state["live_raw_positions"] = raw
    state["live_active_positions"] = active
    state["live_source_row_count"] = len(rows)
    save_json_atomic(STATE_PATH, state)


def _live_boundary_due(
    state: dict, current: datetime, bars: list[PriceBar]
) -> tuple[str, str, datetime] | None:
    clock = current.time()
    today = current.date()
    keys = set(_stored_keys(state.get("live_boundary_keys")))
    morning_key = f"{today}:morning_flat"
    if clock_time(4, 59) <= clock < clock_time(5, 9):
        if morning_key not in keys and observed_night_session(bars, current):
            return morning_key, "morning_flat", current.replace(
                hour=4, minute=59, second=0, microsecond=0
            )
    day_key = f"{today}:day_flat"
    if clock_time(13, 44) <= clock < clock_time(13, 54):
        if day_key not in keys and observed_day_session(bars, today):
            return day_key, "day_flat", current.replace(
                hour=13, minute=44, second=0, microsecond=0
            )
    restore_key = f"{today}:night_restore"
    if clock_time(15, 0) <= clock < clock_time(15, 10):
        if restore_key not in keys and observed_day_session(bars, today):
            return restore_key, "night_restore", current.replace(
                hour=15, minute=0, second=0, microsecond=0
            )
    return None


def apply_live_clock_event(state: dict, current: datetime, bars: list[PriceBar]) -> bool:
    due = _live_boundary_due(state, current, bars)
    if due is None:
        return False
    event_key, kind, scheduled = due
    raw = normalized_positions(state.get("live_raw_positions"))
    active = normalized_positions(state.get("live_active_positions"))
    previous_net = net_position(active)
    if kind == "night_restore":
        active, _, target = restore_latest_positions(raw, active)
        description = "15:00恢復當下最新EF部位"
    else:
        active, _, target = flatten_positions(active)
        description = (
            "13:44清空；15:00將恢復最新EF部位"
            if kind == "day_flat"
            else "04:59清空；08:45不恢復，等待日盤新EF訊號"
        )
    state["live_active_positions"] = active
    keys = _stored_keys(state.get("live_boundary_keys"))
    keys.append(event_key)
    state["live_boundary_keys"] = keys
    save_json_atomic(STATE_PATH, state)
    live_result = execute_live_target(
        state,
        target,
        trigger=f"clock_{kind}",
        event_key=f"clock:{event_key}",
    )
    append_csv(
        CLOCK_PATH,
        CLOCK_FIELDS,
        {
            "scheduled_at": text_time(scheduled),
            "triggered_at": text_time(current),
            "event_key": event_key,
            "kind": kind,
            "mode": "live_dedicated_api" if env_flag(ENABLE_ORDERS_ENV) else "shadow_only",
            "previous_target": previous_net,
            "target_position": target,
            "result": live_result,
        },
    )
    message = (
        "⏰【EF雙時段風控｜時鐘排程】\n"
        f"排程：{text_time(scheduled)}；觸發：{text_time(current)}\n"
        f"事件：{kind}\n"
        f"目標：{position_text(previous_net)} → {position_text(target)}\n"
        f"規則：{description}\n"
        f"執行：{live_result}"
    )
    print(message)
    send_discord(message)
    write_position(state, description)
    return True


def main() -> None:
    load_env_file(ENV_PATH)
    poll_seconds = max(0.5, float(os.getenv("EF_DUAL_SESSION_POLL_SECONDS", "2")))
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("EF雙時段風控策略已有另一個實例執行中") from exc

    try:
        rows = load_signal_rows(SOURCE_PATH)
        bars = load_price_bars(PRICE_PATH)
        state = load_json(STATE_PATH, {})
        if state.get("source_row_count") is None or int(
            state.get("source_row_count") or 0
        ) > len(rows):
            state = _rebuild_state(rows, bars, now_local())

        orders_enabled = env_flag(ENABLE_ORDERS_ENV)
        if orders_enabled:
            initialize_live_cursor(state, rows)
            if env_flag(RECONCILE_ON_START_ENV):
                target = net_position(
                    normalized_positions(state.get("live_active_positions"))
                )
                startup_result = execute_live_target(
                    state,
                    target,
                    trigger="startup_reconcile",
                    event_key=f"startup:{text_time(now_local())}",
                )
            else:
                startup_result = "未啟用啟動對帳；等待下一筆新訊號或排程事件"
        else:
            startup_result = "影子模式，未連線券商"

        startup = (
            "✅【開始監控｜EF雙時段風控】\n"
            f"時間：{text_time(now_local())}\n"
            "規則一：13:44清空，15:00恢復當下最新EF部位。\n"
            "規則二：04:59清空，08:45不恢復，等待日盤新EF訊號。\n"
            "訊號影子價：received_at後下一個可用分鐘Open。\n"
            f"模式：{'專屬API永豐實單' if orders_enabled else '影子模式'}。\n"
            f"啟動：{startup_result}"
        )
        print(startup)
        send_discord(startup)

        while True:
            current = now_local()
            rows = load_signal_rows(SOURCE_PATH)
            bars = load_price_bars(PRICE_PATH)
            if orders_enabled:
                initialize_live_cursor(state, rows)
                # 先更新13:44後收到的EF原始訊號，再讓15:00恢復最新部位。
                process_live_rows(state, rows)
                apply_live_clock_event(state, current, bars)
            previous_count = int(state.get("source_row_count") or 0)
            if len(rows) < previous_count:
                state = _rebuild_state(rows, bars, current)
            else:
                process_shadow(state, rows, bars, current)
            time.sleep(poll_seconds)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
