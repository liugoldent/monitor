from __future__ import annotations

import asyncio
import csv
import fcntl
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from telethon import TelegramClient, events

from auto_trade import execute_target_position


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
MXF_VALUE_CSV_PATH = BACKEND_DIR / "tv_doc" / "mxf_value.csv"
LEGACY_SIX_STRATEGY_RECORD_PATH = (
    BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
)
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_PATH = RUNTIME_DIR / "h3_ef_012_state.json"
LOCK_PATH = RUNTIME_DIR / "h3_ef_012.lock"
TZ = ZoneInfo("Asia/Taipei")
RECONNECT_DELAY_SECONDS = 5
ORDER_ERROR_NOTIFICATION_INTERVAL_SECONDS = 60
MAX_ORDER_ERROR_NOTIFICATIONS = 5
DISCORD_WEBHOOK_ENV = "DISCORD_MXF_ALERT_WEBHOOK_URL"
SECONDARY_DISCORD_WEBHOOK_ENV = "DISCORD_SIX_STRATEGY_WEBHOOK_URL"
ENABLE_ORDERS_ENV = "H3_EF_012_ENABLE_ORDERS"

LEGACY_STRATEGY_NAMES = {
    "CFC07m": "財神列車7號",
    "CFCTX17m": "財神列車17號",
    "CFCTX18m": "財神列車18號",
    "CFCTX19m": "財神列車19號",
    "CFCTX20m": "財神列車20號",
    "CFCTX21m": "財神列車21號",
    "CFCWIN01m": "智能引擎1號",
    "CFCPW3m": "新財神列車3號",
    "CFCCPm": "財神列車6號",
    "CFCTX16m": "財神列車16號",
    "CFCTX22m": "財神列車22號",
    "CFCTX23m": "財神列車23號",
}

from strategy import (
    ALL_STRATEGIES,
    Decision,
    MAX_POSITION_UNIT,
    MAX_TARGET_QUANTITY,
    PORTFOLIO_E,
    PORTFOLIO_F,
    build_h_trade_rows,
    direction_text,
    evaluate_strategy,
    load_latest_ef_positions,
    load_latest_h_position,
    load_latest_recorded_close,
    normalize_h_position,
    normalize_h_record_message,
    parse_h_signal,
    parse_six_strategy_signal,
    position_event_action,
    position_text,
    scale_target_position,
    scaled_relation_reason,
    simulated_order_action,
    unchanged_target_notification_text,
    validate_position_unit,
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
        "strategy": "H3+EF 0/U/2U",
        "processed_event_keys": [],
        "last_simulated_target": None,
    }


def evaluate_records() -> Decision:
    """Always rebuild the decision inputs from the two append-only CSV files."""
    return evaluate_strategy(
        load_latest_h_position(H_RECORD_PATH),
        load_latest_ef_positions(EF_RECORD_PATH),
    )


def load_ef_strategy_position_details(path: Path = EF_RECORD_PATH) -> dict:
    """Rebuild human-readable E/F strategy details from the append-only CSV."""
    latest_rows: dict[str, dict[str, object]] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                strategy_code = str(row.get("strategy_code") or "").strip()
                if strategy_code not in ALL_STRATEGIES:
                    continue
                try:
                    position = int(float(str(row.get("new_position") or "")))
                except ValueError:
                    continue
                if position not in {-1, 0, 1}:
                    continue
                latest_rows[strategy_code] = {
                    "position": position,
                    "updated_at": str(row.get("received_at") or "").strip() or None,
                }

    result: dict[str, dict[str, dict[str, object]]] = {}
    for group_name, strategy_codes in (("E", PORTFOLIO_E), ("F", PORTFOLIO_F)):
        group: dict[str, dict[str, object]] = {}
        for strategy_code in strategy_codes:
            latest = latest_rows.get(strategy_code, {})
            position = latest.get("position")
            if position == 1:
                position_label = "多1口"
            elif position == -1:
                position_label = "空1口"
            elif position == 0:
                position_label = "空手"
            else:
                position_label = "尚無資料"
            strategy_name = LEGACY_STRATEGY_NAMES[strategy_code]
            group[strategy_name] = {
                "strategy_code": strategy_code,
                "position": position,
                "position_text": position_label,
                "updated_at": latest.get("updated_at"),
            }
        result[group_name] = group
    return result


def sync_combined_ef_strategy_positions() -> None:
    """Add current E/F details on startup without triggering broker reconciliation."""
    if not COMBINED_POSITION_PATH.exists():
        return
    snapshot = load_combined_position()
    details = load_ef_strategy_position_details()
    if snapshot.get("ef_strategy_positions") == details:
        return
    snapshot["ef_strategy_positions"] = details
    save_json_atomic(COMBINED_POSITION_PATH, snapshot)


def decision_text(decision: Decision, position_unit: int = 1) -> str:
    if not decision.ready:
        missing_text = (
            f"，缺少 {', '.join(decision.missing_strategies)}"
            if decision.missing_strategies
            else ""
        )
        return f"策略尚未就緒：{decision.reason}{missing_text}"
    scaled_target = scale_target_position(decision.target_position, position_unit)
    reason = scaled_relation_reason(decision.relation, position_unit)
    return (
        f"H方向={direction_text(decision.h_position)}，EF淨部位={decision.ef_net}，"
        f"EF方向={direction_text(decision.ef_direction)}，"
        f"U={position_unit}，永豐目標={position_text(scaled_target)}；{reason}"
    )


def _target_position(
    value: object,
    field_name: str,
    *,
    max_abs: int = MAX_TARGET_QUANTITY,
    allow_none: bool = False,
) -> int | None:
    if value is None and allow_none:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or abs(value) > max_abs
    ):
        raise ValueError(
            f"{field_name}必須是-{max_abs}到{max_abs}的整數，目前為{value!r}"
        )
    return value


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
    position_unit = validate_position_unit(existing.get("U", 1))
    base_target = _target_position(
        decision.target_position,
        "base_target_position",
        max_abs=2,
    )
    calculated_target = scale_target_position(base_target, position_unit)
    manual_target = _target_position(
        existing.get("manual_target_position"),
        "manual_target_position",
        max_abs=2 * position_unit,
        allow_none=True,
    )
    final_target = manual_target if manual_target is not None else calculated_target
    snapshot = {
        "updated_at": now_text(),
        "trigger": trigger_reason,
        "U": position_unit,
        "h_position": decision.h_position,
        "e_net": decision.e_net,
        "f_net": decision.f_net,
        "e_direction": decision.e_direction,
        "f_direction": decision.f_direction,
        "ef_net": decision.ef_net,
        "ef_direction": decision.ef_direction,
        "consensus": decision.consensus,
        "relation": decision.relation,
        "base_target_position": base_target,
        "calculated_target_position": calculated_target,
        "manual_target_position": manual_target,
        "final_target_position": final_target,
        "manual_override_active": manual_target is not None,
        "reason": scaled_relation_reason(decision.relation, position_unit),
        "ef_strategy_positions": load_ef_strategy_position_details(),
        "U_edit_help": (
            f"只修改U為1到{MAX_POSITION_UNIT}的整數；U=2時策略為0/2/4口，"
            "存檔後會立即重新計算。"
        ),
        "manual_edit_help": (
            f"要人工覆寫時只修改manual_target_position為-{2 * position_unit}"
            f"到{2 * position_unit}的整數；"
            "設回null即可恢復自動計算。請勿直接修改final_target_position。"
        ),
    }
    save_json_atomic(COMBINED_POSITION_PATH, snapshot)
    return snapshot


def reconcile_manual_override() -> dict:
    """Normalize manual U/target edits and make the final target authoritative."""
    snapshot = load_combined_position()
    position_unit = validate_position_unit(snapshot.get("U", 1))
    base_target = _target_position(
        snapshot.get(
            "base_target_position",
            snapshot.get("calculated_target_position"),
        ),
        "base_target_position",
        max_abs=2,
    )
    calculated_target = scale_target_position(base_target, position_unit)
    manual_target = _target_position(
        snapshot.get("manual_target_position"),
        "manual_target_position",
        max_abs=2 * position_unit,
        allow_none=True,
    )
    final_target = manual_target if manual_target is not None else calculated_target
    relation = snapshot.get("relation")
    if relation not in {"same", "opposite", "neutral"}:
        h_position = snapshot.get("h_position")
        consensus = snapshot.get("consensus")
        if h_position in {-1, 1} and consensus == h_position:
            relation = "same"
        elif h_position in {-1, 1} and consensus == -h_position:
            relation = "opposite"
        else:
            relation = "neutral"
    reason = scaled_relation_reason(relation, position_unit)
    normalized = {
        "U": position_unit,
        "relation": relation,
        "base_target_position": base_target,
        "calculated_target_position": calculated_target,
        "manual_target_position": manual_target,
        "final_target_position": final_target,
        "manual_override_active": manual_target is not None,
        "reason": reason,
        "ef_strategy_positions": load_ef_strategy_position_details(),
        "U_edit_help": (
            f"只修改U為1到{MAX_POSITION_UNIT}的整數；U=2時策略為0/2/4口，"
            "存檔後會立即重新計算。"
        ),
        "manual_edit_help": (
            f"要人工覆寫時只修改manual_target_position為-{2 * position_unit}"
            f"到{2 * position_unit}的整數；"
            "設回null即可恢復自動計算。請勿直接修改final_target_position。"
        ),
    }
    changed = any(snapshot.get(key) != value for key, value in normalized.items())
    if changed:
        snapshot.update(normalized)
        snapshot["final_target_position"] = final_target
        snapshot["updated_at"] = now_text()
        snapshot["trigger"] = "人工修改U或總和倉位檔"
        save_json_atomic(COMBINED_POSITION_PATH, snapshot)
    return snapshot


def combined_position_text(snapshot: dict) -> str:
    override_text = (
        f"，人工覆寫={position_text(snapshot.get('manual_target_position'))}"
        if snapshot.get("manual_target_position") is not None
        else ""
    )
    return (
        f"H方向={direction_text(snapshot.get('h_position'))}，"
        f"EF淨部位={snapshot.get('ef_net', (snapshot.get('e_net') or 0) + (snapshot.get('f_net') or 0))}，"
        f"EF方向={direction_text(snapshot.get('ef_direction', snapshot.get('consensus')))}，"
        f"U={snapshot.get('U', 1)}，"
        f"自動目標={position_text(snapshot.get('calculated_target_position'))}"
        f"{override_text}，最終目標={position_text(snapshot.get('final_target_position'))}；"
        f"{snapshot.get('reason', '')}"
    )


def _send_discord_to_target(
    content: str,
    env_name: str,
    webhook_url: str,
    all_webhook_urls: list[str],
) -> bool:
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                webhook_url,
                params={"wait": "true"},
                json={"username": "NotifierBot", "content": content},
                timeout=15,
            )
            response.raise_for_status()
            response_payload = response.json()
            message_id = response_payload.get("id")
            if not message_id:
                raise requests.exceptions.RequestException(
                    "Discord 未回傳已建立的訊息 ID"
                )
            print(
                f"✅ Discord 訊息已送達 [{env_name}]: "
                f"message_id={message_id}"
            )
            return True
        except (requests.exceptions.RequestException, ValueError) as exc:
            safe_error = str(exc)
            for configured_url in all_webhook_urls:
                safe_error = safe_error.replace(configured_url, "<Discord webhook>")
            if attempt == max_attempts:
                print(
                    f"❌ 發送 Discord 訊息失敗 [{env_name}]"
                    f"（已嘗試{max_attempts}次）: "
                    f"{safe_error}"
                )
                return False
            retry_delay = attempt
            print(
                f"⚠️ Discord 訊息送出失敗 [{env_name}]，"
                f"{retry_delay}秒後重試"
                f"（{attempt}/{max_attempts}）: {safe_error}"
            )
            time.sleep(retry_delay)

    return False


def send_discord_message(
    content: str,
    webhook_env: str = DISCORD_WEBHOOK_ENV,
) -> bool:
    """Send to one selected Discord destination and confirm delivery."""
    webhook_url = os.getenv(webhook_env, "").strip()
    if not webhook_url:
        print(f"❌ Discord webhook 未設定: {webhook_env}")
        return False
    configured_urls = [
        value
        for env_name in (DISCORD_WEBHOOK_ENV, SECONDARY_DISCORD_WEBHOOK_ENV)
        if (value := os.getenv(env_name, "").strip())
    ]
    return _send_discord_to_target(
        content,
        webhook_env,
        webhook_url,
        configured_urls,
    )


def send_simulated_order(
    previous_target: int,
    target_position: int,
    *,
    trigger_reason: str,
    decision_summary: str,
) -> bool:
    """Simulate the target-position adjustment by sending Discord only."""
    if (
        isinstance(previous_target, bool)
        or not isinstance(previous_target, int)
        or abs(previous_target) > MAX_TARGET_QUANTITY
    ):
        raise ValueError(f"前次模擬部位超出範圍: {previous_target}")
    if (
        isinstance(target_position, bool)
        or not isinstance(target_position, int)
        or abs(target_position) > MAX_TARGET_QUANTITY
    ):
        raise ValueError(f"目標模擬部位超出範圍: {target_position}")

    action, side, quantity = simulated_order_action(previous_target, target_position)
    if quantity == 0:
        return True

    message = (
        "🧪【模擬下單｜H3+EF 0/U/2U】\n"
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
LEGACY_SIX_STRATEGY_RECORD_FIELDS = [
    "received_at",
    "message_time",
    "account",
    "strategy_code",
    "raw_strategy_code",
    "strategy_name",
    "previous_position",
    "new_position",
    "action",
    "side",
    "quantity",
    "signal",
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


def append_legacy_six_strategy_record(
    signal,
    raw_message: str,
    *,
    received_at: str | None = None,
) -> None:
    """Keep the former tv_doc event file current for existing consumers."""
    previous = signal.previous_position
    new = signal.new_position
    if previous == 0:
        action = "enter"
        side = "bull" if new > 0 else "bear"
    elif new == 0:
        action = "exit"
        side = "bull" if previous > 0 else "bear"
    else:
        action = "reverse"
        side = "bull" if new > 0 else "bear"

    message_time = ""
    time_match = re.search(
        r"【(?P<month>\d{2})\.(?P<day>\d{2})\s+"
        r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})】",
        raw_message,
    )
    if time_match:
        try:
            received_year = (
                int(received_at[:4])
                if received_at and received_at[:4].isdigit()
                else datetime.now(TZ).year
            )
            message_time = datetime(
                year=received_year,
                month=int(time_match.group("month")),
                day=int(time_match.group("day")),
                hour=int(time_match.group("hour")),
                minute=int(time_match.group("minute")),
                second=int(time_match.group("second")),
                tzinfo=TZ,
            ).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    append_record(
        LEGACY_SIX_STRATEGY_RECORD_PATH,
        LEGACY_SIX_STRATEGY_RECORD_FIELDS,
        {
            "received_at": received_at or now_text(),
            "message_time": message_time,
            "account": signal.account,
            "strategy_code": signal.strategy_code,
            "raw_strategy_code": signal.raw_strategy_code,
            "strategy_name": LEGACY_STRATEGY_NAMES[signal.strategy_code],
            "previous_position": previous,
            "new_position": new,
            "action": action,
            "side": side,
            "quantity": 1,
            "signal": (
                f"《策略》{signal.raw_strategy_code}《倉位》"
                f"{float(previous):.1f} -> {float(new):.1f}"
            ),
        },
    )


def backfill_legacy_six_strategy_records() -> int:
    """Copy newer E/F events into the compatibility CSV after an upgrade."""
    latest_legacy_received_at = ""
    if LEGACY_SIX_STRATEGY_RECORD_PATH.exists():
        with LEGACY_SIX_STRATEGY_RECORD_PATH.open(
            newline="",
            encoding="utf-8",
        ) as handle:
            for row in csv.DictReader(handle):
                received_at = str(row.get("received_at") or "").strip()
                if received_at > latest_legacy_received_at:
                    latest_legacy_received_at = received_at

    if not EF_RECORD_PATH.exists():
        return 0

    appended = 0
    with EF_RECORD_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            received_at = str(row.get("received_at") or "").strip()
            if not received_at or received_at <= latest_legacy_received_at:
                continue
            raw_message = str(row.get("raw_message") or "")
            signal = parse_six_strategy_signal(raw_message)
            if signal is None:
                continue
            append_legacy_six_strategy_record(
                signal,
                raw_message,
                received_at=received_at,
            )
            appended += 1
    return appended


def latest_market_price(at: datetime | None = None) -> float | None:
    """Return the newest one-minute close that was already recorded at `at`."""
    return load_latest_recorded_close(
        WEBHOOK_DATA_1MIN_PATH,
        at or datetime.now(TZ),
    )


def _legacy_number(value: object) -> str:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return "-"
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def latest_mxf_notice_text() -> str:
    tank = "-"
    guerrilla = "-"
    try:
        with MXF_VALUE_CSV_PATH.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("tx_bvav") or row.get("mtx_bvav"):
                    tank = _legacy_number(row.get("tx_bvav"))
                    guerrilla = _legacy_number(row.get("mtx_bvav"))
    except OSError as exc:
        print(f"讀取 MXF 籌碼快照失敗：{exc}")
    return f"籌碼：坦克 {tank}，游擊 {guerrilla}"


def build_legacy_six_strategy_message(signal) -> str:
    """Reproduce the former six-strategy Discord notice from this one listener."""
    positions = load_latest_ef_positions(EF_RECORD_PATH)
    net_position = sum(positions.values())
    if net_position > 0:
        net_position_text = f"多{net_position}口"
    elif net_position < 0:
        net_position_text = f"空{abs(net_position)}口"
    else:
        net_position_text = "空手"
    portfolio_text = "贏家E投組" if signal.strategy_code in PORTFOLIO_E else "贏家F投組"
    strategy_name = LEGACY_STRATEGY_NAMES.get(
        signal.strategy_code,
        signal.strategy_code,
    )
    reference_price = latest_market_price()
    price_text = f"{reference_price:g}" if reference_price is not None else "未知"
    received_time = datetime.now(TZ).strftime("%H:%M:%S")
    return (
        f"[{received_time}]：{portfolio_text}。"
        f"{strategy_name}({signal.strategy_code}) "
        f"{float(signal.previous_position):.1f} -> {float(signal.new_position):.1f}。"
        f"下單價位：{price_text}，下單後策略倉位：{net_position_text}\n"
        f"{latest_mxf_notice_text()}"
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
    if side not in {"bull", "bear"} or not 1 <= quantity <= MAX_TARGET_QUANTITY:
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
    # Every H3-only record is deliberately one unit.  The source may announce
    # two or more lots, but its magnitude must never reach H3 PnL/MDD records.
    position = normalize_h_position(position)
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
            "raw_message": normalize_h_record_message(raw_message),
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
    received_at = now_text()
    append_record(
        EF_RECORD_PATH,
        EF_RECORD_FIELDS,
        {
            "received_at": received_at,
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
    append_legacy_six_strategy_record(
        signal,
        raw_message,
        received_at=received_at,
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
        "ef_net": decision.ef_net,
        "ef_direction": decision.ef_direction,
        "consensus": decision.consensus,
        "relation": decision.relation,
        "target_position": decision.target_position,
        "reason": decision.reason,
        "missing_strategies": list(decision.missing_strategies),
    }
    state["updated_at"] = now_text()


def order_failure_notification_text(
    *,
    failed_at: str,
    target_position: int,
    error_text: str,
    notification_number: int,
) -> str:
    return (
        "❌【實單失敗｜H3+EF 0/U/2U】\n"
        f"時間：{failed_at}\n"
        f"提醒：{notification_number}/{MAX_ORDER_ERROR_NOTIFICATIONS}\n"
        f"目標：{position_text(target_position)}\n"
        f"錯誤：{error_text}\n"
        "結果：本目標不再重送，請人工下單；"
        "策略目標改變後才會再嘗試一次。"
    )


def process_order_failure_reminder(current_time: datetime | None = None) -> bool:
    """Send one due reminder and permanently stop after the fifth notification."""
    state = load_json(STATE_PATH, default_state())
    failure = state.get("active_order_failure")
    if not isinstance(failure, dict):
        return False
    try:
        target_position = int(failure["target_position"])
        notification_count = int(failure.get("notification_count", 0))
    except (KeyError, TypeError, ValueError):
        return False
    if notification_count >= MAX_ORDER_ERROR_NOTIFICATIONS:
        return False
    next_notification_at = failure.get("next_notification_at")
    if not isinstance(next_notification_at, str):
        return False
    try:
        next_time = datetime.strptime(
            next_notification_at,
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=TZ)
    except ValueError:
        return False
    reminder_time = current_time or datetime.now(TZ)
    if reminder_time < next_time:
        return False

    notification_number = notification_count + 1
    error_text = str(failure.get("error") or "未知錯誤")
    failed_at = str(failure.get("failed_at") or next_notification_at)
    notification_sent = send_discord_message(
        order_failure_notification_text(
            failed_at=failed_at,
            target_position=target_position,
            error_text=error_text,
            notification_number=notification_number,
        )
    )
    failure["notification_count"] = notification_number
    failure["last_notification_at"] = reminder_time.strftime("%Y-%m-%d %H:%M:%S")
    if notification_number >= MAX_ORDER_ERROR_NOTIFICATIONS:
        failure["next_notification_at"] = None
        failure["reminders_completed_at"] = failure["last_notification_at"]
        print("實單失敗已提醒5次，停止後續通知")
    else:
        failure["next_notification_at"] = (
            reminder_time
            + timedelta(seconds=ORDER_ERROR_NOTIFICATION_INTERVAL_SECONDS)
        ).strftime("%Y-%m-%d %H:%M:%S")
    state["active_order_failure"] = failure
    save_json_atomic(STATE_PATH, state)
    return notification_sent


def apply_final_position_file(
    trigger_reason: str,
    *,
    decision_summary: str | None = None,
    notify_unchanged: bool = False,
) -> bool:
    """Read the total file, then reconcile it in simulation or real-order mode."""
    state = load_json(STATE_PATH, default_state())
    snapshot = reconcile_manual_override()
    position_unit = validate_position_unit(snapshot.get("U", 1))
    final_target = _target_position(
        snapshot.get("final_target_position"),
        "final_target_position",
        max_abs=2 * position_unit,
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

    orders_enabled = env_flag(ENABLE_ORDERS_ENV)
    target_changed = previous_target is None or normalized_previous != final_target
    if not target_changed and not orders_enabled:
        result_text = (
            f"最終倉位檔未改變，維持 {position_text(final_target)}，"
            "不送Discord模擬單"
        )
        print(result_text)
        success = True
        if notify_unchanged:
            notification = unchanged_target_notification_text(
                timestamp=now_text(),
                trigger_reason=trigger_reason,
                decision_summary=decision_summary or combined_position_text(snapshot),
                target_position=final_target,
            )
            print(notification)
            success = send_discord_message(notification)
            if success:
                state.pop("last_status_notification_error_at", None)
            else:
                state["last_status_notification_error_at"] = now_text()
        save_json_atomic(STATE_PATH, state)
        return success

    executed_previous = normalized_previous
    if orders_enabled:
        last_order_attempt_target = state.get(
            "last_order_attempt_target",
            # Migrate a failure lock written by the previous implementation.
            state.get("last_order_error_target"),
        )
        try:
            normalized_attempt_target = int(last_order_attempt_target)
        except (TypeError, ValueError):
            normalized_attempt_target = None
        if normalized_attempt_target == final_target:
            state["last_order_attempt_target"] = final_target
            save_json_atomic(STATE_PATH, state)
            print(
                "相同實單目標已嘗試過，不論上次成功或失敗都不重送："
                f"{position_text(final_target)}"
            )
            return True
        attempt_time = datetime.now(TZ)
        attempt_time_text = attempt_time.strftime("%Y-%m-%d %H:%M:%S")
        # Persist before contacting the broker so a crash cannot duplicate the order.
        state["last_order_attempt_target"] = final_target
        state["last_order_attempt_at"] = attempt_time_text
        state.pop("active_order_failure", None)
        save_json_atomic(STATE_PATH, state)
        try:
            order_result = execute_target_position(final_target)
            executed_previous = order_result.previous_position
            side_text = "買進" if order_result.side == "buy" else "賣出"
            if order_result.order_sent:
                result_text = f"已送出{side_text} TMF {order_result.quantity}口"
            else:
                result_text = "永豐實際部位已符合目標，未送單"
            notification_sent = send_discord_message(
                "🚨【實單執行｜H3+EF 0/U/2U】\n"
                f"時間：{now_text()}\n"
                f"結果：{result_text}\n"
                f"實際部位：{position_text(executed_previous)} → "
                f"{position_text(order_result.actual_position)}（已向永豐重新查詢確認）\n"
                f"觸發：{trigger_reason}\n"
                f"判斷：{combined_position_text(snapshot)}"
            )
            if not notification_sent:
                state["last_order_notification_error_at"] = now_text()
            else:
                state.pop("last_order_notification_error_at", None)
            # The broker operation succeeded even if its Discord receipt failed.
            state.pop("last_order_error_at", None)
            state.pop("last_order_error_target", None)
            state.pop("last_order_error", None)
            success = True
        except Exception as exc:
            error_text = str(exc)
            state["last_order_error_at"] = attempt_time_text
            state["last_order_error_target"] = final_target
            state["last_order_error"] = error_text
            state["active_order_failure"] = {
                "target_position": final_target,
                "error": error_text,
                "failed_at": attempt_time_text,
                "notification_count": 1,
                "last_notification_at": attempt_time_text,
                "next_notification_at": (
                    attempt_time
                    + timedelta(seconds=ORDER_ERROR_NOTIFICATION_INTERVAL_SECONDS)
                ).strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_json_atomic(STATE_PATH, state)
            print(f"實單執行失敗：{exc}")
            send_discord_message(
                order_failure_notification_text(
                    failed_at=attempt_time_text,
                    target_position=final_target,
                    error_text=error_text,
                    notification_number=1,
                )
            )
            return False
    else:
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
                executed_previous,
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
        if orders_enabled:
            state["last_executed_target"] = final_target
            state["last_executed_target_updated_at"] = now_text()
    else:
        state["last_simulation_error_at"] = now_text()
        state["last_simulation_error_target"] = final_target
    save_json_atomic(STATE_PATH, state)
    return success


def send_strategy_signal_notifications(
    trigger_reasons: list[str],
    *,
    decision_summary: str,
    target_position: int | None,
) -> bool:
    """Deliver every strategy signal before any broker reconciliation starts."""
    all_delivered = True
    total = len(trigger_reasons)
    target_text = (
        position_text(target_position)
        if target_position is not None
        else "尚未產生目標"
    )
    for index, trigger_reason in enumerate(trigger_reasons, start=1):
        notification = (
            "📨【策略訊號｜H3+EF 0/U/2U】\n"
            f"時間：{now_text()}\n"
            f"訊號：{trigger_reason}\n"
            f"批次：{index}/{total}\n"
            f"判斷：{decision_summary}\n"
            f"最終目標：{target_text}\n"
            "順序：本批全部策略訊號通知送達後，才會核對永豐部位並下單。"
        )
        print(notification)
        if not send_discord_message(notification):
            all_delivered = False
    return all_delivered


def execute_signal_batch(trigger_reasons: list[str]) -> None:
    """Notify every accepted signal, then reconcile the final batch target once."""
    if not trigger_reasons:
        return

    state = load_json(STATE_PATH, default_state())
    decision = evaluate_records()
    save_decision(state, decision)
    if not decision.ready or decision.target_position is None:
        message = decision_text(decision)
        print(message)
        save_json_atomic(STATE_PATH, state)
        send_strategy_signal_notifications(
            trigger_reasons,
            decision_summary=message,
            target_position=None,
        )
        return

    batch_trigger = (
        trigger_reasons[0]
        if len(trigger_reasons) == 1
        else f"本批{len(trigger_reasons)}筆訊號，最後訊號：{trigger_reasons[-1]}"
    )
    try:
        snapshot = write_combined_position(decision, batch_trigger)
    except (OSError, ValueError) as exc:
        state["last_combined_position_error_at"] = now_text()
        state["last_combined_position_error"] = str(exc)
        save_json_atomic(STATE_PATH, state)
        send_discord_message(
            f"[{datetime.now(TZ):%H:%M:%S}]：H3+EF總和倉位檔錯誤，"
            f"本次不模擬下單：{exc}"
        )
        return
    message = decision_text(decision, snapshot["U"])
    print(message)
    save_json_atomic(STATE_PATH, state)

    notifications_delivered = send_strategy_signal_notifications(
        trigger_reasons,
        decision_summary=message,
        target_position=snapshot["final_target_position"],
    )
    if not notifications_delivered:
        state = load_json(STATE_PATH, default_state())
        state["last_preorder_notification_error_at"] = now_text()
        state["last_preorder_notification_triggers"] = list(trigger_reasons)
        save_json_atomic(STATE_PATH, state)
        print("本批策略訊號未全數送達Discord，安全起見不下單")
        return

    state = load_json(STATE_PATH, default_state())
    state.pop("last_preorder_notification_error_at", None)
    state.pop("last_preorder_notification_triggers", None)
    save_json_atomic(STATE_PATH, state)
    apply_final_position_file(
        batch_trigger,
        decision_summary=message,
        notify_unchanged=False,
    )


def execute_current_decision(trigger_reason: str) -> None:
    execute_signal_batch([trigger_reason])


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
        async with state_lock:
            await asyncio.to_thread(execute_signal_batch, reasons)
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


async def watch_order_failure_reminders() -> None:
    """Persistently deliver at most five one-minute broker failure reminders."""
    while True:
        await asyncio.sleep(2)
        async with state_lock:
            await asyncio.to_thread(process_order_failure_reminder)


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
                f"H方向 {direction_text(previous)}->{direction_text(h_signal.position)}（{action}）"
            )
        else:
            changed = True
            reconciled = apply_six_event(current_state, six_signal, event_key, text)
            legacy_message = build_legacy_six_strategy_message(six_signal)
            print(legacy_message)
            await asyncio.to_thread(
                send_discord_message,
                legacy_message,
                SECONDARY_DISCORD_WEBHOOK_ENV,
            )
            trigger = (
                f"{six_signal.strategy_code} "
                f"{six_signal.previous_position}->{six_signal.new_position}"
            )
            if reconciled:
                trigger += "（已依訊號前倉位校正狀態）"

        current_state["updated_at"] = now_text()
        save_json_atomic(STATE_PATH, current_state)
        print(f"收到訊號 {event_key}: {trigger}")
        if not changed:
            trigger += "（H方向未變）"
        schedule_recompute(trigger)


def acquire_process_lock():
    handle = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("H3+EF 0/U/2U監控程式已經有另一個實例在執行") from exc
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def main() -> None:
    process_lock = acquire_process_lock()
    orders_enabled = env_flag(ENABLE_ORDERS_ENV)
    mode_text = "永豐實單" if orders_enabled else "Discord模擬下單（不連永豐）"
    print(f"=== H3 + EF 0/U/2U {mode_text}監控 ===")
    if send_discord_message("開始自動交易"):
        print("已送出Discord啟動通知：開始自動交易")
    else:
        print("Discord啟動通知送出失敗，監控服務仍會繼續啟動")
    print(f"群益帳號過濾={CAPITAL_ACCOUNT}，執行模式={mode_text}")
    backfilled_count = backfill_legacy_six_strategy_records()
    if backfilled_count:
        print(
            f"已補寫 {backfilled_count} 筆 E/F 訊號到"
            "tv_doc/six_strategy_signal_events.csv"
        )
    sync_combined_ef_strategy_positions()
    current_unit = validate_position_unit(
        load_combined_position().get("U", 1)
        if COMBINED_POSITION_PATH.exists()
        else 1
    )
    print(decision_text(evaluate_records(), current_unit))

    if env_flag("H3_EF_012_SIMULATE_ON_START", default=False):
        execute_current_decision("程式啟動對帳")
    else:
        print("啟動時不送單；收到下一筆有效H或EF訊號後才重新計算")

    watcher_task = client.loop.create_task(watch_combined_position_file())
    failure_reminder_task = client.loop.create_task(watch_order_failure_reminders())
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
        failure_reminder_task.cancel()
        process_lock.close()


if __name__ == "__main__":
    main()
