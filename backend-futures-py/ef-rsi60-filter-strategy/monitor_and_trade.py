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

from auto_trade import execute_target_position
from strategy import (
    ALL_STRATEGIES,
    FilterDecision,
    apply_event,
    latest_rsi_snapshot,
    load_execution_bars,
    load_rsi_snapshots,
    load_signal_rows,
    net_position,
    next_minute_open,
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
ENABLE_ORDERS_ENV = "EF_RSI60_ENABLE_ORDERS"


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
            "mode": (
                "live_api_key2" if env_flag(ENABLE_ORDERS_ENV) else "shadow_only"
            ),
            "rule": "bull RSI14>=50; bear RSI14<=50 on last completed 60m bar",
            "net_position": net_position(positions),
            "filtered_positions": positions,
            "last_reason": "startup rebuild" if decision is None else decision.reason,
            "updated_at": now_text(),
        },
    )


def webhook_url() -> str:
    return (
        os.getenv("DISCORD_EF_RSIFILTER_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_EF_RSI60_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_MXF_ALERT_WEBHOOK_URL", "").strip()
    )


def send_discord(content: str) -> bool:
    webhook = webhook_url()
    if not webhook:
        print(
            "⚠️ 未設定 DISCORD_EF_RSIFILTER_WEBHOOK_URL、"
            "DISCORD_EF_RSI60_WEBHOOK_URL 或 DISCORD_MXF_ALERT_WEBHOOK_URL"
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


def decision_message(
    decision: FilterDecision,
    *,
    execution_time: datetime | None = None,
    execution_price: float | None = None,
) -> str:
    if decision.allowed is True:
        outcome = "✅ 允許進場"
    elif decision.allowed is False:
        outcome = "⛔ 阻擋進場"
    else:
        outcome = "🔄 同步出場／維持"
    rsi_text = "無可用RSI" if decision.rsi is None else f"RSI60={decision.rsi:.2f}"
    execution_text = ""
    if execution_time is not None and execution_price is not None:
        execution_label = (
            "影子模擬成交"
            if decision.previous_filtered_position != decision.filtered_position
            else "模擬計價基準（未成交）"
        )
        execution_text = (
            f"{execution_label}：{execution_time:%Y-%m-%d %H:%M:%S} @ "
            f"{execution_price:g}（收到訊號後下一分鐘1分K Open）\n"
        )
    return (
        "🧪【六策略 RSI60 Shadow】\n"
        f"收到時間：{decision.event.timestamp:%Y-%m-%d %H:%M:%S}\n"
        f"{execution_text}"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"判定：{outcome}，{rsi_text}\n"
        f"影子總倉：{position_text(decision.previous_net_position)} → "
        f"{position_text(decision.net_position)}\n"
        f"原因：{decision.reason}\n"
        "備註：只記錄影子績效，不查詢部位、不送實單。"
    )


def immediate_decision_message(decision: FilterDecision, live_result: str) -> str:
    if decision.allowed is True:
        outcome = "✅ 允許進場"
    elif decision.allowed is False:
        outcome = "⛔ 阻擋進場"
    else:
        outcome = "🔄 同步出場／維持"
    rsi_text = "無可用RSI" if decision.rsi is None else f"RSI60={decision.rsi:.2f}"
    return (
        "🚨【RSI60即時判斷｜API_KEY2實單】\n"
        f"收到時間：{decision.event.timestamp:%Y-%m-%d %H:%M:%S}\n"
        f"策略：{decision.event.strategy_name or decision.event.strategy_code} "
        f"({decision.event.strategy_code})\n"
        f"原訊號：{decision.event.previous_position} → {decision.event.new_position}\n"
        f"判定：{outcome}，{rsi_text}\n"
        f"完整淨目標：{position_text(decision.previous_net_position)} → "
        f"{position_text(decision.net_position)}\n"
        f"原因：{decision.reason}\n"
        f"實單結果：{live_result}\n"
        "影子績效的下一分鐘Open會稍後另行補記。"
    )


def execute_live_for_decision(state: dict, decision: FilterDecision) -> str:
    """Execute immediately; never wait for the next one-minute bar."""
    if not env_flag(ENABLE_ORDERS_ENV):
        return "影子模式，未送實單"

    target = decision.net_position
    started = bool(state.get("live_started"))
    previous_attempt = state.get("last_order_attempt_target")
    try:
        previous_attempt = int(previous_attempt)
    except (TypeError, ValueError):
        previous_attempt = None

    # The first EF signal after activation always reconciles the account, even
    # if RSI blocks that child signal and the filtered target itself is unchanged.
    if started and previous_attempt == target:
        return f"完整目標仍為{position_text(target)}，已嘗試過相同目標，不重送"

    attempted_at = now_text()
    state["live_started"] = True
    state["last_order_attempt_target"] = target
    state["last_order_attempt_at"] = attempted_at
    save_json_atomic(STATE_PATH, state)
    try:
        result = execute_target_position(target)
    except Exception as exc:
        error_text = str(exc)
        state["last_order_error_target"] = target
        state["last_order_error_at"] = attempted_at
        state["last_order_error"] = error_text
        save_json_atomic(STATE_PATH, state)
        return f"❌ 下單失敗：{error_text}（相同目標不自動重送）"

    state["last_executed_target"] = target
    state["last_executed_at"] = now_text()
    state.pop("last_order_error_target", None)
    state.pop("last_order_error_at", None)
    state.pop("last_order_error", None)
    save_json_atomic(STATE_PATH, state)
    if result.order_sent:
        action = "買進" if result.side == "buy" else "賣出"
        return (
            f"✅ 已送{action} TMF {result.quantity}口；"
            f"實際部位{position_text(result.previous_position)} → "
            f"{position_text(result.actual_position)}（已回查確認）"
        )
    return f"帳戶已是{position_text(result.actual_position)}，無需送單"


def rebuild_state(rows: list[dict[str, str]], events, threshold: float) -> dict:
    snapshots = load_rsi_snapshots(PRICE_PATH)
    positions, _ = replay_events(events, snapshots, threshold=threshold)
    state = {
        "source_row_count": len(rows),
        "filtered_positions": positions,
        "last_shadow_target": net_position(positions),
        "shadow_entry_price": None,
        "pending_shadow_fills": [],
        "live_started": False,
        "orders_armed_after_row": len(rows),
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
    processed_count = previous_count
    for row_number, row in enumerate(rows[previous_count:], start=previous_count + 1):
        event = parse_signal_row(row, row_number)
        if event is None:
            processed_count = row_number
            continue
        snapshot = latest_rsi_snapshot(snapshots, event.timestamp)
        decision = apply_event(positions, event, snapshot, threshold=threshold)
        append_csv(DECISION_PATH, DECISION_FIELDS, decision_row(decision))
        write_position(positions, decision)
        pending = state.get("pending_shadow_fills")
        if not isinstance(pending, list):
            pending = []
        if decision.previous_net_position != decision.net_position:
            pending.append(
                {
                    "source_row": row_number,
                    "received_at": event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "strategy_code": event.strategy_code,
                    "previous_target": decision.previous_net_position,
                    "target": decision.net_position,
                }
            )
        state["pending_shadow_fills"] = pending
        state["filtered_positions"] = positions
        state["source_row_count"] = row_number
        # Persist the decision and cursor before contacting the broker.  The
        # live adapter also stores its target before placing an order, so a
        # crash cannot duplicate the same target order.
        save_json_atomic(STATE_PATH, state)
        live_result = execute_live_for_decision(state, decision)
        message = immediate_decision_message(decision, live_result)
        print(message)
        send_discord(message)
        processed_count = row_number
    state["filtered_positions"] = positions
    state["source_row_count"] = processed_count
    save_json_atomic(STATE_PATH, state)


def process_pending_shadow_fills(state: dict) -> None:
    pending = state.get("pending_shadow_fills")
    if not isinstance(pending, list) or not pending:
        return
    bars = load_execution_bars(PRICE_PATH)
    remaining = list(pending)
    changed = False
    while remaining:
        item = remaining[0]
        try:
            received_at = datetime.strptime(str(item["received_at"]), "%Y-%m-%d %H:%M:%S")
            previous = int(item["previous_target"])
            target = int(item["target"])
        except (KeyError, TypeError, ValueError):
            remaining.pop(0)
            changed = True
            continue
        execution_bar = next_minute_open(bars, received_at)
        if execution_bar is None:
            break
        append_shadow_transition(
            state,
            previous,
            target,
            execution_bar.open,
            execution_bar.bar_time,
        )
        state["last_shadow_target"] = target
        message = (
            "📊【RSI60影子成交補記】\n"
            f"收到時間：{received_at:%Y-%m-%d %H:%M:%S}\n"
            f"策略：{item.get('strategy_code', '')}\n"
            f"影子成交：{execution_bar.bar_time:%Y-%m-%d %H:%M:%S} @ "
            f"{execution_bar.open:g}（收到訊號後下一分鐘Open）\n"
            f"影子總倉：{position_text(previous)} → {position_text(target)}\n"
            "備註：此價格只用於績效對帳，實單早已在RSI判斷後立即處理。"
        )
        print(message)
        send_discord(message)
        remaining.pop(0)
        changed = True
    if changed:
        state["pending_shadow_fills"] = remaining
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
        raise RuntimeError("六策略RSI60已有另一個實例執行中") from exc

    try:
        rows, events = load_signal_rows(SOURCE_PATH)
        state = load_json(STATE_PATH, {})
        previous_count = state.get("source_row_count")
        if previous_count is None or int(previous_count) > len(rows):
            state = rebuild_state(rows, events, threshold)
        state.setdefault("pending_shadow_fills", [])
        state.setdefault("live_started", False)
        state.setdefault("orders_armed_after_row", int(state.get("source_row_count") or 0))
        save_json_atomic(STATE_PATH, state)

        orders_enabled = env_flag(ENABLE_ORDERS_ENV)
        mode_text = "API_KEY2永豐實單" if orders_enabled else "影子模式"
        send_discord(
            "✅【開始監控｜六策略 RSI60 Filter】\n"
            f"時間：{now_text()}\nRSI門檻：{threshold:g}\n"
            f"模式：{mode_text}\n"
            "實單口數：RSI過濾後12策略完整淨部位\n"
            "啟動行為：不對齊舊影子部位，從下一筆新EF訊號才啟動實單\n"
            "實單時機：收到訊號、RSI判斷後立即送單，不等分鐘準點\n"
            "訊號基準：本機 received_at\n"
            "影子對帳：收到訊號後下一分鐘1分K Open\n"
            "範例：08:47:22收到 → 實單立即處理，影子價用08:48 Open\n"
            "規則：多單RSI60≥門檻、空單RSI60≤門檻。"
        )
        print(f"=== 六策略 RSI60 {mode_text} 監控 ===")
        print(
            f"source={SOURCE_PATH} prices={PRICE_PATH} threshold={threshold:g} "
            f"orders_enabled={orders_enabled}"
        )

        while True:
            process_pending_shadow_fills(state)
            rows, events = load_signal_rows(SOURCE_PATH)
            previous_count = int(state.get("source_row_count") or 0)
            if len(rows) < previous_count:
                state = rebuild_state(rows, events, threshold)
            elif len(rows) > previous_count:
                process_new_rows(state, rows, previous_count, threshold)
                process_pending_shadow_fills(state)
            time.sleep(poll_seconds)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
