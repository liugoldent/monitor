import csv
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events

from auto_trade_six_strategy import (
    auto_trade,
    orders_enabled,
    send_discord_message,
    send_observation_order_notice,
)


TZ = ZoneInfo("Asia/Taipei")
BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "tv_doc" / "six_strategy_position_state.json"
SIGNAL_LOG_PATH = BASE_DIR / "tv_doc" / "six_strategy_signal_events.csv"
WEBHOOK_DATA_1MIN_PATH = BASE_DIR / "tv_doc" / "webhook_data_1min.csv"
SIGNAL_TTL = 10
RECONNECT_DELAY_SECONDS = 5
SIGNAL_LOG_FIELDS = [
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

POSITION_REQUIRED_MARKER = "訊號通知"
AUTO_TRADE_START = "開始自動交易"
AUTO_TRADE_STOP = "停止自動交易"

STRATEGIES = {
    "CFCWIN01m": "智能引擎1號",
    "CFCPW3m": "新財神列車3號",
    "CFCCPm": "財神列車6號",
    "CFCTX16m": "財神列車16號",
    "CFCTX22m": "財神列車22號",
    "CFCTX23m": "財神列車23號",
}

STRATEGY_ALIASES = {
    # Telegram 範例裡少了一個 I；保留別名，避免漏接智能引擎1號。
    "CFCWN01m": "CFCWIN01m",
}

POSITION_CHANGE_PATTERN = re.compile(
    r"《策略》\s*(?P<strategy>[A-Za-z0-9]+)\s*"
    r"《倉位》\s*(?P<old>[+-]?\d+(?:\.\d+)?)\s*->\s*(?P<new>[+-]?\d+(?:\.\d+)?)"
)
MESSAGE_TIME_PATTERN = re.compile(
    r"【(?P<month>\d{2})\.(?P<day>\d{2})\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})】"
)

recent_signals: dict[tuple[str, int, int, str], float] = {}


@dataclass
class StrategySignal:
    received_at: str
    message_time: str
    account: str
    strategy_code: str
    raw_strategy_code: str
    strategy_name: str
    previous_position: int
    new_position: int
    action: str
    side: str
    quantity: int
    raw_text: str
    reference_price: float | None


def load_env_file(path: str = ".env") -> None:
    env_path = BASE_DIR / path
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


load_env_file()

TARGET_BOT_IDENTIFIERS = {
    value.strip().lower().lstrip("@")
    for value in os.getenv("TARGET_BOT_USERNAME", "taiwan_mxf_bot,Capital_monitor").split(",")
    if value.strip()
}
api_id = int(require_env("API_ID"))
api_hash = require_env("API_HASH")
client = TelegramClient("session_monitor_six_strategy", api_id, api_hash)


def _normalize_strategy_code(raw_code: str) -> str:
    code = raw_code.strip()
    return STRATEGY_ALIASES.get(code, code)


def _parse_position(value: str) -> int | None:
    try:
        number = float(value)
    except ValueError:
        return None

    if number in {-1.0, 0.0, 1.0}:
        return int(number)
    return None


def _parse_message_time(text: str) -> str:
    match = MESSAGE_TIME_PATTERN.search(text)
    if not match:
        return ""

    now = datetime.now(TZ)
    try:
        parsed = datetime(
            year=now.year,
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second")),
            tzinfo=TZ,
        )
    except ValueError:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _parse_account(text: str) -> str:
    for bracket_value in re.findall(r"【([^】]+)】", text):
        value = bracket_value.strip()
        if value.isdigit():
            return value
    return ""


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_latest_entry_price() -> float | None:
    if not WEBHOOK_DATA_1MIN_PATH.exists():
        return None

    try:
        with WEBHOOK_DATA_1MIN_PATH.open("r", newline="", encoding="utf-8") as handle:
            last_row = None
            for row in csv.DictReader(handle):
                last_row = row
    except Exception as exc:
        print(f"讀取 1 分 K 進場價位失敗: {exc}")
        return None

    if not last_row:
        return None
    return _to_float(last_row.get("Close"))


def _transition_to_action(previous_position: int, new_position: int) -> tuple[str, str] | None:
    if previous_position == 0 and new_position == 1:
        return "enter", "bull"
    if previous_position == 0 and new_position == -1:
        return "enter", "bear"
    if previous_position == 1 and new_position == 0:
        return "exit", "bull"
    if previous_position == -1 and new_position == 0:
        return "exit", "bear"
    if previous_position == 1 and new_position == -1:
        return "reverse", "bear"
    if previous_position == -1 and new_position == 1:
        return "reverse", "bull"
    return None


def parse_signal(text: str) -> StrategySignal | None:
    if POSITION_REQUIRED_MARKER not in text:
        return None

    match = POSITION_CHANGE_PATTERN.search(text)
    if not match:
        return None

    raw_code = match.group("strategy").strip()
    strategy_code = _normalize_strategy_code(raw_code)
    strategy_name = STRATEGIES.get(strategy_code)
    if not strategy_name:
        return None

    previous_position = _parse_position(match.group("old"))
    new_position = _parse_position(match.group("new"))
    if previous_position is None or new_position is None:
        return None

    action = _transition_to_action(previous_position, new_position)
    if action is None:
        return None

    signal_action, side = action
    return StrategySignal(
        received_at=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        message_time=_parse_message_time(text),
        account=_parse_account(text),
        strategy_code=strategy_code,
        raw_strategy_code=raw_code,
        strategy_name=strategy_name,
        previous_position=previous_position,
        new_position=new_position,
        action=signal_action,
        side=side,
        quantity=1,
        raw_text=text,
        reference_price=read_latest_entry_price(),
    )


def _signal_key(signal: StrategySignal) -> tuple[str, int, int, str]:
    return (
        signal.strategy_code,
        signal.previous_position,
        signal.new_position,
        signal.message_time or signal.raw_text.strip(),
    )


def _is_recent_duplicate(signal: StrategySignal) -> bool:
    now = time.time()
    key = _signal_key(signal)
    last_seen = recent_signals.get(key)
    if last_seen and (now - last_seen) < SIGNAL_TTL:
        return True
    recent_signals[key] = now
    return False


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"strategies": {}}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {"strategies": {}}
    except (OSError, json.JSONDecodeError):
        return {"strategies": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _position_side(position: int) -> str:
    if position == 1:
        return "bull"
    if position == -1:
        return "bear"
    return "flat"


def update_strategy_state(signal: StrategySignal) -> dict:
    state = _load_state()
    strategies = state.setdefault("strategies", {})
    strategies[signal.strategy_code] = {
        "name": signal.strategy_name,
        "position": signal.new_position,
        "side": _position_side(signal.new_position),
        "updated_at": signal.received_at,
        "last_message_time": signal.message_time,
        "last_account": signal.account,
        "last_raw_strategy_code": signal.raw_strategy_code,
        "last_raw_text": signal.raw_text,
    }
    _save_state(state)
    return state


def _safe_position(value: object) -> int:
    try:
        position = int(float(value))
    except (TypeError, ValueError):
        return 0
    return position if position in {-1, 0, 1} else 0


def get_net_position(state: dict) -> int:
    strategies = state.get("strategies", {})
    if not isinstance(strategies, dict):
        return 0

    net_position = 0
    for strategy_code in STRATEGIES:
        strategy_state = strategies.get(strategy_code, {})
        if isinstance(strategy_state, dict):
            net_position += _safe_position(strategy_state.get("position", 0))
    return net_position


def get_previous_net_position_for_signal(state: dict, signal: StrategySignal) -> tuple[int, bool]:
    strategies = state.get("strategies", {})
    if not isinstance(strategies, dict):
        strategies = {}

    adjusted_state = dict(state)
    adjusted_strategies = dict(strategies)
    current_strategy_state = adjusted_strategies.get(signal.strategy_code, {})
    if not isinstance(current_strategy_state, dict):
        current_strategy_state = {}
    else:
        current_strategy_state = dict(current_strategy_state)

    stored_previous_position = _safe_position(current_strategy_state.get("position", 0))
    reconciled = stored_previous_position != signal.previous_position
    if reconciled:
        current_strategy_state.update(
            {
                "name": signal.strategy_name,
                "position": signal.previous_position,
                "side": _position_side(signal.previous_position),
            }
        )
        adjusted_strategies[signal.strategy_code] = current_strategy_state
        adjusted_state["strategies"] = adjusted_strategies

    return get_net_position(adjusted_state), reconciled


def _target_action_for_net_position(net_position: int) -> str:
    if net_position > 0:
        return "bull"
    if net_position < 0:
        return "bear"
    return "close"


def _sender_identifiers(sender) -> set[str]:
    identifiers: set[str] = set()
    if not sender:
        return identifiers

    for attr in ("username", "first_name", "last_name", "title"):
        value = getattr(sender, attr, None)
        if value:
            identifiers.add(str(value).strip().lower().lstrip("@"))
    return identifiers


def append_signal_log(signal: StrategySignal) -> None:
    SIGNAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    exists = SIGNAL_LOG_PATH.exists()
    row = {
        "received_at": signal.received_at,
        "message_time": signal.message_time,
        "account": signal.account,
        "strategy_code": signal.strategy_code,
        "raw_strategy_code": signal.raw_strategy_code,
        "strategy_name": signal.strategy_name,
        "previous_position": signal.previous_position,
        "new_position": signal.new_position,
        "action": signal.action,
        "side": signal.side,
        "quantity": signal.quantity,
        "signal": _signal_log_text(signal),
    }
    with SIGNAL_LOG_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIGNAL_LOG_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _signal_log_text(signal: StrategySignal) -> str:
    return (
        f"《策略》{signal.raw_strategy_code}《倉位》"
        f"{_position_text(signal.previous_position)} -> {_position_text(signal.new_position)}"
    )


def _position_text(position: int) -> str:
    return f"{float(position):.1f}"


def _action_text(signal: StrategySignal) -> str:
    if signal.action == "enter" and signal.side == "bull":
        return "多單進場"
    if signal.action == "enter" and signal.side == "bear":
        return "空單進場"
    if signal.action == "exit" and signal.side == "bull":
        return "多單平倉"
    if signal.action == "exit" and signal.side == "bear":
        return "空單平倉"
    if signal.action == "reverse" and signal.side == "bull":
        return "空翻多"
    if signal.action == "reverse" and signal.side == "bear":
        return "多翻空"
    return "未知動作"


def _price_text(signal: StrategySignal) -> str:
    if signal.reference_price is None:
        return ""
    label = "進場價位" if signal.action in {"enter", "reverse"} else "出場價位"
    return f"，{label} {signal.reference_price:g}"


def _target_action_text(target_action: str, target_quantity: int) -> str:
    if target_action == "bull":
        return f"帳戶目標多單 {target_quantity} 口"
    if target_action == "bear":
        return f"帳戶目標空單 {target_quantity} 口"
    return "帳戶目標平倉"


def build_discord_signal_message(
    signal: StrategySignal,
    net_position: int,
    target_action: str,
    target_quantity: int,
) -> str:
    received_time = datetime.now(TZ).strftime("%H:%M:%S")
    message_time = f"，訊號時間 {signal.message_time}" if signal.message_time else ""
    account = f"，帳號 {signal.account}" if signal.account else ""
    return (
        f"[{received_time}]：六策略訊號。"
        f"{signal.strategy_name}({signal.strategy_code}) "
        f"{_position_text(signal.previous_position)} -> {_position_text(signal.new_position)}，"
        f"{_action_text(signal)}{_price_text(signal)}，策略淨倉位 {net_position}，"
        f"{_target_action_text(target_action, target_quantity)}，觀察模式不下單"
        f"{message_time}{account}"
    )


def run_account_orders(
    signal: StrategySignal,
    previous_net_position: int,
    state_reconciled: bool,
    target_action: str,
    net_position: int,
    target_quantity: int,
) -> None:
    strategy_context = asdict(signal)
    strategy_context["previous_net_position"] = previous_net_position
    strategy_context["state_reconciled"] = state_reconciled
    strategy_context["net_position"] = net_position
    strategy_context["target_action"] = target_action
    strategy_context["target_quantity"] = target_quantity
    try:
        if not orders_enabled():
            send_observation_order_notice(
                target_action,
                strategy=strategy_context,
                quantity=target_quantity,
            )
            return
        auto_trade(target_action, strategy=strategy_context, quantity=target_quantity)
    except Exception as exc:
        message = (
            f"[{datetime.now(TZ):%H:%M:%S}]：六策略訊號。"
            f"{signal.strategy_name}({signal.strategy_code}) 下單發生未處理錯誤：{exc}"
        )
        print(message)
        send_discord_message(message)


@client.on(events.NewMessage)
async def bot_message_handler(event):
    sender = await event.get_sender()
    text = event.text or ""
    signal = parse_signal(text)
    sender_username = getattr(sender, "username", None) if sender else None
    sender_is_target = bool(_sender_identifiers(sender) & TARGET_BOT_IDENTIFIERS)

    # Telegram 訊息在群組/頻道裡有時不會以原 bot 身分出現在 sender。
    # 六策略訊號本身格式已足夠明確，所以 valid signal 不再依賴 sender filter。
    if not signal and not sender_is_target:
        return

    print("台指期 Bot 訊息")
    print("sender:", sender_username, "bot:", getattr(sender, "bot", None) if sender else None)
    print("內容:", text)

    if not signal:
        if AUTO_TRADE_START in text:
            print("解析結果: 自動交易已開始")
        elif AUTO_TRADE_STOP in text:
            print("解析結果: 自動交易已停止")
        else:
            print("略過：不是六策略倉位訊號或轉換格式不支援")
        print("──────────────")
        return

    if _is_recent_duplicate(signal):
        print(
            "略過重複訊號: "
            f"{signal.strategy_name} {signal.previous_position} -> {signal.new_position}"
        )
        print("──────────────")
        return

    previous_state = _load_state()
    previous_net_position, state_reconciled = get_previous_net_position_for_signal(previous_state, signal)
    append_signal_log(signal)
    state = update_strategy_state(signal)
    net_position = get_net_position(state)
    target_action = _target_action_for_net_position(net_position)
    target_quantity = abs(net_position)
    message = build_discord_signal_message(signal, net_position, target_action, target_quantity)
    print(message)
    send_discord_message(message)
    run_account_orders(
        signal,
        previous_net_position,
        state_reconciled,
        target_action,
        net_position,
        target_quantity,
    )

    print(
        "解析結果: "
        f"{signal.strategy_name}({signal.strategy_code}) "
        f"{signal.previous_position} -> {signal.new_position} {_action_text(signal)}，"
        f"策略淨倉位 {net_position}，{_target_action_text(target_action, target_quantity)}"
    )
    print("──────────────")


def main():
    while True:
        try:
            client.start()
            print("Telethon 開始監控 Telegram 六策略倉位訊號...")
            client.run_until_disconnected()
        except (ConnectionError, OSError, TimeoutError) as exc:
            print(f"Telegram 連線中斷：{exc}")
            print(f"{RECONNECT_DELAY_SECONDS} 秒後重新連線...")
            time.sleep(RECONNECT_DELAY_SECONDS)
        except KeyboardInterrupt:
            print("收到停止指令，結束監控。")
            break


if __name__ == "__main__":
    print("=== 六策略自動交易監控程式 ===")
    main()
