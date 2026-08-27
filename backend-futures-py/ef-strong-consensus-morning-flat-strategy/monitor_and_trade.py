from __future__ import annotations

import csv
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from filelock import FileLock, Timeout

from auto_trade import execute_target_position
from strategy import (
    ALL_STRATEGIES,
    ConsensusDecision,
    PriceBar,
    evaluate_event,
    latest_morning_boundary,
    load_price_bars,
    load_signal_rows,
    morning_boundaries,
    next_minute_open,
    normalized_positions,
    parse_position_row,
    parse_signal_row,
    position_text,
    consensus_target,
    signal_is_in_morning_block,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"
SOURCE_PATH = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
PRICE_PATH = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
RECORDS_DIR = BASE_DIR / "records"
POSITION_PATH = RECORDS_DIR / "ef_strong_morning_flat_position.json"
DECISION_PATH = RECORDS_DIR / "ef_strong_morning_flat_decisions.csv"
TRADE_PATH = RECORDS_DIR / "ef_strong_morning_flat_shadow_trade.csv"
ORDER_ATTEMPT_PATH = RECORDS_DIR / "ef_strong_morning_flat_order_attempts.csv"
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
ORDER_ATTEMPT_FIELDS = [
    "timestamp", "attempt_id", "event", "trigger", "target_position",
    "previous_position", "actual_position", "side", "quantity", "detail",
]
ENABLE_ORDERS_ENV = "EF_STRONG_MORNING_FLAT_ENABLE_ORDERS"
POSITION_UNIT_ENV = "EF_STRONG_MORNING_FLAT_POSITION_UNIT"
MAX_POSITION_UNIT = 20


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


def position_unit() -> int:
    try:
        unit = int(os.getenv(POSITION_UNIT_ENV, "1"))
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
    return (
        os.getenv("DISCORD_EFSTRONG_MORNING_FLAT_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_EF_STRONG_MORNING_FLAT_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_MXF_ALERT_WEBHOOK_URL", "").strip()
    )


def send_discord(content: str) -> bool:
    webhook = webhook_url()
    if not webhook:
        print(
            "⚠️ 未設定 DISCORD_EFSTRONG_MORNING_FLAT_WEBHOOK_URL "
            "或 DISCORD_MXF_ALERT_WEBHOOK_URL"
        )
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
    append_csv(ORDER_ATTEMPT_PATH, ORDER_ATTEMPT_FIELDS, {
        "timestamp": text_time(now_local()),
        "attempt_id": attempt_id,
        "event": event,
        "trigger": trigger,
        "target_position": target,
        "previous_position": previous,
        "actual_position": actual,
        "side": side,
        "quantity": quantity,
        "detail": detail,
    })


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


def execute_live_target(
    state: dict,
    target: int,
    *,
    trigger: str,
    force_reconcile: bool = False,
) -> str:
    if not env_flag(ENABLE_ORDERS_ENV):
        return "影子模式，未送實單"

    broker_target = scaled_target(target)
    attempt_id = uuid.uuid4().hex

    attempted = state.get("last_order_attempt_target")
    try:
        attempted = int(attempted)
    except (TypeError, ValueError):
        attempted = None
    if not force_reconcile and attempted == broker_target:
        append_order_event(
            attempt_id=attempt_id,
            event="skipped_duplicate",
            trigger=trigger,
            target=broker_target,
            detail="相同目標已嘗試過，防重送",
        )
        return f"相同實單目標{position_text(broker_target)}已嘗試過，不重送"

    append_order_event(
        attempt_id=attempt_id,
        event="attempt_started",
        trigger=trigger,
        target=broker_target,
        detail="準備登入永豐、查詢TMF部位並對帳",
    )

    attempted_at = text_time(now_local())
    state["last_order_attempt_target"] = broker_target
    state["last_order_attempt_at"] = attempted_at
    state["last_order_trigger"] = trigger
    save_json_atomic(STATE_PATH, state)
    try:
        result = execute_target_position(broker_target)
    except Exception as exc:
        error_text = str(exc)
        append_order_event(
            attempt_id=attempt_id,
            event="failed",
            trigger=trigger,
            target=broker_target,
            detail=f"{type(exc).__name__}: {error_text}",
        )
        state["last_order_error_target"] = target
        state["last_order_error_at"] = attempted_at
        state["last_order_error"] = error_text
        save_json_atomic(STATE_PATH, state)
        return f"❌ 下單失敗：{error_text}（相同目標不自動重送）"

    state["last_executed_target"] = broker_target
    state["last_executed_at"] = text_time(now_local())
    state.pop("last_order_error_target", None)
    state.pop("last_order_error_at", None)
    state.pop("last_order_error", None)
    save_json_atomic(STATE_PATH, state)
    if result.order_sent:
        append_order_event(
            attempt_id=attempt_id,
            event="order_sent_confirmed",
            trigger=trigger,
            target=broker_target,
            previous=result.previous_position,
            actual=result.actual_position,
            side=result.side or "",
            quantity=result.quantity,
            detail="永豐委託完成且已回查目標部位",
        )
        action = "買進" if result.side == "buy" else "賣出"
        return (
            f"✅ 已送{action} TMF {result.quantity}口；"
            f"實際部位{position_text(result.previous_position)} → "
            f"{position_text(result.actual_position)}（已回查確認）"
        )
    append_order_event(
        attempt_id=attempt_id,
        event="no_order_needed",
        trigger=trigger,
        target=broker_target,
        previous=result.previous_position,
        actual=result.actual_position,
        quantity=0,
        detail="永豐帳戶原本已符合策略目標",
    )
    return f"帳戶已是{position_text(result.actual_position)}，無需送單"


def write_position(state: dict, reason: str) -> None:
    raw_positions = normalized_positions(state.get("raw_positions"))
    threshold = int(state.get("threshold") or 2)
    target, e_net, f_net, relation = consensus_target(raw_positions, threshold)
    save_json_atomic(
        POSITION_PATH,
        {
            "strategy": "EF Strong Consensus + Morning Flat",
            "mode": (
                "live_api_key" if env_flag(ENABLE_ORDERS_ENV) else "shadow_only"
            ),
            "position_unit": position_unit(),
            "broker_target_position": scaled_target(target),
            "rule": "E/F each net >=2; one contract; 04:59 flatten; wait for new signal",
            "e_net": e_net,
            "f_net": f_net,
            "raw_consensus_target": target,
            "relation": relation,
            "shadow_position": int(state.get("position") or 0),
            "entry_price": state.get("entry_price"),
            "threshold": threshold,
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


def decision_message(decision: ConsensusDecision, live_result: str) -> str:
    previous_final = scaled_target(decision.previous_position)
    target_final = scaled_target(decision.target_position)
    action = (
        "目標未變"
        if decision.previous_position == decision.target_position
        else f"{position_text(previous_final)} → {position_text(target_final)}"
    )
    return (
        "🚨【策略訊號｜EF強共識＋04:59清倉】\n"
        f"訊號時間：{text_time(decision.event.timestamp)}\n"
        f"收到訊號後【最終口數】：{position_text(target_final)}\n"
        f"模擬成交：{text_time(decision.execution_time)} @ {decision.execution_price:g}\n"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"E淨部位：{decision.e_net}；F淨部位：{decision.f_net}\n"
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
        "🚨【即時實單判斷｜EF強共識＋04:59清倉】\n"
        f"收到時間：{text_time(decision.event.timestamp)}\n"
        f"收到訊號後【最終口數】：{position_text(target_final)}\n"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"E淨部位：{decision.e_net}；F淨部位：{decision.f_net}\n"
        f"組合目標：{action}\n"
        f"原因：{decision.reason}\n"
        f"執行：{live_result}\n"
        "影子績效會在下一分鐘Open可用後另行補記。"
    )


def initialize_live_cursor(state: dict, rows: list[dict[str, str]]) -> None:
    if state.get("live_source_row_count") is not None:
        return
    positions = {code: 0 for code in ALL_STRATEGIES}
    for row in rows:
        parsed = parse_position_row(row)
        if parsed is not None:
            positions[parsed[0]] = parsed[1]
    state["live_raw_positions"] = positions
    state["live_source_row_count"] = len(rows)
    state["live_target_position"] = int(state.get("position") or 0)
    state["live_cursor_initialized_at"] = text_time(now_local())
    save_json_atomic(STATE_PATH, state)


def process_live_rows(
    state: dict,
    rows: list[dict[str, str]],
    threshold: int,
) -> None:
    if not env_flag(ENABLE_ORDERS_ENV):
        return
    previous_count = int(state.get("live_source_row_count") or 0)
    positions = normalized_positions(state.get("live_raw_positions"))
    current = int(state.get("live_target_position", state.get("position") or 0))
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
        decision = evaluate_event(
            positions,
            current,
            event,
            intended_bar,
            threshold=threshold,
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
    save_json_atomic(STATE_PATH, state)


def apply_live_clock_flatten(state: dict, current_time: datetime) -> None:
    if not env_flag(ENABLE_ORDERS_ENV):
        return
    boundary = current_time.replace(hour=4, minute=59, second=0, microsecond=0)
    reopen = current_time.replace(hour=8, minute=45, second=0, microsecond=0)
    if not boundary <= current_time < reopen:
        return
    if str(state.get("last_live_flat_time") or "").startswith(boundary.strftime("%Y-%m-%d")):
        return
    previous = int(state.get("live_target_position", state.get("position") or 0))
    state["live_target_position"] = 0
    state["last_live_flat_time"] = text_time(boundary)
    save_json_atomic(STATE_PATH, state)
    result = execute_live_target(
        state,
        0,
        trigger="04:59_live_clock_flat",
        force_reconcile=True,
    )
    message = (
        "🌅【04:59即時實單清倉｜EF強共識】\n"
        f"排程時間：{text_time(boundary)}\n"
        "收到訊號後【最終口數】：空手\n"
        f"組合目標：{position_text(scaled_target(previous))} → 空手\n"
        f"執行：{result}\n"
        "08:45不自動恢復，等待新的E/F訊號。"
    )
    print(message)
    send_discord(message)


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
        trigger="04:59_morning_flat",
        persist=persist,
    )
    state["last_flat_time"] = text_time(boundary.bar_time)
    reason = "04:59清空強共識組合部位；08:45不自動恢復，等待新EF訊號"
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
    if notify:
        live_result = (
            "實單已由04:59時鐘排程獨立處理"
            if env_flag(ENABLE_ORDERS_ENV)
            else "影子模式，未送實單"
        )
        message = (
            "🌅【04:59清倉｜EF強共識】\n"
            f"時間：{text_time(boundary.bar_time)}\n"
            "收到訊號後【最終口數】：空手\n"
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
            decision = evaluate_event(
                raw_positions, current, event, execution_bar, threshold=threshold
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
    save_json_atomic(STATE_PATH, state)
    write_position(state, "startup rebuild")
    return state


def process_new_rows(
    state: dict,
    rows: list[dict[str, str]],
    bars: list[PriceBar],
    cutoff: datetime,
    threshold: int,
) -> None:
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
        decision = evaluate_event(
            raw_positions, previous, event, execution_bar, threshold=threshold
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
        message = decision_message(decision, live_result)
        print(message)
        send_discord(message)
    apply_due_flatten(state, bars, cutoff)
    state["raw_positions"] = raw_positions
    save_json_atomic(STATE_PATH, state)


def main() -> None:
    load_env_file(ENV_PATH)
    poll_seconds = max(
        0.5, float(os.getenv("EF_STRONG_MORNING_FLAT_POLL_SECONDS", "2"))
    )
    threshold = int(os.getenv("EF_STRONG_MORNING_FLAT_MIN_GROUP_NET", "2"))
    if not 1 <= threshold <= 6:
        raise ValueError("EF_STRONG_MORNING_FLAT_MIN_GROUP_NET必須是1到6")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("EF強共識＋04:59清倉 Shadow已有另一個實例執行中") from exc

    try:
        rows = load_signal_rows(SOURCE_PATH)
        bars = load_price_bars(PRICE_PATH)
        state = load_json(STATE_PATH, {})
        previous_count = state.get("source_row_count")
        if previous_count is None or int(previous_count) > len(rows):
            state = initialize_state(rows, bars, now_local(), threshold)
        else:
            state["threshold"] = threshold
            save_json_atomic(STATE_PATH, state)
            write_position(state, "startup threshold sync")

        unit = position_unit()
        startup_result = ""
        startup_base_target = int(state.get("position") or 0)
        if env_flag(ENABLE_ORDERS_ENV):
            initialize_live_cursor(state, rows)
            current_time = now_local()
            if signal_is_in_morning_block(current_time, current_time):
                state["live_target_position"] = 0
                state["last_live_flat_time"] = text_time(
                    current_time.replace(hour=4, minute=59, second=0, microsecond=0)
                )
                save_json_atomic(STATE_PATH, state)
            startup_base_target = int(state.get("live_target_position") or 0)
            startup_result = execute_live_target(
                state,
                startup_base_target,
                trigger="startup_reconcile",
                force_reconcile=True,
            )
        startup_message = (
            "✅【開始監控｜EF強共識＋04:59清倉】\n"
            f"時間：{text_time(now_local())}\n"
            f"收到訊號後【最終口數】：{position_text(scaled_target(startup_base_target))}\n"
            f"規則：E/F兩組淨部位皆至少{threshold}票同向才成立；U={unit}。\n"
            "04:59清倉；08:45不自動恢復，等新EF訊號再判斷。\n"
            "成交：received_at後嚴格下一根1分K開盤價。\n"
            f"模式：{'API_KEY永豐實單' if env_flag(ENABLE_ORDERS_ENV) else '影子模式'}。"
        )
        if startup_result:
            startup_message += f"\n啟動對帳：{startup_result}"
        print(startup_message)
        send_discord(startup_message)

        while True:
            cutoff = now_local()
            rows = load_signal_rows(SOURCE_PATH)
            bars = load_price_bars(PRICE_PATH)
            if env_flag(ENABLE_ORDERS_ENV):
                initialize_live_cursor(state, rows)
                apply_live_clock_flatten(state, cutoff)
                process_live_rows(state, rows, threshold)
            previous_count = int(state.get("source_row_count") or 0)
            if len(rows) < previous_count:
                state = initialize_state(rows, bars, cutoff, threshold)
            else:
                process_new_rows(state, rows, bars, cutoff, threshold)
            time.sleep(poll_seconds)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
