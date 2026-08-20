from __future__ import annotations

import asyncio
import csv
import fcntl
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from telethon import TelegramClient, events


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
BACKEND_ENV_PATH = BACKEND_DIR / ".env"
RECORDS_DIR = BASE_DIR / "records"
H_RECORD_PATH = RECORDS_DIR / "h3_position_events.csv"
EF_RECORD_PATH = RECORDS_DIR / "ef_position_events.csv"
COMBINED_POSITION_PATH = RECORDS_DIR / "combined_position.json"
H_TRADE_RECORD_PATH = RECORDS_DIR / "h3_trade.csv"
MIXED_TRADE_RECORD_PATH = RECORDS_DIR / "h3_ef_trade.csv"
WEBHOOK_DATA_1MIN_PATH = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "h3_ef_012_state.json"
LOCK_PATH = RUNTIME_DIR / "h3_ef_012.lock"
TZ = ZoneInfo("Asia/Taipei")
RECONNECT_DELAY_SECONDS = 5
DISCORD_WEBHOOK_ENV = "DISCORD_MXF_ALERT_WEBHOOK_URL"

from strategy import (
    ALL_STRATEGIES,
    Decision,
    build_h_trade_rows,
    evaluate_strategy,
    load_latest_ef_positions,
    load_latest_h_position,
    load_latest_recorded_close,
    parse_h_signal,
    parse_six_strategy_signal,
    position_event_action,
    position_text,
    simulated_order_action,
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


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少必要環境變數: {name}")
    return value


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
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def default_state() -> dict:
    return {
        "strategy": "H3+EF 0/1/2",
        "processed_event_keys": [],
        "last_simulated_target": None,
    }


def evaluate_records() -> Decision:
    """Always rebuild the decision inputs from the two append-only CSV files."""
    return evaluate_strategy(
        load_latest_h_position(H_RECORD_PATH),
        load_latest_ef_positions(EF_RECORD_PATH),
    )


def decision_text(decision: Decision) -> str:
    if not decision.ready:
        missing_text = (
            f"，缺少 {', '.join(decision.missing_strategies)}"
            if decision.missing_strategies
            else ""
        )
        return f"策略尚未就緒：{decision.reason}{missing_text}"
    return (
        f"H={position_text(decision.h_position)}，E淨部位={decision.e_net}，"
        f"F淨部位={decision.f_net}，EF共識={position_text(decision.consensus)}，"
        f"永豐目標={position_text(decision.target_position)}；{decision.reason}"
    )


def _target_position(value: object, field_name: str, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    try:
        position = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必須是-2到2的整數，目前為{value!r}") from exc
    if position != value or position not in {-2, -1, 0, 1, 2}:
        raise ValueError(f"{field_name}必須是-2到2的整數，目前為{value!r}")
    return position


def load_combined_position() -> dict:
    if not COMBINED_POSITION_PATH.exists():
        raise FileNotFoundError(f"找不到總和倉位檔: {COMBINED_POSITION_PATH}")
    try:
        value = json.loads(COMBINED_POSITION_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"總和倉位檔不是有效JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("總和倉位檔最外層必須是JSON物件")
    return value


def write_combined_position(decision: Decision, trigger_reason: str) -> dict:
    """Write the calculated total while preserving an explicit manual override."""
    if not decision.ready or decision.target_position is None:
        raise ValueError("策略尚未就緒，不能寫入最終倉位")

    existing = load_combined_position() if COMBINED_POSITION_PATH.exists() else {}
    manual_target = _target_position(
        existing.get("manual_target_position"),
        "manual_target_position",
        allow_none=True,
    )
    calculated_target = _target_position(
        decision.target_position,
        "calculated_target_position",
    )
    final_target = manual_target if manual_target is not None else calculated_target
    snapshot = {
        "updated_at": now_text(),
        "trigger": trigger_reason,
        "h_position": decision.h_position,
        "e_net": decision.e_net,
        "f_net": decision.f_net,
        "e_direction": decision.e_direction,
        "f_direction": decision.f_direction,
        "consensus": decision.consensus,
        "calculated_target_position": calculated_target,
        "manual_target_position": manual_target,
        "final_target_position": final_target,
        "manual_override_active": manual_target is not None,
        "reason": decision.reason,
        "manual_edit_help": (
            "要人工覆寫時只修改manual_target_position為-2到2；"
            "設回null即可恢復自動計算。請勿直接修改final_target_position。"
        ),
    }
    save_json_atomic(COMBINED_POSITION_PATH, snapshot)
    return snapshot


def reconcile_manual_override() -> dict:
    """Normalize a manual edit and make final_target_position authoritative."""
    snapshot = load_combined_position()
    calculated_target = _target_position(
        snapshot.get("calculated_target_position"),
        "calculated_target_position",
    )
    manual_target = _target_position(
        snapshot.get("manual_target_position"),
        "manual_target_position",
        allow_none=True,
    )
    final_target = manual_target if manual_target is not None else calculated_target
    if snapshot.get("final_target_position") != final_target:
        snapshot["final_target_position"] = final_target
        snapshot["manual_override_active"] = manual_target is not None
        snapshot["updated_at"] = now_text()
        snapshot["trigger"] = "人工修改總和倉位檔"
        save_json_atomic(COMBINED_POSITION_PATH, snapshot)
    return snapshot


def combined_position_text(snapshot: dict) -> str:
    override_text = (
        f"，人工覆寫={position_text(snapshot.get('manual_target_position'))}"
        if snapshot.get("manual_target_position") is not None
        else ""
    )
    return (
        f"H={position_text(snapshot.get('h_position'))}，"
        f"E淨部位={snapshot.get('e_net')}，F淨部位={snapshot.get('f_net')}，"
        f"自動目標={position_text(snapshot.get('calculated_target_position'))}"
        f"{override_text}，最終目標={position_text(snapshot.get('final_target_position'))}；"
        f"{snapshot.get('reason', '')}"
    )


def send_discord_message(content: str) -> bool:
    """Send through the same webhook used by the existing alive notification."""
    webhook_url = os.getenv(DISCORD_WEBHOOK_ENV, "").strip()
    if not webhook_url:
        print(f"❌ Discord webhook 未設定: {DISCORD_WEBHOOK_ENV}")
        return False

    try:
        response = requests.post(
            webhook_url,
            json={"username": "NotifierBot", "content": content},
            timeout=15,
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as exc:
        print(f"❌ 發送 Discord 訊息失敗: {exc}")
        return False


def send_simulated_order(
    previous_target: int,
    target_position: int,
    *,
    trigger_reason: str,
    decision_summary: str,
) -> bool:
    """Simulate the target-position adjustment by sending Discord only."""
    if previous_target not in {-2, -1, 0, 1, 2}:
        raise ValueError(f"前次模擬部位超出範圍: {previous_target}")
    if target_position not in {-2, -1, 0, 1, 2}:
        raise ValueError(f"目標模擬部位超出範圍: {target_position}")

    action, side, quantity = simulated_order_action(previous_target, target_position)
    if quantity == 0:
        return True

    message = (
        "🧪【模擬下單｜H3+EF 0/1/2】\n"
        f"時間：{now_text()}\n"
        f"動作：{action}，{side}微型台指近一（TMF）{quantity}口\n"
        f"模擬部位：{position_text(previous_target)} → {position_text(target_position)}\n"
        f"觸發：{trigger_reason}\n"
        f"判斷：{decision_summary}\n"
        "備註：只有Discord模擬通知，未查詢永豐部位、未送出任何實單。"
    )
    print(message)
    return send_discord_message(message)


H_RECORD_FIELDS = [
    "received_at",
    "event_key",
    "source",
    "action",
    "previous_position",
    "new_position",
    "raw_message",
]
EF_RECORD_FIELDS = [
    "received_at",
    "event_key",
    "account",
    "strategy_code",
    "source",
    "action",
    "previous_position",
    "new_position",
    "state_reconciled",
    "raw_message",
]
TRADE_RECORD_FIELDS = ["timestamp", "action", "side", "price", "pnl", "quantity"]


def append_record(path: Path, fields: list[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def latest_market_price(at: datetime | None = None) -> float | None:
    """Return the newest one-minute close that was already recorded at `at`."""
    return load_latest_recorded_close(
        WEBHOOK_DATA_1MIN_PATH,
        at or datetime.now(TZ),
    )


def latest_open_trade(path: Path) -> dict[str, str] | None:
    """Rebuild the currently open analytical segment from a trade CSV."""
    if not path.exists():
        return None
    open_trade: dict[str, str] | None = None
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            action = str(row.get("action") or "").strip()
            if action == "enter":
                open_trade = dict(row)
            elif action == "exiting":
                open_trade = None
    return open_trade


def trade_log_position(path: Path) -> int | None:
    open_trade = latest_open_trade(path)
    if open_trade is None:
        return 0 if path.exists() else None
    side = str(open_trade.get("side") or "").strip()
    try:
        quantity = int(float(str(open_trade.get("quantity") or "")))
    except ValueError:
        return None
    if side not in {"bull", "bear"} or quantity not in {1, 2}:
        return None
    return quantity if side == "bull" else -quantity


def append_trade_transition(
    path: Path,
    previous_position: int,
    target_position: int,
    *,
    observed_at: datetime | None = None,
) -> None:
    """Append h_trade-compatible exit/entry rows for a target change."""
    event_time = observed_at or datetime.now(TZ)
    open_trade = latest_open_trade(path)
    previous_entry_price: float | None = None
    if open_trade is not None and trade_log_position(path) == previous_position:
        try:
            previous_entry_price = float(str(open_trade.get("price") or "").strip())
        except ValueError:
            previous_entry_price = None

    rows = build_h_trade_rows(
        timestamp=event_time.strftime("%Y-%m-%d %H:%M:%S"),
        previous_position=previous_position,
        target_position=target_position,
        price=latest_market_price(event_time),
        previous_entry_price=previous_entry_price,
    )
    for row in rows:
        append_record(path, TRADE_RECORD_FIELDS, row)


def record_contains_event(path: Path, event_key: str) -> bool:
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8") as handle:
        return any(row.get("event_key") == event_key for row in csv.DictReader(handle))


def event_already_processed(state: dict, event_key: str) -> bool:
    values = state.get("processed_event_keys", [])
    if isinstance(values, list) and event_key in values:
        return True
    return record_contains_event(H_RECORD_PATH, event_key) or record_contains_event(
        EF_RECORD_PATH,
        event_key,
    )


def mark_event_processed(state: dict, event_key: str) -> None:
    values = state.setdefault("processed_event_keys", [])
    if not isinstance(values, list):
        values = []
    values.append(event_key)
    state["processed_event_keys"] = values[-200:]


def apply_h_event(
    state: dict,
    position: int,
    event_key: str,
    raw_message: str,
) -> tuple[int | None, str, bool]:
    previous = load_latest_h_position(H_RECORD_PATH)
    action = position_event_action(previous, position)
    mark_event_processed(state, event_key)
    if previous == position:
        return previous, action, False

    append_record(
        H_RECORD_PATH,
        H_RECORD_FIELDS,
        {
            "received_at": now_text(),
            "event_key": event_key,
            "source": "浩克3V3",
            "action": action,
            "previous_position": previous,
            "new_position": position,
            "raw_message": raw_message,
        },
    )
    try:
        append_trade_transition(
            H_TRADE_RECORD_PATH,
            previous if previous in {-1, 1} else 0,
            position,
        )
    except (OSError, ValueError) as exc:
        print(f"H3交易紀錄寫入失敗：{exc}")
        send_discord_message(f"H3交易紀錄寫入失敗，請人工檢查：{exc}")
    return previous, action, True


def apply_six_event(state: dict, signal, event_key: str, raw_message: str) -> bool:
    stored_position = load_latest_ef_positions(EF_RECORD_PATH).get(signal.strategy_code)
    reconciled = stored_position != signal.previous_position
    mark_event_processed(state, event_key)
    append_record(
        EF_RECORD_PATH,
        EF_RECORD_FIELDS,
        {
            "received_at": now_text(),
            "event_key": event_key,
            "account": signal.account,
            "strategy_code": signal.strategy_code,
            "source": "群益Telegram",
            "action": position_event_action(stored_position, signal.new_position),
            "previous_position": signal.previous_position,
            "new_position": signal.new_position,
            "state_reconciled": reconciled,
            "raw_message": raw_message,
        },
    )
    return reconciled


def save_decision(state: dict, decision: Decision) -> None:
    state["last_decision"] = {
        "calculated_at": now_text(),
        "ready": decision.ready,
        "h_position": decision.h_position,
        "e_net": decision.e_net,
        "f_net": decision.f_net,
        "e_direction": decision.e_direction,
        "f_direction": decision.f_direction,
        "consensus": decision.consensus,
        "relation": decision.relation,
        "target_position": decision.target_position,
        "reason": decision.reason,
        "missing_strategies": list(decision.missing_strategies),
    }
    state["updated_at"] = now_text()


def apply_final_position_file(trigger_reason: str) -> bool:
    """Read only the total file, then emit the required simulated adjustment."""
    state = load_json(STATE_PATH, default_state())
    snapshot = reconcile_manual_override()
    final_target = _target_position(
        snapshot.get("final_target_position"),
        "final_target_position",
    )
    previous_target = state.get("last_simulated_target")
    if previous_target is None:
        # Compatibility with state written by the first observation-only version.
        previous_target = state.get("last_observed_target")
    if previous_target is None:
        previous_target = trade_log_position(MIXED_TRADE_RECORD_PATH)
    try:
        normalized_previous = int(previous_target) if previous_target is not None else 0
    except (TypeError, ValueError):
        normalized_previous = 0

    target_changed = previous_target is None or normalized_previous != final_target
    if not target_changed:
        print(f"最終倉位檔未改變，維持 {position_text(final_target)}，不送Discord模擬單")
        save_json_atomic(STATE_PATH, state)
        return True

    success = send_simulated_order(
        normalized_previous,
        final_target,
        trigger_reason=trigger_reason,
        decision_summary=combined_position_text(snapshot),
    )
    if success:
        try:
            append_trade_transition(
                MIXED_TRADE_RECORD_PATH,
                normalized_previous,
                final_target,
            )
            state.pop("last_trade_log_error_at", None)
            state.pop("last_trade_log_error", None)
        except (OSError, ValueError) as exc:
            state["last_trade_log_error_at"] = now_text()
            state["last_trade_log_error"] = str(exc)
            print(f"混合策略交易紀錄寫入失敗：{exc}")
            send_discord_message(f"混合策略交易紀錄寫入失敗，請人工檢查：{exc}")
        state["last_simulated_target"] = final_target
        state["last_simulated_target_updated_at"] = now_text()
    else:
        state["last_simulation_error_at"] = now_text()
        state["last_simulation_error_target"] = final_target
    save_json_atomic(STATE_PATH, state)
    return success


def execute_current_decision(trigger_reason: str) -> None:
    state = load_json(STATE_PATH, default_state())
    decision = evaluate_records()
    save_decision(state, decision)
    message = decision_text(decision)
    print(message)

    if not decision.ready or decision.target_position is None:
        save_json_atomic(STATE_PATH, state)
        send_discord_message(f"[{datetime.now(TZ):%H:%M:%S}]：H3+EF 0/1/2。{message}")
        return

    try:
        write_combined_position(decision, trigger_reason)
    except (OSError, ValueError) as exc:
        state["last_combined_position_error_at"] = now_text()
        state["last_combined_position_error"] = str(exc)
        save_json_atomic(STATE_PATH, state)
        send_discord_message(
            f"[{datetime.now(TZ):%H:%M:%S}]：H3+EF總和倉位檔錯誤，"
            f"本次不模擬下單：{exc}"
        )
        return
    save_json_atomic(STATE_PATH, state)
    apply_final_position_file(trigger_reason)


load_env_file(BACKEND_ENV_PATH)
API_ID = int(require_env("API_ID"))
API_HASH = require_env("API_HASH")
CAPITAL_ACCOUNT = os.getenv("H3_EF_012_CAPITAL_ACCOUNT", "6008770").strip()
BATCH_DELAY_SECONDS = float(os.getenv("H3_EF_012_BATCH_DELAY_SECONDS", "1.0"))

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
state = load_json(STATE_PATH, default_state())
save_json_atomic(STATE_PATH, state)

client = TelegramClient(str(RUNTIME_DIR / "session_h3_ef_012"), API_ID, API_HASH)
pending_recompute_task: asyncio.Task | None = None
pending_reasons: list[str] = []
state_lock = asyncio.Lock()
recompute_generation = 0


async def delayed_recompute() -> None:
    global pending_reasons
    while True:
        generation = recompute_generation
        await asyncio.sleep(BATCH_DELAY_SECONDS)
        if generation != recompute_generation:
            continue
        reasons = pending_reasons
        pending_reasons = []
        reason_text = "、".join(dict.fromkeys(reasons))
        async with state_lock:
            await asyncio.to_thread(execute_current_decision, reason_text)
        return


def schedule_recompute(reason: str) -> None:
    global pending_recompute_task, recompute_generation
    pending_reasons.append(reason)
    recompute_generation += 1
    if not pending_recompute_task or pending_recompute_task.done():
        pending_recompute_task = asyncio.create_task(delayed_recompute())


def combined_file_signature() -> tuple[int, int] | None:
    try:
        stat = COMBINED_POSITION_PATH.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


async def watch_combined_position_file() -> None:
    """Apply valid manual target edits without waiting for another Telegram signal."""
    previous_signature = combined_file_signature()
    while True:
        await asyncio.sleep(2)
        current_signature = combined_file_signature()
        if current_signature == previous_signature:
            continue
        try:
            async with state_lock:
                success = await asyncio.to_thread(
                    apply_final_position_file,
                    "總和倉位檔人工更新",
                )
        except (OSError, ValueError) as exc:
            print(f"總和倉位檔更新無效，尚未模擬下單：{exc}")
            continue
        if success:
            previous_signature = combined_file_signature()


@client.on(events.NewMessage)
async def telegram_message_handler(event):
    text = event.text or ""
    h_signal = parse_h_signal(text)
    six_signal = parse_six_strategy_signal(text, required_account=CAPITAL_ACCOUNT)
    if h_signal is None and six_signal is None:
        return

    event_key = f"{event.chat_id}:{event.id}"
    async with state_lock:
        current_state = load_json(STATE_PATH, default_state())
        if event_already_processed(current_state, event_key):
            print(f"略過已處理Telegram訊號: {event_key}")
            return

        if h_signal is not None:
            previous, action, changed = apply_h_event(
                current_state,
                h_signal.position,
                event_key,
                text,
            )
            trigger = (
                f"H {position_text(previous)}->{position_text(h_signal.position)}（{action}）"
            )
            if h_signal.announced_quantity != 1:
                trigger += f"（原訊息口數{h_signal.announced_quantity}，本策略只取方向）"
        else:
            changed = True
            reconciled = apply_six_event(current_state, six_signal, event_key, text)
            trigger = (
                f"{six_signal.strategy_code} "
                f"{six_signal.previous_position}->{six_signal.new_position}"
            )
            if reconciled:
                trigger += "（已依訊號前倉位校正狀態）"

        current_state["updated_at"] = now_text()
        save_json_atomic(STATE_PATH, current_state)
        print(f"收到訊號 {event_key}: {trigger}")
        if changed:
            schedule_recompute(trigger)
        else:
            print("H方向未變，已去重但不重新計算模擬單")


def acquire_process_lock():
    handle = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("H3+EF 0/1/2監控程式已經有另一個實例在執行") from exc
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def main() -> None:
    process_lock = acquire_process_lock()
    print("=== H3 + EF 0/1/2 Discord 模擬下單監控 ===")
    if send_discord_message("開始自動交易"):
        print("已送出Discord啟動通知：開始自動交易")
    else:
        print("Discord啟動通知送出失敗，監控服務仍會繼續啟動")
    print(f"群益帳號過濾={CAPITAL_ACCOUNT}，執行模式=Discord模擬下單（不連永豐）")
    print(decision_text(evaluate_records()))

    if env_flag("H3_EF_012_SIMULATE_ON_START", default=False):
        execute_current_decision("程式啟動模擬對帳")
    else:
        print("啟動時不送模擬單；收到下一筆有效H或EF訊號後才重新計算")

    watcher_task = client.loop.create_task(watch_combined_position_file())
    try:
        while True:
            try:
                client.start()
                print("Telethon開始監控浩克3與群益E/F訊號...")
                client.run_until_disconnected()
            except (ConnectionError, OSError, TimeoutError) as exc:
                print(f"Telegram連線中斷：{exc}")
                print(f"{RECONNECT_DELAY_SECONDS}秒後重新連線...")
                time.sleep(RECONNECT_DELAY_SECONDS)
    except KeyboardInterrupt:
        print("收到停止指令，結束監控。")
    finally:
        watcher_task.cancel()
        process_lock.close()


if __name__ == "__main__":
    main()
