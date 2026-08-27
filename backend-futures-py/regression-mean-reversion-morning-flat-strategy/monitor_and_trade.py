from __future__ import annotations

import csv
import json
import os
import sys
import time as time_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from filelock import FileLock, Timeout

from auto_trade import execute_target_position
from strategy import (
    Action,
    Config,
    Decision,
    MeanReversionEngine,
    RiskContextTracker,
    load_price_bars,
    state_from_dict,
    state_to_dict,
    text_time,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ENV_PATH = BACKEND_DIR / ".env"
PRICE_PATH = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
H_TRADE_PATH = BACKEND_DIR / "tv_doc" / "h_trade.csv"
EF_SIGNAL_PATH = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
RECORDS_DIR = BASE_DIR / "records"
POSITION_PATH = RECORDS_DIR / "regression_mean_reversion_position.json"
DECISION_PATH = RECORDS_DIR / "regression_mean_reversion_decisions.csv"
TRADE_PATH = RECORDS_DIR / "regression_mean_reversion_shadow_trade.csv"
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "regression_mean_reversion_state.json"
LOCK_PATH = RUNTIME_DIR / "regression_mean_reversion.lock"
ENABLE_ORDERS_ENV = "MEAN_REVERSION_ENABLE_ORDERS"

try:
    TZ = ZoneInfo("Asia/Taipei")
except ZoneInfoNotFoundError:
    TZ = timezone(timedelta(hours=8), name="Asia/Taipei")

DECISION_FIELDS = [
    "timestamp", "kind", "side", "accepted", "reason", "center", "upper",
    "lower", "slope", "residual_std", "h_position", "ef_target", "e_net", "f_net",
]
TRADE_FIELDS = [
    "timestamp", "action", "side", "price", "pnl_points", "pnl_twd", "quantity",
    "reason", "target_price", "stop_price", "signal_time",
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
    text = str(content)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding))


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
        os.getenv("DISCORD_MEAN_REVERSION_WEBHOOK_URL", "").strip()
        or os.getenv("DISCORD_MXF_ALERT_WEBHOOK_URL", "").strip()
    )


def send_discord(content: str) -> bool:
    webhook = webhook_url()
    if not webhook:
        safe_print("⚠️ 未設定 DISCORD_MEAN_REVERSION_WEBHOOK_URL")
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
    return "多1口" if position > 0 else "空1口" if position < 0 else "空手"


def config_from_env() -> Config:
    return Config(
        regression_length=int(os.getenv("MEAN_REVERSION_LENGTH", "60")),
        channel_width=float(os.getenv("MEAN_REVERSION_CHANNEL_WIDTH", "2.0")),
        stop_points=float(os.getenv("MEAN_REVERSION_STOP_POINTS", "100")),
        max_abs_slope=float(os.getenv("MEAN_REVERSION_MAX_ABS_SLOPE", "2.5")),
        abnormal_range_multiple=float(os.getenv("MEAN_REVERSION_ABNORMAL_RANGE_MULTIPLE", "3.0")),
        min_reward_risk=float(os.getenv("MEAN_REVERSION_MIN_REWARD_RISK", "1.2")),
        gap_points=float(os.getenv("MEAN_REVERSION_GAP_POINTS", "100")),
        gap_expansion_points=float(os.getenv("MEAN_REVERSION_GAP_EXPANSION_POINTS", "50")),
        max_entries_per_day=int(os.getenv("MEAN_REVERSION_MAX_ENTRIES_PER_DAY", "4")),
    )


def append_decision(decision: Decision) -> None:
    append_csv(DECISION_PATH, DECISION_FIELDS, {
        "timestamp": text_time(decision.timestamp),
        "kind": decision.kind,
        "side": "bull" if decision.side > 0 else "bear",
        "accepted": int(decision.accepted),
        "reason": decision.reason,
        "center": decision.center,
        "upper": decision.upper,
        "lower": decision.lower,
        "slope": decision.slope,
        "residual_std": decision.residual_std,
        "h_position": decision.h_position,
        "ef_target": decision.ef_target,
        "e_net": decision.e_net,
        "f_net": decision.f_net,
    })


def append_action(action: Action) -> None:
    append_csv(TRADE_PATH, TRADE_FIELDS, {
        "timestamp": text_time(action.timestamp),
        "action": action.action,
        "side": "bull" if action.side > 0 else "bear",
        "price": action.price,
        "pnl_points": "" if action.pnl_points is None else round(action.pnl_points, 2),
        "pnl_twd": "" if action.pnl_points is None else round(action.pnl_points * 10, 2),
        "quantity": 1,
        "reason": action.reason,
        "target_price": action.target_price,
        "stop_price": action.stop_price,
        "signal_time": text_time(action.signal_time),
    })


def execute_live_target(runtime: dict, target: int, *, trigger: str, force: bool = False) -> str:
    if not env_flag(ENABLE_ORDERS_ENV):
        return "影子模式，未送實單"
    attempted = runtime.get("last_order_attempt_target")
    if not force and attempted == target:
        return f"相同目標{position_text(target)}已嘗試過，不重送"
    runtime["last_order_attempt_target"] = target
    runtime["last_order_attempt_at"] = text_time(now_local())
    runtime["last_order_trigger"] = trigger
    save_json_atomic(STATE_PATH, runtime)
    try:
        result = execute_target_position(target)
    except Exception as exc:
        runtime["last_order_error"] = str(exc)
        save_json_atomic(STATE_PATH, runtime)
        return f"❌ 下單失敗：{exc}（相同目標不自動重送）"
    runtime["last_executed_target"] = target
    runtime["last_executed_at"] = text_time(now_local())
    runtime.pop("last_order_error", None)
    save_json_atomic(STATE_PATH, runtime)
    if result.order_sent:
        return f"✅ 已送{'買進' if result.side == 'buy' else '賣出'} TMF {result.quantity}口；實際部位{position_text(result.actual_position)}"
    return f"帳戶已是{position_text(result.actual_position)}，無需送單"


def write_position(engine: MeanReversionEngine, runtime: dict, reason: str) -> None:
    state = engine.state
    save_json_atomic(POSITION_PATH, {
        "strategy": "Regression Mean Reversion + 13:20 Flat",
        "mode": "live_api_key4" if env_flag(ENABLE_ORDERS_ENV) else "shadow_only",
        "shadow_position": state.position,
        "entry_price": state.entry_price,
        "target_price": state.target_price,
        "stop_price": state.stop_price,
        "long_locked": state.long_locked,
        "short_locked": state.short_locked,
        "entries_today": state.entries_today,
        "last_bar_time": text_time(state.last_bar_time),
        "last_reason": reason,
        "updated_at": text_time(now_local()),
    })
    runtime["engine_state"] = state_to_dict(state)
    save_json_atomic(STATE_PATH, runtime)


def action_message(action: Action, live_result: str) -> str:
    verb = "進場" if action.action == "enter" else "出場"
    lines = [
        f"📐【均值回歸｜{verb}】",
        f"時間：{text_time(action.timestamp)}",
        f"方向：{'多' if action.side > 0 else '空'}1口 @ {action.price:g}",
        f"原因：{action.reason}",
    ]
    if action.target_price is not None:
        lines.append(f"中心目標：{action.target_price:g}；停損：{action.stop_price:g}")
    if action.pnl_points is not None:
        lines.append(f"損益：{action.pnl_points:g}點 / {action.pnl_points * 10:g}元")
    lines.append(f"執行：{live_result}")
    return "\n".join(lines)


def initialize_runtime(bars: list, config: Config) -> tuple[MeanReversionEngine, dict]:
    engine = MeanReversionEngine(config)
    if bars:
        engine.warm(bars)
        engine.state.last_bar_time = bars[-1].bar_time
    runtime = {"engine_state": state_to_dict(engine.state), "started_at": text_time(now_local())}
    save_json_atomic(STATE_PATH, runtime)
    write_position(engine, runtime, "安全啟動：只從下一根新K開始，不追補歷史委託")
    return engine, runtime


def load_runtime(bars: list, config: Config) -> tuple[MeanReversionEngine, dict]:
    runtime = load_json(STATE_PATH, {})
    if not runtime.get("engine_state"):
        return initialize_runtime(bars, config)
    engine = MeanReversionEngine(config, state_from_dict(runtime["engine_state"]))
    history = [bar for bar in bars if engine.state.last_bar_time is None or bar.bar_time <= engine.state.last_bar_time]
    engine.warm(history)
    return engine, runtime


def process_new_bars(
    engine: MeanReversionEngine,
    runtime: dict,
    bars: list,
    config: Config,
    *,
    execute_orders: bool = True,
    notify: bool = True,
) -> None:
    last_time = engine.state.last_bar_time
    new_bars = [bar for bar in bars if last_time is None or bar.bar_time > last_time]
    tracker = RiskContextTracker(H_TRADE_PATH, EF_SIGNAL_PATH, ef_threshold=config.ef_threshold)
    for bar in new_bars:
        actions, decisions = engine.process_bar(bar, tracker.at(bar.record_time))
        for decision in decisions:
            append_decision(decision)
        for action in actions:
            append_action(action)
            target = action.side if action.action == "enter" else 0
            live_result = (
                execute_live_target(runtime, target, trigger=action.reason)
                if execute_orders
                else "啟動補算，只更新影子狀態"
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
    poll_seconds = max(0.5, float(os.getenv("MEAN_REVERSION_POLL_SECONDS", "2")))
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOCK_PATH))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise RuntimeError("均值回歸策略已有另一個實例執行中") from exc
    try:
        bars = load_price_bars(PRICE_PATH)
        engine, runtime = load_runtime(bars, config)
        # Rebuild missed bars before any broker reconciliation. This prevents a
        # restart from firing a rapid sequence of stale historical orders.
        process_new_bars(
            engine,
            runtime,
            bars,
            config,
            execute_orders=False,
            notify=False,
        )
        startup = (
            "✅【開始監控｜回歸通道均值回歸】\n"
            f"時間：{text_time(now_local())}\n"
            f"通道：{config.regression_length}根1分K、{config.channel_width:g}倍殘差標準差。\n"
            f"風控：固定{config.stop_points:g}點停損、13:20清倉、同向停損後當日鎖定。\n"
            f"模式：{'API_KEY4永豐實單' if env_flag(ENABLE_ORDERS_ENV) else '影子模式'}。"
        )
        if env_flag(ENABLE_ORDERS_ENV):
            startup += "\n啟動對帳：" + execute_live_target(
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
