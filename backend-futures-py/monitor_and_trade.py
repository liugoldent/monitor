import csv
import os
from datetime import datetime
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events
from auto_trade import auto_trade, send_discord_message

recent_signals = {}
SIGNAL_TTL = 10 
RECONNECT_DELAY_SECONDS = 5
last_position = ""
TZ = ZoneInfo("Asia/Taipei")
BASE_DIR = Path(__file__).resolve().parent
MXF_VALUE_CSV_PATH = BASE_DIR / "tv_doc" / "mxf_value.csv"

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
TARGET_SIGNAL_MARKER = "小H1"
AUTO_TRADE_START = "開始自動交易"
AUTO_TRADE_STOP = "停止自動交易"
MTX_BVAV_STRONG_THRESHOLD = 3000
MTX_BVAV_GUARD_MAX_AGE_SECONDS = 10 * 60


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


def _parse_mxf_time(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except ValueError:
        return None


def read_latest_mxf_snapshot() -> dict | None:
    if not MXF_VALUE_CSV_PATH.exists():
        return None

    try:
        with MXF_VALUE_CSV_PATH.open("r", newline="", encoding="utf-8") as handle:
            last_row = None
            for row in csv.DictReader(handle):
                last_row = row
    except Exception as exc:
        print(f"讀取游擊資料失敗: {exc}")
        return None

    if not last_row:
        return None

    snapshot_time = _parse_mxf_time(last_row.get("time"))
    mtx_bvav = _to_float(last_row.get("mtx_bvav"))
    return {
        "time": snapshot_time,
        "mtx_bvav": mtx_bvav,
        "raw": last_row,
    }


def should_block_by_mtx_bvav_guard(signal_side: str) -> tuple[bool, str]:
    snapshot = read_latest_mxf_snapshot()
    if not snapshot:
        return False, "無最新游擊資料，放行"

    snapshot_time = snapshot.get("time")
    mtx_bvav = snapshot.get("mtx_bvav")
    if snapshot_time is None or mtx_bvav is None:
        return False, "最新游擊資料時間或數值無法解析，放行"

    now = datetime.now(TZ)
    age_seconds = (now - snapshot_time).total_seconds()
    if age_seconds > MTX_BVAV_GUARD_MAX_AGE_SECONDS:
        return False, f"最新游擊資料過舊 {snapshot_time:%Y-%m-%d %H:%M:%S}，放行"

    if signal_side == "bear" and mtx_bvav > MTX_BVAV_STRONG_THRESHOLD:
        return True, (
            f"收到空訊號但 mtx_bvav={mtx_bvav:.0f} > {MTX_BVAV_STRONG_THRESHOLD}，"
            f"資料時間 {snapshot_time:%Y-%m-%d %H:%M:%S}"
        )

    if signal_side == "bull" and mtx_bvav < -MTX_BVAV_STRONG_THRESHOLD:
        return True, (
            f"收到多訊號但 mtx_bvav={mtx_bvav:.0f} < -{MTX_BVAV_STRONG_THRESHOLD}，"
            f"資料時間 {snapshot_time:%Y-%m-%d %H:%M:%S}"
        )

    return False, f"游擊防呆通過 mtx_bvav={mtx_bvav:.0f}，資料時間 {snapshot_time:%Y-%m-%d %H:%M:%S}"


def run_account_orders(side: str) -> None:
    try:
        auto_trade(side)
    except Exception as exc:
        print(f"H1 主帳號下單發生未處理錯誤: {exc}")


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

        should_block, guard_reason = should_block_by_mtx_bvav_guard(target_side)
        if should_block:
            recent_signals[position] = now
            message = f"長線。游擊防呆擋單：{guard_reason}"
            print(message)
            try:
                send_discord_message(f"[{datetime.now(TZ):%H:%M:%S}]：{message}")
            except Exception as exc:
                print(f"游擊防呆 Discord 通知失敗: {exc}")
            print("──────────────")
            return

        print(guard_reason)
        recent_signals[position] = now
        run_account_orders(target_side)

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
            print("🚀 Telethon 開始監控 Telegram 訊息（Keedem）...")
            client.run_until_disconnected()
        except (ConnectionError, OSError, TimeoutError) as exc:
            print(f"⚠️ Telegram 連線中斷：{exc}")
            print(f"⏳ {RECONNECT_DELAY_SECONDS} 秒後重新連線...")
            time.sleep(RECONNECT_DELAY_SECONDS)
        except KeyboardInterrupt:
            print("收到停止指令，結束監控。")
            break


if __name__ == "__main__":
    print('=== 台指期自動交易監控程式 ===')
    main()
