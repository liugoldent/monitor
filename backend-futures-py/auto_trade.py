import shioaji as sj # 載入永豐金Python API
import os
import requests
import csv
from pathlib import Path
from datetime import datetime
from datetime import time
from zoneinfo import ZoneInfo

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
WEBHOOK_URL = "https://discord.com/api/webhooks/1379030995348488212/4wjckp5NQhvB2v-YJ5RzUASN_H96RqOm2fzmuz9H26px6cLGcnNHfcBBLq7AKfychT5w"
TRADE_LOG_PATH = Path(__file__).resolve().parent / "tv_doc" / "h_trade.csv"


def _ensure_trade_log() -> None:
    if TRADE_LOG_PATH.exists():
        return
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADE_LOG_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "action", "side", "price", "pnl"])


def _append_trade(action: str, side: str, price: float, pnl: float | None = None) -> None:
    _ensure_trade_log()
    timestamp = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    with TRADE_LOG_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([timestamp, action, side, price, "" if pnl is None else pnl])


def _get_last_entry() -> tuple[str, float] | None:
    if not TRADE_LOG_PATH.exists():
        return None
    with TRADE_LOG_PATH.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    for row in reversed(rows[1:]):
        if len(row) < 4:
            continue
        action = row[1].strip().lower()
        side = row[2].strip().lower()
        if action == "enter" and side in {"bull", "bear"}:
            try:
                return side, float(row[3])
            except ValueError:
                return None
    return None

# 純下單func
def auto_trade(type):
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))
    API_KEY = os.getenv("API_KEY")
    SECRET_KEY = os.getenv("SECRET_KEY")

    # current_time = testNow.time()
    # night_start = time(15, 0)
    # night_end = time(5, 0)
    # if current_time >= night_start or current_time < night_end:
    #     closePosition()
    #     send_discord_message(f'[{testNow:%H:%M:%S}] 夜盤時段只做平倉')
    #     return

    try:
        if not os.path.exists(ca_path):
            print(f"❌ 找不到憑證檔案，目前嘗試路徑為: {ca_path}")
            return
        else:
            print(f"✅ 憑證檔案路徑: {ca_path}")
        
        api = sj.Shioaji(simulation=False)
        api.login(API_KEY, SECRET_KEY)
        
        api.activate_ca(
            ca_path=ca_path,  # 填入憑證路徑
            ca_passwd=os.getenv("PERSON_ID"),       # ca密碼
            person_id=os.getenv("PERSON_ID"),     # 身份證字號
        )
        positions = api.list_positions(api.futopt_account)
        
        contract = api.Contracts.Futures.TMF.TMFR1

        api.quote.subscribe(contract, quote_type='tick')

        api.update_status()

        # 先平倉
        closePosition()

        # 平倉後進新倉 (預設 1 口)
        if type == 'bull':
            buyOne(api, contract)
            _append_trade("enter", "bull", 34000)
            send_discord_message(f'[{testNow:%H:%M:%S}] 近月多單進場 go bull')

        if type == 'bear':
            sellOne(api, contract)
            _append_trade("enter", "bear", 29500)
            send_discord_message(f'[{testNow:%H:%M:%S}] 近月空單進場 go bear')

        api.logout()
        print('送單完成')
    except Exception as e:
        api.logout()
        print('送單錯誤',e)


def closePosition():
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))
    try:
        api = sj.Shioaji()
        API_KEY = os.getenv("API_KEY")
        SECRET_KEY = os.getenv("SECRET_KEY")
        api.login(API_KEY, SECRET_KEY)

        api.activate_ca(
            ca_path,  # 填入憑證路徑
            ca_passwd=os.getenv("PERSON_ID"),       # ca密碼
            person_id=os.getenv("PERSON_ID"),     # 身份證字號
        )

        positions = api.list_positions(api.futopt_account)
        contract = api.Contracts.Futures.TMF.TMFR1

        if len(positions) > 0:
            if positions[0]['direction'] == 'Buy':
                sellOne(api, contract)
                last_entry = _get_last_entry()
                exit_price = 29500
                if last_entry:
                    _, entry_price = last_entry
                    pnl = exit_price - entry_price
                else:
                    pnl = None
                _append_trade("exiting", "bull", exit_price, pnl)
                send_discord_message(f'[{testNow:%H:%M:%S}] 丟空單平倉')

            if positions[0]['direction'] == 'Sell':
                buyOne(api, contract)
                last_entry = _get_last_entry()
                exit_price = 34000
                if last_entry:
                    _, entry_price = last_entry
                    pnl = entry_price - exit_price
                else:
                    pnl = None
                _append_trade("exiting", "bear", exit_price, pnl)
                send_discord_message(f'[{testNow:%H:%M:%S}] 丟多單平倉')
        api.logout()
    except Exception as e:
        api.logout()
        print('送單錯誤',e)


def buyOne(api, contract, quantity=1):
    order = api.Order(
        action=sj.constant.Action.Buy,               # action (買賣別): Buy, Sell
        price=0,                        # price (價格)
        quantity=quantity,                        # quantity (委託數量)
        price_type=sj.constant.FuturesPriceType.MKT,        # price_type (委託價格類別): LMT(限價), MKT(市價), MKP(範圍市價)
        order_type=sj.constant.OrderType.ROD,           # order_type (委託條件): IOC, ROD, FOK
        octype=sj.constant.FuturesOCType.Auto,           # octype (倉別 ): Auto(自動), New(新倉), Cover(平倉), DayTrade(當沖)
        account=api.futopt_account                 # account (下單帳號)
    )
    print("委託內容", order)
    # 執行委託
    trade = api.place_order(contract, order, timeout=0)
    print("委託回傳內容", trade)


def sellOne(api, contract, quantity=1):
    order = api.Order(
        action=sj.constant.Action.Sell,               # action (買賣別): Buy, Sell
        price=0,                        # price (價格)
        quantity=quantity,                        # quantity (委託數量)
        price_type=sj.constant.FuturesPriceType.MKT,        # price_type (委託價格類別): LMT(限價), MKT(市價), MKP(範圍市價)
        order_type=sj.constant.OrderType.ROD,           # order_type (委託條件): IOC, ROD, FOK
        octype=sj.constant.FuturesOCType.Auto,           # octype (倉別 ): Auto(自動), New(新倉), Cover(平倉), DayTrade(當沖)
        account=api.futopt_account                 # account (下單帳號)
    )
    print("委託內容", order)
    # 執行委託
    trade = api.place_order(contract, order, timeout=0)
    print("委託回傳內容", trade)


def send_discord_message(content: str):
    payload = {
        "username": "NotifierBot",
        "content": content,
    }
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ 發送 Discord 訊息失敗: {e}")
