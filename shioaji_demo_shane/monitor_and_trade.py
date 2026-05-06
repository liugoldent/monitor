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
last_position = ""
TZ = ZoneInfo("Asia/Taipei")

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
ROSCO_ENV = load_env_values(MONITOR_ROOT / "shioaji_demo_rosco" / ".env")
ICHIH_ENV = load_env_values(MONITOR_ROOT / "shioaji_demo_ichih" / ".env")


def auto_trade_shane(signal_type: str) -> None:
    auto_trade_shane_module.ca_path = str(MONITOR_ROOT / "shioaji_demo_shane" / "Sinopac.pfx")
    with temporary_env(SHANE_ENV):
        auto_trade_shane_module.auto_trade(signal_type)


def auto_trade_rosco(signal_type: str) -> None:
    auto_trade_rosco_module.ca_path = str(MONITOR_ROOT / "shioaji_demo_rosco" / "Sinopac.pfx")
    with temporary_env(ROSCO_ENV):
        auto_trade_rosco_module.auto_trade(signal_type)


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
        recent_signals[position] = now

        # h 長週期單API下單 / 短週期平倉
        if position == "多":
            run_auto_trade("shane", auto_trade_shane, "bull")
            time.sleep(1)  # 確保下單間有短暫間隔
            run_auto_trade("rosco", auto_trade_rosco, "bull")
            time.sleep(1)  # 確保下單間有短暫間隔
            run_auto_trade("ichih", auto_trade_ichih, "bull")
        elif position == "空":
            run_auto_trade("shane", auto_trade_shane, "bear")
            time.sleep(1)  # 確保下單間有短暫間隔
            run_auto_trade("rosco", auto_trade_rosco, "bear")
            time.sleep(1)  # 確保下單間有短暫間隔
            run_auto_trade("ichih", auto_trade_ichih, "bear")

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
    client.start()
    print("🚀 Telethon 開始監控 Telegram 訊息...")
    client.run_until_disconnected()


if __name__ == "__main__":
    print('=== 台指期自動交易監控程式 ===')
    main()
