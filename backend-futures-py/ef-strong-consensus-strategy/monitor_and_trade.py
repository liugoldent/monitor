from __future__ import annotations

import csv
import fcntl
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

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
    return (
        os.getenv("DISCORD_EF_STRONG_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_MXF_ALERT_WEBHOOK_URL", "").strip()
    )


def send_discord(content: str) -> bool:
    webhook = discord_webhook()
    if not webhook:
        print("⚠️ 未設定 DISCORD_EF_STRONG_WEBHOOK_URL 或 DISCORD_MXF_ALERT_WEBHOOK_URL")
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
    if not decision.ready or decision.target_position is None:
        print(decision_text(decision, threshold))
        return

    previous = state.get("last_simulated_target")
    if previous is None:
        previous = 0
    previous = int(previous)
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
    if target == previous:
        print(f"[{now_text()}] {decision_text(decision, threshold)}｜維持不動")
        return

    observed_at = now()
    action, side, quantity = simulated_order_action(previous, target)
    message = (
        "🧪【模擬下單｜純EF強共識】\n"
        f"時間：{now_text()}\n"
        f"動作：{action}，{side}微型台指近一（TMF）{quantity}口\n"
        f"模擬部位：{position_text(previous)} → {position_text(target)}\n"
        f"觸發：{trigger}\n"
        f"判斷：{decision_text(decision, threshold)}\n"
        "備註：本策略完全不使用H，目前只有Discord模擬與獨立績效紀錄。"
    )
    print(message)
    if send_discord(message):
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
    if state.get("source_row_count") is None:
        decision = evaluate_records(threshold, unit)
        write_position(decision, threshold, unit, "首次啟動重建")
        if decision.ready and decision.target_position is not None:
            if env_flag("EF_STRONG_SIMULATE_ON_START", False):
                process_batch(state, rows, [], threshold, unit)
            else:
                state["last_simulated_target"] = decision.target_position
        state["source_row_count"] = len(rows)
        save_json_atomic(STATE_PATH, state)

    send_discord(
        "✅【開始監控｜純EF強共識】\n"
        f"時間：{now_text()}\n強共識門檻：E、F各至少淨{threshold}票\n"
        f"單位：{unit}口\n本策略不使用H，預設僅Discord模擬。"
    )
    print("=== 純 EF 強共識 Discord 模擬監控 ===")
    print(f"source={SOURCE_PATH} threshold={threshold} U={unit}")

    while True:
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
