from __future__ import annotations

import csv
import json
import os
import sys
import time as time_module
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from filelock import FileLock, Timeout

from auto_trade import execute_target_position
from strategy import (
    DAY_OPEN,
    DAY_SESSION_END,
    Action,
    BreakRetestEngine,
    Config,
    Decision,
    daily_regimes,
    load_hourly_daily_bars,
    load_price_bars,
    merge_daily_history,
    pending_to_dict,
    state_from_dict,
    state_to_dict,
    text_time,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"
PRICE_PATH = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
DAILY_WARMUP_PATH = (
    BACKEND_DIR
    / "tv_doc"
    / "archive_research_2026-05-15"
    / "tradingview_mxf_60min.csv"
)
RECORDS_DIR = BASE_DIR / "records"
POSITION_PATH = RECORDS_DIR / "opening_retest_position.json"
DECISION_PATH = RECORDS_DIR / "opening_retest_decisions.csv"
TRADE_PATH = RECORDS_DIR / "opening_retest_shadow_trade.csv"
ORDER_ATTEMPT_PATH = RECORDS_DIR / "opening_retest_order_attempts.csv"
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "opening_retest_state.json"
LOCK_PATH = RUNTIME_DIR / "opening_retest.lock"
ENABLE_ORDERS_ENV = "OPENING_RETEST_ENABLE_ORDERS"
POSITION_UNIT_ENV = "OPENING_RETEST_POSITION_UNIT"
MAX_POSITION_UNIT = 20

try:
    TZ = ZoneInfo("Asia/Taipei")
except ZoneInfoNotFoundError:
    TZ = timezone(timedelta(hours=8), name="Asia/Taipei")

DECISION_FIELDS = [
    "timestamp",
    "kind",
    "side",
    "accepted",
    "reason",
    "opening_high",
    "opening_low",
    "daily_close",
    "daily_sma",
    "retest_limit",
]
TRADE_FIELDS = [
    "timestamp",
    "action",
    "side",
    "price",
    "pnl_points",
    "pnl_twd",
    "quantity",
    "reason",
    "target_price",
    "stop_price",
    "signal_time",
]
ORDER_ATTEMPT_FIELDS = [
    "timestamp",
    "attempt_id",
    "event",
    "trigger",
    "target_position",
    "previous_position",
    "actual_position",
    "side",
    "quantity",
    "detail",
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
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def safe_print(content: object) -> None:
    value = str(content)
    try:
        print(value)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(value.encode(encoding, errors="replace").decode(encoding))


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
        os.getenv("DISCORD_OPENING_RETEST", "").strip()
        or os.getenv("DISCORD_OPENING_RETEST_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_MXF_ALERT_WEBHOOK_URL", "").strip()
    )


def send_discord(content: str) -> bool:
    webhook = webhook_url()
    if not webhook:
        safe_print("⚠️ 未設定 DISCORD_OPENING_RETEST")
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
        safe_print(f"❌ Discord通知失敗：{str(exc).replace(webhook, '<Discord webhook>')}")
        return False


def position_text(position: int) -> str:
    return f"多{position}口" if position > 0 else f"空{abs(position)}口" if position < 0 else "空手"


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


def _env_clock(name: str, default: str) -> time:
    try:
        return datetime.strptime(os.getenv(name, default), "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"{name}格式必須是HH:MM") from exc


def config_from_env() -> Config:
    opening_minutes = int(os.getenv("OPENING_RETEST_OPENING_MINUTES", "30"))
    return Config(
        opening_minutes=opening_minutes,
        daily_sma_length=int(os.getenv("OPENING_RETEST_DAILY_SMA_LENGTH", "20")),
        retest_points=float(os.getenv("OPENING_RETEST_POINTS", "50")),
        retest_expiry_minutes=int(os.getenv("OPENING_RETEST_EXPIRY_MINUTES", "30")),
        limit_penetration_points=float(
            os.getenv("OPENING_RETEST_LIMIT_PENETRATION_POINTS", "1")
        ),
        stop_points=float(os.getenv("OPENING_RETEST_STOP_POINTS", "100")),
        target_points=float(os.getenv("OPENING_RETEST_TARGET_POINTS", "100")),
        force_flat_time=_env_clock("OPENING_RETEST_FORCE_FLAT_TIME", "11:00"),
        minimum_opening_bars=int(
            os.getenv("OPENING_RETEST_MINIMUM_OPENING_BARS", str(max(1, opening_minutes - 2)))
        ),
    )


def append_decision(decision: Decision) -> None:
    append_csv(
        DECISION_PATH,
        DECISION_FIELDS,
        {
            "timestamp": text_time(decision.timestamp),
            "kind": decision.kind,
            "side": "bull" if decision.side > 0 else "bear",
            "accepted": int(decision.accepted),
            "reason": decision.reason,
            "opening_high": decision.opening_high,
            "opening_low": decision.opening_low,
            "daily_close": decision.daily_close,
            "daily_sma": decision.daily_sma,
            "retest_limit": decision.retest_limit,
        },
    )


def append_action(action: Action) -> None:
    append_csv(
        TRADE_PATH,
        TRADE_FIELDS,
        {
            "timestamp": text_time(action.timestamp),
            "action": action.action,
            "side": "bull" if action.side > 0 else "bear",
            "price": action.price,
            "pnl_points": "" if action.pnl_points is None else round(action.pnl_points, 2),
            "pnl_twd": (
                ""
                if action.pnl_points is None
                else round(action.pnl_points * 10 * position_unit(), 2)
            ),
            "quantity": position_unit(),
            "reason": action.reason,
            "target_price": action.target_price,
            "stop_price": action.stop_price,
            "signal_time": text_time(action.signal_time),
        },
    )


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
    append_csv(
        ORDER_ATTEMPT_PATH,
        ORDER_ATTEMPT_FIELDS,
        {
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
        },
    )


def execute_live_target(runtime: dict, target: int, *, trigger: str, force: bool = False) -> str:
    if not env_flag(ENABLE_ORDERS_ENV):
        return "影子模式，未送實單"
    broker_target = scaled_target(target)
    attempt_id = uuid.uuid4().hex
    attempted = runtime.get("last_order_attempt_target")
    if not force and attempted == broker_target:
        append_order_event(
            attempt_id=attempt_id,
            event="skipped_duplicate",
            trigger=trigger,
            target=broker_target,
            detail="相同目標已嘗試過，防重送",
        )
        return f"相同目標{position_text(broker_target)}已嘗試過，不重送"
    append_order_event(
        attempt_id=attempt_id,
        event="attempt_started",
        trigger=trigger,
        target=broker_target,
        detail="準備登入永豐、查詢TMF部位並對帳",
    )
    runtime["last_order_attempt_target"] = broker_target
    runtime["last_order_attempt_at"] = text_time(now_local())
    runtime["last_order_trigger"] = trigger
    save_json_atomic(STATE_PATH, runtime)
    try:
        result = execute_target_position(broker_target)
    except Exception as exc:
        append_order_event(
            attempt_id=attempt_id,
            event="failed",
            trigger=trigger,
            target=broker_target,
            detail=f"{type(exc).__name__}: {exc}",
        )
        runtime["last_order_error"] = str(exc)
        save_json_atomic(STATE_PATH, runtime)
        return f"❌ 下單失敗：{exc}（相同目標不自動重送）"
    runtime["last_executed_target"] = broker_target
    runtime["last_executed_at"] = text_time(now_local())
    runtime.pop("last_order_error", None)
    save_json_atomic(STATE_PATH, runtime)
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
        return (
            f"✅ 已送{'買進' if result.side == 'buy' else '賣出'} TMF {result.quantity}口；"
            f"實際部位{position_text(result.actual_position)}"
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


def write_position(engine: BreakRetestEngine, runtime: dict, reason: str) -> None:
    state = engine.state
    save_json_atomic(
        POSITION_PATH,
        {
            "strategy": "Daily SMA + Opening Range Breakout Retest + 11:00 Flat",
            "mode": "live_target_adapter" if env_flag(ENABLE_ORDERS_ENV) else "shadow_only",
            "position_unit": position_unit(),
            "broker_target_position": scaled_target(state.position),
            "shadow_position": state.position,
            "entry_price": state.entry_price,
            "entry_time": text_time(state.entry_time),
            "target_price": state.target_price,
            "stop_price": state.stop_price,
            "pending_retest": pending_to_dict(state.pending),
            "session_date": "" if state.session_date is None else state.session_date.isoformat(),
            "opening_high": state.opening_high,
            "opening_low": state.opening_low,
            "daily_bias": state.daily_bias,
            "daily_close": state.daily_close,
            "daily_sma": state.daily_sma,
            "signal_used_today": state.signal_used_today,
            "last_bar_time": text_time(state.last_bar_time),
            "last_reason": reason,
            "updated_at": text_time(now_local()),
        },
    )
    runtime["engine_state"] = state_to_dict(state)
    save_json_atomic(STATE_PATH, runtime)


def action_message(action: Action, live_result: str) -> str:
    verb = "進場" if action.action == "enter" else "出場"
    final_target = scaled_target(action.side if action.action == "enter" else 0)
    lines = [
        f"🚦【開盤突破回踩｜{verb}】",
        f"時間：{text_time(action.timestamp)}",
        f"收到訊號後【最終口數】：{position_text(final_target)}",
        f"方向：{'多' if action.side > 0 else '空'}{position_unit()}口 @ {action.price:g}",
        f"原因：{action.reason}",
    ]
    if action.target_price is not None:
        lines.append(f"停利：{action.target_price:g}；停損：{action.stop_price:g}")
    if action.pnl_points is not None:
        lines.append(
            f"損益：{action.pnl_points:g}點 / {action.pnl_points * 10 * position_unit():g}元"
        )
    lines.append(f"執行：{live_result}")
    return "\n".join(lines)


def decision_message(decision: Decision) -> str:
    direction = "多" if decision.side > 0 else "空"
    return "\n".join(
        [
            f"🧭【開盤突破回踩｜{decision.kind}】",
            f"時間：{text_time(decision.timestamp)}",
            f"方向：{direction}；狀態：{'成立' if decision.accepted else '取消'}",
            f"開盤區間：{decision.opening_low:g}～{decision.opening_high:g}",
            f"回踩限價：{decision.retest_limit:g}",
            f"原因：{decision.reason}",
        ]
    )


def initialize_runtime(bars: list, config: Config) -> tuple[BreakRetestEngine, dict]:
    engine = BreakRetestEngine(config)
    if bars:
        latest = bars[-1]
        engine.state.last_bar_time = latest.bar_time
        if DAY_OPEN <= latest.bar_time.time() < DAY_SESSION_END:
            engine.state.session_date = latest.bar_time.date()
            engine.state.signal_used_today = True
    runtime = {"engine_state": state_to_dict(engine.state), "started_at": text_time(now_local())}
    save_json_atomic(STATE_PATH, runtime)
    write_position(engine, runtime, "安全啟動：只從下一根新K開始，不追補歷史委託")
    return engine, runtime


def load_runtime(bars: list, config: Config) -> tuple[BreakRetestEngine, dict]:
    runtime = load_json(STATE_PATH, {})
    if not runtime.get("engine_state"):
        return initialize_runtime(bars, config)
    return BreakRetestEngine(config, state_from_dict(runtime["engine_state"])), runtime


def _regimes(bars: list, config: Config) -> dict:
    history = merge_daily_history(load_hourly_daily_bars(DAILY_WARMUP_PATH), bars)
    return daily_regimes(history, config.daily_sma_length)


def process_new_bars(
    engine: BreakRetestEngine,
    runtime: dict,
    bars: list,
    config: Config,
    *,
    execute_orders: bool = True,
    notify: bool = True,
) -> None:
    last_time = engine.state.last_bar_time
    new_bars = [bar for bar in bars if last_time is None or bar.bar_time > last_time]
    regimes = _regimes(bars, config)
    for bar in new_bars:
        actions, decisions = engine.process_bar(bar, regimes.get(bar.bar_time.date()))
        for decision in decisions:
            append_decision(decision)
            message = decision_message(decision)
            safe_print(message)
            if notify:
                send_discord(message)
        for action_index, action in enumerate(actions):
            append_action(action)
            has_later_action = action_index < len(actions) - 1
            if not execute_orders:
                live_result = "啟動補算，只更新影子狀態"
            elif has_later_action:
                live_result = "同一根K已有後續狀態，不送已過時的中間目標"
            else:
                live_result = execute_live_target(
                    runtime,
                    engine.state.position,
                    trigger=action.reason,
                )
            message = action_message(action, live_result)
            safe_print(message)
            if notify:
                send_discord(message)
        reason = actions[-1].reason if actions else decisions[-1].reason if decisions else "new_bar"
        write_position(engine, runtime, reason)


def main() -> None:
    load_env_file(ENV_PATH)
    config = config_from_env()
    config.validate()
    unit = position_unit()
    poll_seconds = max(0.5, float(os.getenv("OPENING_RETEST_POLL_SECONDS", "2")))
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("開盤突破回踩策略已有另一個實例執行中") from exc
    try:
        bars = load_price_bars(PRICE_PATH)
        engine, runtime = load_runtime(bars, config)
        process_new_bars(engine, runtime, bars, config, execute_orders=False, notify=False)
        startup = (
            "✅【開始監控｜日線方向＋開盤突破回踩】\n"
            f"時間：{text_time(now_local())}\n"
            f"收到訊號後【最終口數】：{position_text(scaled_target(engine.state.position))}\n"
            f"規則：前{config.opening_minutes}分開盤區間、回踩{config.retest_points:g}點、"
            f"{config.retest_expiry_minutes}分鐘失效。\n"
            f"風控：停損{config.stop_points:g}點、停利{config.target_points:g}點、"
            f"{config.force_flat_time.strftime('%H:%M')}清倉、每日最多一筆；U={unit}。\n"
            f"模式：{'實單目標對帳（尚非原生限價/OCO）' if env_flag(ENABLE_ORDERS_ENV) else '影子模式'}。"
        )
        if env_flag(ENABLE_ORDERS_ENV):
            startup += "\n⚠️ 啟動對帳：" + execute_live_target(
                runtime, engine.state.position, trigger="startup_reconcile", force=True
            )
        safe_print(startup)
        send_discord(startup)
        while True:
            bars = load_price_bars(PRICE_PATH)
            process_new_bars(engine, runtime, bars, config)
            time_module.sleep(poll_seconds)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
