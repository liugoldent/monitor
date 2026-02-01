import shioaji as sj # 載入永豐金Python API
import os
import requests
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import sys

class StdoutFilter:
    def __init__(self, real_stdout):
        self.real_stdout = real_stdout
        self.block_keywords = [
            "Exchange.TAIFEX Tick(",
        ]

    def write(self, msg):
        if any(k in msg for k in self.block_keywords):
            return
        self.real_stdout.write(msg)

    def flush(self):
        self.real_stdout.flush()

# 套用 stdout filter
sys.stdout = StdoutFilter(sys.stdout)
sys.stderr = StdoutFilter(sys.stderr)

def load_env_file(path: str = ".env") -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            stripped = line.strip()

            # Skip comments/empty lines
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue

            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()
base_dir = os.path.dirname(os.path.abspath(__file__))
ca_path = os.getenv("CA_PATH") or os.path.join(base_dir, "Sinopac.pfx")

api = sj.Shioaji(simulation=False)
API_KEY = os.getenv("API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

api.login(API_KEY, SECRET_KEY)

api.activate_ca(
    ca_path=ca_path,  # 填入憑證路徑
    ca_passwd=os.getenv("PERSON_ID"),       # ca密碼
    person_id=os.getenv("PERSON_ID"),     # 身份證字號
)

tx_contract = api.Contracts.Futures.TMF.TMFR1

# ======================
# 一分鐘節流控制
# ======================
last_minute = None

def on_tick(exchange, tick):
    global last_minute

    minute_key = tick.datetime.strftime("%Y-%m-%d %H:%M")

    # 只在跨分鐘時輸出一次
    if minute_key != last_minute:
        last_minute = minute_key

        price = tick.close
        volume = tick.total_volume
        ts = tick.datetime.strftime("%H:%M:%S")

        print(f"[1 MIN] {ts} | 價格={price} | 累積量={volume}")

api.quote.on_tick = on_tick

api.quote.subscribe(
    tx_contract,
    quote_type=sj.constant.QuoteType.Tick,
    version=sj.constant.QuoteVersion.v1
)

print("🚀 已訂閱台指期（每分鐘一次）")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    api.quote.unsubscribe(tx_contract, sj.constant.QuoteType.Tick)
