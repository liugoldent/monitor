import os
from pathlib import Path
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from telethon import TelegramClient, events
from auto_trade import auto_trade
from auto_trade_shortCycle import auto_trade as auto_trade_shortcycle
from auto_trade_shortCycle import _get_api_client as get_api_client_short
from auto_trade_shortCycle import _close_position_with_api as close_position_with_api_short

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
    # print("聊天 ID:", event.chat_id)
    print("內容:", event.text)

    # Parse position from known message format.
    text = event.text or ""
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
            auto_trade("bull")
        elif position == "空":
            auto_trade("bear")

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
