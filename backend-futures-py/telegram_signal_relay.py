"""Receive H/EF Telegram signals, persist the raw event, and relay it to Discord.

This service deliberately contains no strategy evaluation or broker integration.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from telethon import TelegramClient, events


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
RECORDS_DIR = BASE_DIR / "telegram-relay-records"
EVENT_LOG_PATH = RECORDS_DIR / "telegram_signal_events.jsonl"
TV_DOC_DIR = BASE_DIR / "tv_doc"
EF_SIGNAL_LOG_PATH = TV_DOC_DIR / "six_strategy_signal_events.csv"
H_TRADE_LOG_PATH = TV_DOC_DIR / "h_trade.csv"
WEBHOOK_DATA_1MIN_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
H3_RECORDS_DIR = BASE_DIR / "h3-ef-012-strategy" / "records"
H_POSITION_EVENT_PATH = H3_RECORDS_DIR / "h3_position_events.csv"
EF_POSITION_EVENT_PATH = H3_RECORDS_DIR / "ef_position_events.csv"
SESSION_PATH = Path(
    os.getenv(
        "TELEGRAM_RELAY_SESSION_PATH",
        str(BASE_DIR / "telegram-relay-runtime" / "session_h_ef_relay"),
    )
)
TZ = ZoneInfo("Asia/Taipei")

H_WEBHOOK_ENV = "DISCORD_H_TRADE_WEBHOOK_URL"
EF_WEBHOOK_ENV = "DISCORD_SIX_STRATEGY_WEBHOOK_URL"
H_REQUIRED_MARKER = "浩克3V3訊號通知"
H_POSITION_PATTERN = re.compile(
    r"小型台指近一訊號部位為\s*[:：]\s*(?P<side>多|空)\s*"
    r"(?P<quantity>\d+)\s*口"
)
EF_POSITION_PATTERN = re.compile(
    r"《策略》\s*(?P<strategy>[A-Za-z0-9]+)\s*"
    r"《倉位》\s*(?P<old>[+-]?\d+(?:\.\d+)?)\s*->\s*"
    r"(?P<new>[+-]?\d+(?:\.\d+)?)"
)
ACCOUNT_PATTERN = re.compile(r"【(?P<account>\d+)】")
MESSAGE_TIME_PATTERN = re.compile(
    r"【(?P<month>\d{2})\.(?P<day>\d{2})\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})】"
)
STRATEGY_ALIASES = {"CFCWN01m": "CFCWIN01m"}
STRATEGY_NAMES = {
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
EF_SIGNAL_FIELDS = [
    "received_at", "message_time", "account", "strategy_code",
    "raw_strategy_code", "strategy_name", "previous_position",
    "new_position", "action", "side", "quantity", "signal",
]
H_TRADE_FIELDS = ["timestamp", "action", "side", "price", "pnl", "quantity"]
H_POSITION_EVENT_FIELDS = [
    "received_at", "event_key", "source", "action", "previous_position",
    "new_position", "raw_message",
]
EF_POSITION_EVENT_FIELDS = [
    "received_at", "event_key", "account", "strategy_code", "source",
    "action", "previous_position", "new_position", "state_reconciled",
    "raw_message",
]
DISCORD_CONTENT_LIMIT = 2000
DISCORD_MAX_ATTEMPTS = 3
RECONNECT_DELAY_SECONDS = 5


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必要環境變數: {name}")
    return value


def classify_signal(text: str) -> str | None:
    """Return the relay route for recognized signal messages."""
    if H_REQUIRED_MARKER in text:
        return "h"
    if "訊號通知" in text and EF_POSITION_PATTERN.search(text):
        return "ef"
    return None


def _append_csv(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})
        handle.flush()


def _parse_position(value: str) -> int | None:
    try:
        number = float(value)
    except ValueError:
        return None
    return int(number) if number in {-1.0, 0.0, 1.0} else None


def _position_event_action(previous: int | None, new: int) -> str:
    if previous == new:
        return "部位不變"
    if previous is None:
        return "初始多單" if new > 0 else "初始空單" if new < 0 else "初始空手"
    if previous == 0:
        return "多單進場" if new > 0 else "空單進場"
    if new == 0:
        return "多單平倉" if previous > 0 else "空單平倉"
    return "空單平倉並轉多" if new > 0 else "多單平倉並轉空"


def _event_key_exists(path: Path, event_key: str) -> bool:
    if not event_key or not path.exists():
        return False
    with path.open(newline="", encoding="utf-8") as handle:
        return any(row.get("event_key") == event_key for row in csv.DictReader(handle))


def _latest_h_event_position() -> int | None:
    if not H_POSITION_EVENT_PATH.exists():
        return None
    latest = None
    with H_POSITION_EVENT_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            position = _parse_position(str(row.get("new_position") or ""))
            if position in {-1, 1}:
                latest = position
    return latest


def _latest_ef_event_positions() -> dict[str, int]:
    latest: dict[str, int] = {}
    if not EF_POSITION_EVENT_PATH.exists():
        return latest
    with EF_POSITION_EVENT_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_code = str(row.get("strategy_code") or "").strip()
            code = STRATEGY_ALIASES.get(raw_code, raw_code)
            position = _parse_position(str(row.get("new_position") or ""))
            if code in STRATEGY_NAMES and position is not None:
                latest[code] = position
    return latest


def _normalize_h_record_message(text: str) -> str:
    match = H_POSITION_PATTERN.search(text)
    if not match:
        return text
    start, end = match.span("quantity")
    return f"{text[:start]}1{text[end:]}"


def record_ef_signal(
    text: str,
    received_at: datetime,
    event_key: str = "",
) -> bool:
    match = EF_POSITION_PATTERN.search(text)
    if not match:
        return False
    raw_code = match.group("strategy")
    strategy_code = STRATEGY_ALIASES.get(raw_code, raw_code)
    if strategy_code not in STRATEGY_NAMES:
        return False
    previous = _parse_position(match.group("old"))
    new = _parse_position(match.group("new"))
    if previous is None or new is None or previous == new:
        return False

    if previous == 0:
        action, side = "enter", "bull" if new > 0 else "bear"
    elif new == 0:
        action, side = "exit", "bull" if previous > 0 else "bear"
    else:
        action, side = "reverse", "bull" if new > 0 else "bear"

    message_time = ""
    time_match = MESSAGE_TIME_PATTERN.search(text)
    if time_match:
        try:
            message_time = received_at.replace(
                month=int(time_match.group("month")),
                day=int(time_match.group("day")),
                hour=int(time_match.group("hour")),
                minute=int(time_match.group("minute")),
                second=int(time_match.group("second")),
                microsecond=0,
            ).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    account_match = ACCOUNT_PATTERN.search(text)
    account = account_match.group("account") if account_match else ""
    stored_previous = _latest_ef_event_positions().get(strategy_code)
    if not _event_key_exists(EF_POSITION_EVENT_PATH, event_key):
        _append_csv(
            EF_POSITION_EVENT_PATH,
            EF_POSITION_EVENT_FIELDS,
            {
                "received_at": received_at.strftime("%Y-%m-%d %H:%M:%S"),
                "event_key": event_key,
                "account": account,
                "strategy_code": strategy_code,
                "source": "群益Telegram",
                "action": _position_event_action(stored_previous, new),
                "previous_position": previous,
                "new_position": new,
                "state_reconciled": stored_previous != previous,
                "raw_message": text,
            },
        )
    _append_csv(
        EF_SIGNAL_LOG_PATH,
        EF_SIGNAL_FIELDS,
        {
            "received_at": received_at.strftime("%Y-%m-%d %H:%M:%S"),
            "message_time": message_time,
            "account": account,
            "strategy_code": strategy_code,
            "raw_strategy_code": raw_code,
            "strategy_name": STRATEGY_NAMES[strategy_code],
            "previous_position": previous,
            "new_position": new,
            "action": action,
            "side": side,
            # Keep the existing compatibility CSV convention: one strategy
            # position change is always one unit, including a reversal.
            "quantity": 1,
            "signal": (
                f"《策略》{raw_code}《倉位》"
                f"{float(previous):.1f} -> {float(new):.1f}"
            ),
        },
    )
    return True


def _latest_h_trade() -> tuple[int, float | None]:
    if not H_TRADE_LOG_PATH.exists():
        return 0, None
    position = 0
    entry_price = None
    with H_TRADE_LOG_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("action") == "enter":
                quantity = int(float(row.get("quantity") or 1))
                position = quantity if row.get("side") == "bull" else -quantity
                try:
                    entry_price = float(row.get("price") or "")
                except ValueError:
                    entry_price = None
            elif row.get("action") == "exiting":
                position, entry_price = 0, None
    return position, entry_price


def _latest_mxf_close(cutoff: datetime) -> float | None:
    if not WEBHOOK_DATA_1MIN_PATH.exists():
        return None
    latest_at = None
    latest_close = None
    with WEBHOOK_DATA_1MIN_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                recorded_at = datetime.strptime(
                    row.get("Record Time", ""), "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=TZ)
                close = float(row.get("Close", ""))
            except (TypeError, ValueError):
                continue
            if recorded_at <= cutoff and (latest_at is None or recorded_at > latest_at):
                latest_at, latest_close = recorded_at, close
    return latest_close


def record_h_trade(text: str, received_at: datetime) -> bool:
    match = H_POSITION_PATTERN.search(text)
    if not match:
        return False
    target = 1 if match.group("side") == "多" else -1
    previous, entry_price = _latest_h_trade()
    # H is recorded as a one-unit direction. Repeated same-direction notices
    # are receipts, not new trades.
    previous = 1 if previous > 0 else -1 if previous < 0 else 0
    if previous == target:
        return True

    timestamp = received_at.strftime("%Y-%m-%d %H:%M:%S")
    price = _latest_mxf_close(received_at)
    if previous:
        pnl: float | str = ""
        if price is not None and entry_price is not None:
            pnl = round((price - entry_price) * previous * 10, 2)
        _append_csv(
            H_TRADE_LOG_PATH,
            H_TRADE_FIELDS,
            {
                "timestamp": timestamp,
                "action": "exiting",
                "side": "bull" if previous > 0 else "bear",
                "price": "" if price is None else price,
                "pnl": pnl,
                "quantity": 1,
            },
        )
    _append_csv(
        H_TRADE_LOG_PATH,
        H_TRADE_FIELDS,
        {
            "timestamp": timestamp,
            "action": "enter",
            "side": "bull" if target > 0 else "bear",
            "price": "" if price is None else price,
            "pnl": "",
            "quantity": 1,
        },
    )
    return True


def record_h_signal(
    text: str,
    received_at: datetime,
    event_key: str = "",
) -> bool:
    match = H_POSITION_PATTERN.search(text)
    if not match:
        return False
    target = 1 if match.group("side") == "多" else -1
    previous = _latest_h_event_position()
    if previous != target and not _event_key_exists(H_POSITION_EVENT_PATH, event_key):
        _append_csv(
            H_POSITION_EVENT_PATH,
            H_POSITION_EVENT_FIELDS,
            {
                "received_at": received_at.strftime("%Y-%m-%d %H:%M:%S"),
                "event_key": event_key,
                "source": "浩克3V3",
                "action": _position_event_action(previous, target),
                "previous_position": "" if previous is None else previous,
                "new_position": target,
                "raw_message": _normalize_h_record_message(text),
            },
        )
    # Keep the requested legacy analytical file synchronized as well.
    return record_h_trade(text, received_at)


def _now_text() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def append_event(record: dict[str, Any]) -> None:
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def _discord_chunks(prefix: str, text: str) -> list[str]:
    available = DISCORD_CONTENT_LIMIT - len(prefix)
    if available <= 0:
        raise ValueError("Discord 訊息前綴過長")
    parts = [text[index : index + available] for index in range(0, len(text), available)]
    return [prefix + part for part in (parts or [""])]


def send_to_discord(webhook_url: str, route: str, text: str) -> tuple[bool, str]:
    prefix = f"[Telegram {route.upper()} 原始訊號]\n"
    last_error = "Discord delivery failed"
    for attempt in range(1, DISCORD_MAX_ATTEMPTS + 1):
        try:
            for chunk in _discord_chunks(prefix, text):
                response = requests.post(webhook_url, json={"content": chunk}, timeout=15)
                response.raise_for_status()
            return True, f"delivered (attempt {attempt})"
        except requests.RequestException as exc:
            # Never include webhook URLs in logs or exception output.
            last_error = f"{type(exc).__name__}: Discord delivery failed"
            if attempt < DISCORD_MAX_ATTEMPTS:
                time.sleep(attempt)
    return False, f"{last_error} after {DISCORD_MAX_ATTEMPTS} attempts"


load_env_file()
API_ID = int(require_env("API_ID"))
API_HASH = require_env("API_HASH")
WEBHOOKS = {
    "h": require_env(H_WEBHOOK_ENV),
    "ef": require_env(EF_WEBHOOK_ENV),
}

SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
client = TelegramClient(str(SESSION_PATH), API_ID, API_HASH)


@client.on(events.NewMessage)
async def telegram_message_handler(event) -> None:
    text = event.raw_text or ""
    route = classify_signal(text)
    if route is None:
        return

    sender = await event.get_sender()
    received_at = datetime.now(TZ)
    base_record = {
        "received_at": received_at.isoformat(timespec="seconds"),
        "telegram_message_at": event.date.isoformat() if event.date else None,
        "route": route,
        "chat_id": event.chat_id,
        "message_id": event.id,
        "sender_id": event.sender_id,
        "sender_username": getattr(sender, "username", None) if sender else None,
        "text": text,
    }
    append_event({**base_record, "event": "received"})
    print(f"收到 Telegram {route.upper()} 訊號: {event.chat_id}:{event.id}")

    try:
        recorded = (
            record_h_signal(text, received_at, f"{event.chat_id}:{event.id}")
            if route == "h"
            else record_ef_signal(text, received_at, f"{event.chat_id}:{event.id}")
        )
        record_detail = "recorded" if recorded else "unrecognized signal format"
    except (OSError, ValueError, csv.Error) as exc:
        recorded = False
        record_detail = f"{type(exc).__name__}: CSV recording failed"
    append_event({
        **base_record,
        "event": "csv_record",
        "recorded": recorded,
        "detail": record_detail,
    })
    print(
        f"{route.upper()} CSV {'記錄完成' if recorded else '格式無效，未寫入'}: "
        f"{event.chat_id}:{event.id} ({record_detail})"
    )

    delivered, detail = await asyncio.to_thread(
        send_to_discord,
        WEBHOOKS[route],
        route,
        text,
    )
    append_event(
        {
            **base_record,
            "event": "discord_delivery",
            "delivered": delivered,
            "detail": detail,
        }
    )
    print(
        f"Discord {route.upper()} {'轉送成功' if delivered else '轉送失敗'}: "
        f"{event.chat_id}:{event.id} ({detail})"
    )


def main() -> None:
    print("=== Telegram H/EF 原始訊號接收與 Discord 轉送服務 ===")
    print(f"記錄檔: {EVENT_LOG_PATH}")
    print(f"EF CSV: {EF_SIGNAL_LOG_PATH}")
    print(f"H CSV: {H_TRADE_LOG_PATH}")
    print(f"H position events: {H_POSITION_EVENT_PATH}")
    print(f"EF position events: {EF_POSITION_EVENT_PATH}")
    print(f"H -> {H_WEBHOOK_ENV}; EF -> {EF_WEBHOOK_ENV}")
    print("策略計算與券商下單：停用")
    while True:
        try:
            client.start()
            print("Telethon 已連線，開始接收 H/EF 訊號...")
            client.run_until_disconnected()
        except (ConnectionError, OSError, TimeoutError) as exc:
            print(f"Telegram 連線中斷：{type(exc).__name__}，{RECONNECT_DELAY_SECONDS} 秒後重連")
            time.sleep(RECONNECT_DELAY_SECONDS)
        except KeyboardInterrupt:
            print("收到停止指令，結束接收。")
            return


if __name__ == "__main__":
    main()
