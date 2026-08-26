from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from filelock import FileLock, Timeout

from strategy import (
    ALL_STRATEGIES,
    FilterDecision,
    apply_event,
    latest_recorded_price,
    latest_rsi_snapshot,
    load_recorded_prices,
    load_rsi_snapshots,
    load_signal_rows,
    net_position,
    parse_signal_row,
    position_text,
    replay_events,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"
SOURCE_PATH = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
PRICE_PATH = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
RECORDS_DIR = BASE_DIR / "records"
POSITION_PATH = RECORDS_DIR / "rsi60_position.json"
DECISION_PATH = RECORDS_DIR / "rsi60_decisions.csv"
TRADE_PATH = RECORDS_DIR / "rsi60_shadow_trade.csv"
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "rsi60_state.json"
LOCK_PATH = RUNTIME_DIR / "rsi60.lock"
TZ = ZoneInfo("Asia/Taipei")
DECISION_FIELDS = [
    "timestamp",
    "source_row",
    "strategy_code",
    "strategy_name",
    "raw_previous_position",
    "raw_new_position",
    "rsi_bar_time",
    "rsi14",
    "allowed",
    "previous_filtered_position",
    "filtered_position",
    "previous_net_position",
    "net_position",
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


def now_text() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


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


def normalized_positions(value: object) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    result = {code: 0 for code in ALL_STRATEGIES}
    for code in ALL_STRATEGIES:
        try:
            position = int(source.get(code, 0))
        except (TypeError, ValueError):
            position = 0
        result[code] = position if position in {-1, 0, 1} else 0
    return result


def decision_row(decision: FilterDecision) -> dict[str, object]:
    return {
        "timestamp": decision.event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "source_row": decision.event.row_number,
        "strategy_code": decision.event.strategy_code,
        "strategy_name": decision.event.strategy_name,
        "raw_previous_position": decision.event.previous_position,
        "raw_new_position": decision.event.new_position,
        "rsi_bar_time": (
            "" if decision.rsi_bar_time is None else decision.rsi_bar_time.strftime("%Y-%m-%d %H:%M:%S")
        ),
        "rsi14": "" if decision.rsi is None else f"{decision.rsi:.6f}",
        "allowed": "" if decision.allowed is None else decision.allowed,
        "previous_filtered_position": decision.previous_filtered_position,
        "filtered_position": decision.filtered_position,
        "previous_net_position": decision.previous_net_position,
        "net_position": decision.net_position,
        "reason": decision.reason,
    }


def write_position(positions: dict[str, int], decision: FilterDecision | None) -> None:
    save_json_atomic(
        POSITION_PATH,
        {
            "strategy": "EF RSI60 Filter",
            "mode": "shadow_only",
            "rule": "bull RSI14>=50; bear RSI14<=50 on last completed 60m bar",
            "net_position": net_position(positions),
            "filtered_positions": positions,
            "last_reason": "startup rebuild" if decision is None else decision.reason,
            "updated_at": now_text(),
        },
    )


def webhook_url() -> str:
    return (
        os.getenv("DISCORD_EF_RSI60_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_MXF_ALERT_WEBHOOK_URL", "").strip()
    )


def send_discord(content: str) -> bool:
    webhook = webhook_url()
    if not webhook:
        print("⚠️ 未設定 DISCORD_EF_RSI60_WEBHOOK_URL 或 DISCORD_MXF_ALERT_WEBHOOK_URL")
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
        print(f"❌ Discord通知失敗：{str(exc).replace(webhook, '<Discord webhook>')}")
        return False


def append_shadow_transition(
    state: dict,
    previous_target: int,
    target: int,
    price: float | None,
    timestamp: datetime,
) -> None:
    if previous_target == target:
        return
    timestamp_text = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    previous_entry = state.get("shadow_entry_price")
    if previous_target:
        pnl_points: float | str = ""
        pnl_twd: float | str = ""
        if price is not None and previous_entry not in {None, ""}:
            direction = 1 if previous_target > 0 else -1
            pnl_points = round(
                (price - float(previous_entry)) * direction * abs(previous_target), 2
            )
            pnl_twd = round(float(pnl_points) * 10, 2)
        append_csv(
            TRADE_PATH,
            TRADE_FIELDS,
            {
                "timestamp": timestamp_text,
                "action": "exiting",
                "side": "bull" if previous_target > 0 else "bear",
                "price": "" if price is None else price,
                "pnl_points": pnl_points,
                "pnl_twd": pnl_twd,
                "quantity": abs(previous_target),
            },
        )
    if target:
        append_csv(
            TRADE_PATH,
            TRADE_FIELDS,
            {
                "timestamp": timestamp_text,
                "action": "enter",
                "side": "bull" if target > 0 else "bear",
                "price": "" if price is None else price,
                "pnl_points": "",
                "pnl_twd": "",
                "quantity": abs(target),
            },
        )
        state["shadow_entry_price"] = price
    else:
        state["shadow_entry_price"] = None


def decision_message(decision: FilterDecision) -> str:
    if decision.allowed is True:
        outcome = "✅ 允許進場"
    elif decision.allowed is False:
        outcome = "⛔ 阻擋進場"
    else:
        outcome = "🔄 同步出場／維持"
    rsi_text = "無可用RSI" if decision.rsi is None else f"RSI60={decision.rsi:.2f}"
    return (
        "🧪【六策略 RSI60 Shadow】\n"
        f"時間：{decision.event.timestamp:%Y-%m-%d %H:%M:%S}\n"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"判定：{outcome}，{rsi_text}\n"
        f"影子總倉：{position_text(decision.previous_net_position)} → "
        f"{position_text(decision.net_position)}\n"
        f"原因：{decision.reason}\n"
        "備註：只記錄影子績效，不查詢部位、不送實單。"
    )


def rebuild_state(rows: list[dict[str, str]], events, threshold: float) -> dict:
    snapshots = load_rsi_snapshots(PRICE_PATH)
    positions, _ = replay_events(events, snapshots, threshold=threshold)
    state = {
        "source_row_count": len(rows),
        "filtered_positions": positions,
        "last_shadow_target": net_position(positions),
        "shadow_entry_price": None,
        "rebuilt_at": now_text(),
    }
    write_position(positions, None)
    save_json_atomic(STATE_PATH, state)
    return state


def process_new_rows(
    state: dict,
    rows: list[dict[str, str]],
    previous_count: int,
    threshold: float,
) -> None:
    positions = normalized_positions(state.get("filtered_positions"))
    snapshots = load_rsi_snapshots(PRICE_PATH)
    price_times, prices = load_recorded_prices(PRICE_PATH)
    for row_number, row in enumerate(rows[previous_count:], start=previous_count + 1):
        event = parse_signal_row(row, row_number)
        if event is None:
            continue
        snapshot = latest_rsi_snapshot(snapshots, event.timestamp)
        decision = apply_event(positions, event, snapshot, threshold=threshold)
        append_csv(DECISION_PATH, DECISION_FIELDS, decision_row(decision))
        price = latest_recorded_price(price_times, prices, event.timestamp)
        append_shadow_transition(
            state,
            decision.previous_net_position,
            decision.net_position,
            price,
            event.timestamp,
        )
        write_position(positions, decision)
        message = decision_message(decision)
        print(message)
        send_discord(message)
        state["last_shadow_target"] = decision.net_position
    state["filtered_positions"] = positions
    state["source_row_count"] = len(rows)
    save_json_atomic(STATE_PATH, state)


def main() -> None:
    load_env_file(ENV_PATH)
    threshold = float(os.getenv("EF_RSI60_THRESHOLD", "50"))
    if not 0 < threshold < 100:
        raise ValueError("EF_RSI60_THRESHOLD 必須介於0與100之間")
    poll_seconds = max(0.5, float(os.getenv("EF_RSI60_POLL_SECONDS", "2")))
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("六策略RSI60 Shadow已有另一個實例執行中") from exc

    try:
        rows, events = load_signal_rows(SOURCE_PATH)
        state = load_json(STATE_PATH, {})
        previous_count = state.get("source_row_count")
        if previous_count is None or int(previous_count) > len(rows):
            state = rebuild_state(rows, events, threshold)

        send_discord(
            "✅【開始監控｜六策略 RSI60 Shadow】\n"
            f"時間：{now_text()}\nRSI門檻：{threshold:g}\n"
            "規則：多單RSI60≥門檻、空單RSI60≤門檻；僅影子記錄，不送實單。"
        )
        print("=== 六策略 RSI60 Shadow 監控 ===")
        print(f"source={SOURCE_PATH} prices={PRICE_PATH} threshold={threshold:g}")

        while True:
            rows, events = load_signal_rows(SOURCE_PATH)
            previous_count = int(state.get("source_row_count") or 0)
            if len(rows) < previous_count:
                state = rebuild_state(rows, events, threshold)
            elif len(rows) > previous_count:
                process_new_rows(state, rows, previous_count, threshold)
            time.sleep(poll_seconds)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
