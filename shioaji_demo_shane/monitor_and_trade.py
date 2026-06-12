import csv
import os
from pathlib import Path
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

MONITOR_ROOT = Path(__file__).resolve().parent.parent
if str(MONITOR_ROOT) not in sys.path:
    sys.path.insert(0, str(MONITOR_ROOT))

from telethon import TelegramClient, events
import auto_trade as auto_trade_shane_module
from shioaji_demo_rosco import auto_trade as auto_trade_rosco_module
from shioaji_demo_ichih import auto_trade as auto_trade_ichih_module

recent_signals = {}
SIGNAL_TTL = 10 
RECONNECT_DELAY_SECONDS = 5
last_position = ""
TZ = ZoneInfo("Asia/Taipei")
MXF_VALUE_CSV_PATH = MONITOR_ROOT / "backend-futures-py" / "tv_doc" / "mxf_value.csv"
H_TRADE_CSV_PATH = MONITOR_ROOT / "backend-futures-py" / "tv_doc" / "h_trade.csv"

def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        stripped = line.strip()

        # Skip comments/empty lines
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@contextmanager
def temporary_env(values: dict[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


SHANE_ENV = load_env_values(MONITOR_ROOT / "shioaji_demo_shane" / ".env")
ICHIH_ENV = load_env_values(MONITOR_ROOT / "shioaji_demo_ichih" / ".env")


def auto_trade_shane(signal_type: str) -> None:
    auto_trade_shane_module.ca_path = str(MONITOR_ROOT / "shioaji_demo_shane" / "Sinopac.pfx")
    with temporary_env(SHANE_ENV):
        auto_trade_shane_module.auto_trade(signal_type)


def auto_trade_ichih(signal_type: str) -> None:
    auto_trade_ichih_module.ca_path = str(MONITOR_ROOT / "shioaji_demo_ichih" / "Sinopac.pfx")
    with temporary_env(ICHIH_ENV):
        auto_trade_ichih_module.auto_trade(signal_type)


def run_auto_trade(name: str, trade_func, signal_type: str) -> None:
    print(f"開始下單: {name} {signal_type}")
    try:
        trade_func(signal_type)
    except Exception as exc:
        print(f"下單錯誤: {name} {signal_type} {exc}")


load_env_file()

# ======================
# 基本設定
# ======================
api_id = int(require_env("API_ID"))
api_hash = require_env("API_HASH")

# 台指期 Bot
TARGET_BOT_USERNAME = "taiwan_mxf_bot"

client = TelegramClient("session_monitor", api_id, api_hash)

# Match "多1口" or "空1口" with flexible spacing.
POSITION_PATTERN = re.compile(r"(空|多)\s*(\d+)\s*口")
POSITION_REQUIRED_MARKER = "訊號通知"
TARGET_SIGNAL_MARKER = "小H1"
AUTO_TRADE_START = "開始自動交易"
AUTO_TRADE_STOP = "停止自動交易"
SHANE_TEMP_ENTRY_LOSS_GUARD_ENABLED = True
SHANE_TEMP_ENTRY_LOSS_GUARD_REQUIRED_LOSSES = 4
SHANE_TEMP_ENTRY_LOSS_GUARD_START_AFTER_TRADE_ROW = 1219
SHANE_TEMP_ENTRY_LOSS_GUARD_START_AT = datetime(2026, 6, 12, 0, 0, tzinfo=TZ)
SHANE_TEMP_ENTRY_LOSS_GUARD_END_AT = datetime(2026, 6, 19, 0, 0, tzinfo=TZ)


def _parse_pnl_value(raw_value: object) -> float | None:
    raw = str(raw_value).strip()
    if raw == "":
        return None
    raw = raw.replace(",", "")
    raw = raw.replace("－", "-").replace("−", "-").replace("﹣", "-")
    try:
        return float(raw)
    except ValueError:
        return None


def _get_consecutive_loss_count(pnls: list[float]) -> int:
    loss_count = 0
    for pnl in reversed(pnls):
        if pnl < 0:
            loss_count += 1
            continue
        break
    return loss_count


def _get_exiting_pnls_after_trade_row(
    trade_csv_path: Path,
    start_after_trade_row: int,
) -> list[float]:
    if not trade_csv_path.exists():
        return []

    try:
        with trade_csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
    except OSError as exc:
        print(f"連輸護欄讀取 trade csv 失敗: {exc}")
        return []

    try:
        start_row = max(0, int(start_after_trade_row))
    except (TypeError, ValueError):
        start_row = 0

    pnls: list[float] = []
    for trade_row, row in enumerate(rows[1:], start=1):
        if trade_row <= start_row or len(row) < 5:
            continue
        action = str(row[1]).strip().lower()
        if action != "exiting":
            continue
        pnl = _parse_pnl_value(row[4])
        if pnl is None:
            continue
        pnls.append(pnl)
    return pnls


def should_skip_entry_until_consecutive_losses(
    trade_csv_path: Path,
    required_loss_count: int,
    start_after_trade_row: int,
    *,
    active_from: datetime | None = None,
    active_until: datetime | None = None,
    enabled: bool = True,
    now: datetime | None = None,
) -> tuple[bool, str, int]:
    if not enabled:
        return False, "連輸進場護欄未啟用", 0

    now = now or datetime.now(TZ)
    if active_from is not None and now < active_from:
        return False, f"連輸進場護欄尚未開始，開始時間 {active_from:%Y-%m-%d %H:%M:%S}", 0
    if active_until is not None and now >= active_until:
        return False, f"連輸進場護欄已結束，結束時間 {active_until:%Y-%m-%d %H:%M:%S}", 0

    pnls = _get_exiting_pnls_after_trade_row(trade_csv_path, start_after_trade_row)
    consecutive_loss_count = _get_consecutive_loss_count(pnls)
    if consecutive_loss_count >= required_loss_count:
        return (
            False,
            f"連輸進場護欄放行：h_trade row {start_after_trade_row} 後已連輸 "
            f"{consecutive_loss_count}/{required_loss_count} 筆，這次可作為第 {required_loss_count + 1} 次進場",
            consecutive_loss_count,
        )

    return (
        True,
        f"連輸進場護欄啟動中：h_trade row {start_after_trade_row} 後目前連輸 "
        f"{consecutive_loss_count}/{required_loss_count} 筆，未達第 {required_loss_count + 1} 次進場門檻",
        consecutive_loss_count,
    )


def should_skip_shane_temporary_entry_loss_guard(
    now: datetime | None = None,
) -> tuple[bool, str, int]:
    return should_skip_entry_until_consecutive_losses(
        H_TRADE_CSV_PATH,
        SHANE_TEMP_ENTRY_LOSS_GUARD_REQUIRED_LOSSES,
        SHANE_TEMP_ENTRY_LOSS_GUARD_START_AFTER_TRADE_ROW,
        active_from=SHANE_TEMP_ENTRY_LOSS_GUARD_START_AT,
        active_until=SHANE_TEMP_ENTRY_LOSS_GUARD_END_AT,
        enabled=SHANE_TEMP_ENTRY_LOSS_GUARD_ENABLED,
        now=now,
    )

# ======================
# Handler ①：台指期下單 Bot 監控
# ======================
@client.on(events.NewMessage)
async def bot_message_handler(event):
    sender = await event.get_sender()

    # 系統訊息或無 sender
    if not sender:
        return

    # 只處理 bot
    if not getattr(sender, "bot", False):
        return

    # 只處理指定 bot
    if getattr(sender, "username", None) != TARGET_BOT_USERNAME:
        return
    
    print("🤖 台指期 Bot 訊息")
    print("內容:", event.text)

    # Parse position from known message format.
    text = event.text or ""

    if TARGET_SIGNAL_MARKER not in text:
        print(f"略過：訊息不包含 {TARGET_SIGNAL_MARKER}")
        print("──────────────")
        return
    
    match = POSITION_PATTERN.search(text)
    
    if match and POSITION_REQUIRED_MARKER in text:
        position = match.group(1)
        quantity = int(match.group(2))

        now = time.time()
        last_seen = recent_signals.get(position)
        if last_seen and (now - last_seen) < SIGNAL_TTL:
            print(f"略過重複訊號: {position}{quantity} 口 (間隔 {now - last_seen:.1f}s)")
            print("──────────────")
            return

        # h 長週期單API下單 / 短週期平倉
        if position == "多":
            target_side = "bull"
        elif position == "空":
            target_side = "bear"
        else:
            print("──────────────")
            return

        should_skip, guard_reason, _ = should_skip_shane_temporary_entry_loss_guard(datetime.now(TZ))
        recent_signals[position] = now
        if should_skip:
            print(f"略過 shane 進場：{guard_reason}")
            print("──────────────")
            return

        print(guard_reason)
        run_auto_trade("shane", auto_trade_shane, target_side)

        print(f"解析結果: 目前倉位 {position}{quantity} 口")

    if AUTO_TRADE_START in text:
        print("解析結果: 自動交易已開始")
    elif AUTO_TRADE_STOP in text:
        print("解析結果: 自動交易已停止")

    print("──────────────")

# ======================
# 主程式
# ======================
def main():
    while True:
        try:
            client.start()
            print("🚀 Telethon 開始監控 Telegram 訊息...")
            client.run_until_disconnected()
        except (ConnectionError, OSError, TimeoutError) as exc:
            print(f"⚠️ Telegram 連線中斷：{exc}")
            print(f"⏳ {RECONNECT_DELAY_SECONDS} 秒後重新連線...")
            time.sleep(RECONNECT_DELAY_SECONDS)
        except KeyboardInterrupt:
            print("收到停止指令，結束監控。")
            break


if __name__ == "__main__":
    print('=== 台指期自動交易監控程式 shane ichih rosco ===')
    main()
