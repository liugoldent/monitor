import shioaji as sj # 載入永豐金Python API
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

# 載入環境變數
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
WEBHOOK_URL = "discord webhook url"
TAKE_PROFIT_POINTS = 1000

# 下單數量
def _get_entry_quantity() -> int:
    return 1

# 取得現在倉位是做多還是做空
def _get_current_position_side(api) -> str | None:
    try:
        positions = api.list_positions(api.futopt_account)
    except Exception:
        return None

    if not positions:
        return None

    pos = positions[0]
    direction = str(getattr(pos, "direction", "")).strip().lower()
    if direction == "buy":
        return "bull"
    if direction == "sell":
        return "bear"
    return None

# 純下單func
def auto_trade(type):
    api = sj.Shioaji(simulation=True)
    api.login(os.getenv("API_KEY"), os.getenv("SECRET_KEY"))
    api.activate_ca(ca_path=ca_path, ca_passwd=os.getenv("PERSON_ID"), person_id=os.getenv("PERSON_ID"))
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))

    try:
        if not os.path.exists(ca_path):
            print(f"❌ 找不到憑證檔案，目前嘗試路徑為: {ca_path}")
            return
        else:
            print(f"✅ 憑證檔案路徑: {ca_path}")

        # 送單前檢查是否已有同方向倉位，若有則略過以避免重複下單
        contract = api.Contracts.Futures.TMF.TMFR1
        current_side = _get_current_position_side(api)

        # 確認是否已有同方向倉位，若有則略過下單
        if current_side == type:
            send_discord_message(f'[{testNow:%H:%M:%S}]：長線。忽略重複訊號，當前已是 {type}')
            api.logout()
            print(f'略過重複訊號: 已持有同方向倉位 {type}')
            return

        # 先平倉
        closePosition(api)
        entry_qty = _get_entry_quantity()
        
        # 平倉後進新倉
        if type == 'bull':
            buyOne(api, contract, quantity=entry_qty)
            send_discord_message(f'[{testNow:%H:%M:%S}]：長線。近月多單進場 go bull')

        if type == 'bear':
            sellOne(api, contract, quantity=entry_qty)
            send_discord_message(f'[{testNow:%H:%M:%S}]：長線。近月空單進場 go bear')

        api.logout()
        print('送單完成')
    except Exception as e:
        api.logout()
        print('送單錯誤',e)


def closePosition(api):
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))
    try:
        positions = api.list_positions(api.futopt_account)
        contract = api.Contracts.Futures.TMF.TMFR1

        if len(positions) > 0:
            pos = positions[0]
            pos_qty = len(positions)
            try:
                pos_qty = int(pos_qty)
            except Exception:
                pos_qty = 1
            if pos['direction'] == 'Buy':
                sellOne(api, contract, quantity=pos_qty)
                send_discord_message(f'[{testNow:%H:%M:%S}] 長線。丟空單平倉')
            if pos['direction'] == 'Sell':
                buyOne(api, contract, quantity=pos_qty)
                send_discord_message(f'[{testNow:%H:%M:%S}] 長線。丟多單平倉')
    except Exception as e:
        # api.logout()
        print('送單錯誤',e)


def buyOne(api, contract, quantity=1):
    order = api.Order(
        action=sj.constant.Action.Buy,               # action (買賣別): Buy, Sell
        # price=price - 50,                        # price (價格)
        price=0,                                    # price (價格)
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
        price=0,                                    # price (價格)
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
