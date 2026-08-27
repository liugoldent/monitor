from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from filelock import FileLock, Timeout

from strategy import (
    ALL_STRATEGIES,
    PriceBar,
    ShadowDecision,
    apply_signal,
    latest_morning_boundary,
    load_price_bars,
    load_signal_rows,
    morning_boundaries,
    net_position,
    next_minute_open,
    normalized_positions,
    parse_signal_row,
    position_text,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"
SOURCE_PATH = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
PRICE_PATH = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
RECORDS_DIR = BASE_DIR / "records"
POSITION_PATH = RECORDS_DIR / "ef_morning_flat_position.json"
DECISION_PATH = RECORDS_DIR / "ef_morning_flat_decisions.csv"
TRADE_PATH = RECORDS_DIR / "ef_morning_flat_shadow_trade.csv"
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "ef_morning_flat_state.json"
LOCK_PATH = RUNTIME_DIR / "ef_morning_flat.lock"
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
    "previous_shadow_position",
    "shadow_position",
    "previous_net_position",
    "net_position",
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


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
        os.getenv("DISCORD_EF_MORNING_FLAT_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_MXF_ALERT_WEBHOOK_URL", "").strip()
    )


def send_discord(content: str) -> bool:
    webhook = webhook_url()
    if not webhook:
        print("⚠️ 未設定 DISCORD_EF_MORNING_FLAT_WEBHOOK_URL 或 DISCORD_MXF_ALERT_WEBHOOK_URL")
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


def entry_prices(state: dict) -> dict[str, float]:
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
    entries = entry_prices(state)
    timestamp_text = text_time(timestamp)
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
                    "timestamp": timestamp_text,
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
                    "timestamp": timestamp_text,
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
    raw_positions = normalized_positions(state.get("raw_positions"))
    shadow_positions = normalized_positions(state.get("shadow_positions"))
    save_json_atomic(
        POSITION_PATH,
        {
            "strategy": "EF Morning Flat",
            "mode": "shadow_only",
            "rule": "04:59 flatten; no automatic 08:45 restore; wait for new signal",
            "raw_net_position": net_position(raw_positions),
            "shadow_net_position": net_position(shadow_positions),
            "raw_positions": raw_positions,
            "shadow_positions": shadow_positions,
            "last_flat_time": state.get("last_flat_time", ""),
            "last_reason": reason,
            "updated_at": text_time(now_local()),
        },
    )


def append_signal_decision(decision: ShadowDecision) -> None:
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
            "previous_shadow_position": decision.previous_shadow_position,
            "shadow_position": decision.shadow_position,
            "previous_net_position": decision.previous_net_position,
            "net_position": decision.net_position,
            "reason": decision.reason,
        },
    )


def decision_message(decision: ShadowDecision) -> str:
    return (
        "🧪【EF 04:59清倉 Shadow】\n"
        f"訊號時間：{text_time(decision.event.timestamp)}\n"
        f"模擬成交：{text_time(decision.execution_time)} @ "
        f"{decision.execution_price:g}\n"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"影子部位：{decision.previous_shadow_position} → {decision.shadow_position}\n"
        f"影子總倉：{position_text(decision.previous_net_position)} → "
        f"{position_text(decision.net_position)}\n"
        f"原因：{decision.reason}\n"
        "備註：只記錄影子績效，不送實單。"
    )


def apply_flatten_bar(
    state: dict,
    boundary: PriceBar,
    *,
    persist: bool = True,
    notify: bool = True,
) -> bool:
    shadow_positions = normalized_positions(state.get("shadow_positions"))
    previous_net = net_position(shadow_positions)
    changed = False
    for code in ALL_STRATEGIES:
        previous = shadow_positions[code]
        if not previous:
            continue
        record_transition(
            state,
            strategy_code=code,
            previous=previous,
            target=0,
            price=boundary.open,
            timestamp=boundary.bar_time,
            trigger="04:59_morning_flat",
            persist=persist,
        )
        shadow_positions[code] = 0
        changed = True
    state["shadow_positions"] = shadow_positions
    state["last_flat_time"] = text_time(boundary.bar_time)
    reason = "04:59休盤前清空全部EF影子部位；08:45不自動恢復"
    if persist:
        append_csv(
            DECISION_PATH,
            DECISION_FIELDS,
            {
                "timestamp": text_time(boundary.bar_time),
                "kind": "scheduled_flatten",
                "execution_time": text_time(boundary.bar_time),
                "execution_price": boundary.open,
                "previous_net_position": previous_net,
                "net_position": 0,
                "reason": reason,
            },
        )
        write_position(state, reason)
    if notify:
        message = (
            "🌅【04:59清倉｜EF Morning Flat Shadow】\n"
            f"時間：{text_time(boundary.bar_time)}\n"
            f"模擬成交價：{boundary.open:g}\n"
            f"影子總倉：{position_text(previous_net)} → 空手\n"
            "08:45不自動恢復舊倉，等新進場／反轉訊號。\n"
            "備註：只記錄影子績效，不送實單。"
        )
        print(message)
        send_discord(message)
    return changed


def apply_due_flatten(state: dict, bars: list[PriceBar], through: datetime) -> None:
    last_text = str(state.get("last_flat_time") or "").strip()
    try:
        last_flat = datetime.strptime(last_text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        last_flat = None
    for boundary in morning_boundaries(bars, after=last_flat, through=through):
        apply_flatten_bar(state, boundary)


def initialize_state(
    rows: list[dict[str, str]], bars: list[PriceBar], cutoff: datetime
) -> dict:
    processed: list[tuple[object, PriceBar]] = []
    source_row_count = 0
    for row_number, row in enumerate(rows, start=1):
        event = parse_signal_row(row, row_number)
        if event is None:
            source_row_count = row_number
            continue
        execution_bar = next_minute_open(bars, event.timestamp)
        if execution_bar is None or execution_bar.record_time > cutoff:
            break
        processed.append((event, execution_bar))
        source_row_count = row_number

    raw_positions = {code: 0 for code in ALL_STRATEGIES}
    for event, _ in processed:
        raw_positions[event.strategy_code] = event.new_position

    boundary = latest_morning_boundary(bars, cutoff)
    shadow_positions = {code: 0 for code in ALL_STRATEGIES}
    state: dict = {
        "source_row_count": source_row_count,
        "raw_positions": raw_positions,
        "shadow_positions": shadow_positions,
        "entry_prices": {},
        "last_flat_time": "" if boundary is None else text_time(boundary.bar_time),
        "started_at": text_time(cutoff),
    }
    replay_start = datetime.min if boundary is None else boundary.bar_time
    for event, execution_bar in processed:
        if execution_bar.bar_time <= replay_start:
            continue
        decision = apply_signal(shadow_positions, event, execution_bar)
        record_transition(
            state,
            strategy_code=event.strategy_code,
            previous=decision.previous_shadow_position,
            target=decision.shadow_position,
            price=execution_bar.open,
            timestamp=execution_bar.bar_time,
            trigger="startup_rebuild",
            persist=False,
        )
    state["shadow_positions"] = shadow_positions
    save_json_atomic(STATE_PATH, state)
    write_position(state, "startup rebuild")
    return state


def process_new_rows(
    state: dict,
    rows: list[dict[str, str]],
    bars: list[PriceBar],
    cutoff: datetime,
) -> None:
    previous_count = int(state.get("source_row_count") or 0)
    raw_positions = normalized_positions(state.get("raw_positions"))
    shadow_positions = normalized_positions(state.get("shadow_positions"))
    for row_number, row in enumerate(rows[previous_count:], start=previous_count + 1):
        event = parse_signal_row(row, row_number)
        if event is None:
            state["source_row_count"] = row_number
            continue
        execution_bar = next_minute_open(bars, event.timestamp)
        if execution_bar is None or execution_bar.record_time > cutoff:
            break
        apply_due_flatten(state, bars, execution_bar.record_time)
        shadow_positions = normalized_positions(state.get("shadow_positions"))
        raw_positions[event.strategy_code] = event.new_position
        decision = apply_signal(shadow_positions, event, execution_bar)
        record_transition(
            state,
            strategy_code=event.strategy_code,
            previous=decision.previous_shadow_position,
            target=decision.shadow_position,
            price=execution_bar.open,
            timestamp=execution_bar.bar_time,
            trigger="ef_signal",
        )
        state["raw_positions"] = raw_positions
        state["shadow_positions"] = shadow_positions
        state["source_row_count"] = row_number
        append_signal_decision(decision)
        write_position(state, decision.reason)
        message = decision_message(decision)
        print(message)
        send_discord(message)
    apply_due_flatten(state, bars, cutoff)
    state["raw_positions"] = raw_positions
    state["shadow_positions"] = normalized_positions(state.get("shadow_positions"))
    save_json_atomic(STATE_PATH, state)


def main() -> None:
    load_env_file(ENV_PATH)
    poll_seconds = max(0.5, float(os.getenv("EF_MORNING_FLAT_POLL_SECONDS", "2")))
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("EF Morning Flat Shadow已有另一個實例執行中") from exc

    try:
        rows = load_signal_rows(SOURCE_PATH)
        bars = load_price_bars(PRICE_PATH)
        state = load_json(STATE_PATH, {})
        previous_count = state.get("source_row_count")
        if previous_count is None or int(previous_count) > len(rows):
            state = initialize_state(rows, bars, now_local())

        startup_message = (
            "✅【開始監控｜EF Morning Flat Shadow】\n"
            f"時間：{text_time(now_local())}\n"
            "規則：04:59清倉；08:45不自動恢復，等新訊號再進。\n"
            "成交價：訊號嚴格下一根1分K開盤價。\n"
            "13:45～15:00不清倉；只記錄影子績效，不送實單。"
        )
        print(startup_message)
        send_discord(startup_message)

        while True:
            cutoff = now_local()
            rows = load_signal_rows(SOURCE_PATH)
            bars = load_price_bars(PRICE_PATH)
            previous_count = int(state.get("source_row_count") or 0)
            if len(rows) < previous_count:
                state = initialize_state(rows, bars, cutoff)
            else:
                process_new_rows(state, rows, bars, cutoff)
            time.sleep(poll_seconds)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
