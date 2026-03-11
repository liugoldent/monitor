import shioaji as sj # 載入永豐金Python API
import os
import requests
import json
import threading
import atexit
import time as pytime
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

def _get_env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"⚠️ 無法解析環境變數 {key}={raw!r}，改用預設值 {default}")
        return default

BUY_ONE_PRICE = _get_env_float("BUY_ONE_PRICE", 0)
SELL_ONE_PRICE = _get_env_float("SELL_ONE_PRICE", 0)

base_dir = os.path.dirname(os.path.abspath(__file__))
ca_path = os.getenv("CA_PATH") or os.path.join(base_dir, "Sinopac.pfx")
WEBHOOK_URL = "https://discord.com/api/webhooks/1379030995348488212/4wjckp5NQhvB2v-YJ5RzUASN_H96RqOm2fzmuz9H26px6cLGcnNHfcBBLq7AKfychT5w"
FUTURE_VALUE_PATH = Path(__file__).resolve().parent / "tv_doc" / "future_max_values.json"
SHORT_CYCLE_STATE_PATH = Path(__file__).resolve().parent / "tv_doc" / "shortCycle.json"
API_LOCK = threading.RLock()
API_CLIENT = None
H_TRADE_FLATTEN_PATH = Path(__file__).resolve().parent / "tv_doc" / "h_trade_flatten.json"


def _parse_number(raw: str) -> float | None:
    if raw is None:
        return None
    text = str(raw).replace(",", "").strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _get_future_max_values() -> tuple[float | None, float | None]:
    if not FUTURE_VALUE_PATH.exists():
        return None, None
    try:
        payload = json.loads(FUTURE_VALUE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    max_buy = _parse_number(payload.get("maxBuyValue"))
    max_sell = _parse_number(payload.get("maxSellValue"))
    return max_buy, max_sell


def _build_api_client():
    api_key = os.getenv("API_KEY2")
    secret_key = os.getenv("SECRET_KEY2")
    if not os.path.exists(ca_path):
        raise FileNotFoundError(f"找不到憑證檔案: {ca_path}")

    api = sj.Shioaji(simulation=False)
    api.login(api_key, secret_key)
    api.activate_ca(
        ca_path=ca_path,
        ca_passwd=os.getenv("PERSON_ID"),
        person_id=os.getenv("PERSON_ID"),
    )
    return api


def _get_api_client():
    global API_CLIENT
    with API_LOCK:
        if API_CLIENT is not None:
            try:
                API_CLIENT.list_positions(API_CLIENT.futopt_account)
                return API_CLIENT
            except Exception:
                try:
                    API_CLIENT.logout()
                except Exception:
                    pass
                API_CLIENT = None
        API_CLIENT = _build_api_client()
        return API_CLIENT


def _shutdown_api_client():
    global API_CLIENT
    with API_LOCK:
        if API_CLIENT is None:
            return
        try:
            API_CLIENT.logout()
        except Exception:
            pass
        API_CLIENT = None


atexit.register(_shutdown_api_client)


def _close_position_with_api(api, test_now: datetime):
    positions = api.list_positions(api.futopt_account)
    contract = api.Contracts.Futures.TMF.TMFR1

    if len(positions) == 0:
        _clear_h_trade_flatten_state()
        return

    if len(positions) > 0:
        if positions[0]['direction'] == 'Buy':
            sellOne(api, contract, len(positions))
            send_discord_message(f'[{test_now:%H:%M:%S}]：短線。丟空單平倉')
            _clear_h_trade_flatten_state()

        if positions[0]['direction'] == 'Sell':
            buyOne(api, contract, len(positions))
            send_discord_message(f'[{test_now:%H:%M:%S}]：短線。丟多單平倉')
            _clear_h_trade_flatten_state()


# 純下單func
def auto_trade(type):
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))

    # current_time = testNow.time()
    # night_start = time(15, 0)
    # night_end = time(5, 0)
    # if current_time >= night_start or current_time < night_end:
    #     closePosition()
    #     send_discord_message(f'[{testNow:%H:%M:%S}] 夜盤時段只做平倉')
    #     return

    try:
        with API_LOCK:
            api = _get_api_client()
            contract = api.Contracts.Futures.TMF.TMFR1
            api.update_status()

            # 先平倉
            _close_position_with_api(api, testNow)

            # 平倉後進新倉 (預設 1 口)
            if type == 'bull':
                buyOne(api, contract)
                send_discord_message(f'[{testNow:%H:%M:%S}]：短線。近月多單進場 go bull')

            if type == 'bear':
                sellOne(api, contract)
                send_discord_message(f'[{testNow:%H:%M:%S}]：短線。近月空單進場 go bear')
        print('送單完成')
    except Exception as e:
        print('送單錯誤',e)


def _clear_h_trade_flatten_state() -> None:
    payload = {
        "side": "",
        "entry_price": 0,
        "add_on_done": False,
        "add_on_quantity": 0,
        "loss_points": 0,
        "trigger_close": 0,
        "updated_at": datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S"),
    }
    H_TRADE_FLATTEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    H_TRADE_FLATTEN_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def closePosition():
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))
    try:
        with API_LOCK:
            api = _get_api_client()
            _close_position_with_api(api, testNow)
    except Exception as e:
        print('送單錯誤',e)


def buyOne(api, contract, quantity=1):
    max_buy, _ = _get_future_max_values()
    price = max_buy
    order = api.Order(
        action=sj.constant.Action.Buy,               # action (買賣別): Buy, Sell
        price=0,                                     # price (價格)
        quantity=quantity,                           # quantity (委託數量)
        price_type=sj.constant.FuturesPriceType.MKT, # price_type (委託價格類別): LMT(限價), MKT(市價), MKP(範圍市價)
        order_type=sj.constant.OrderType.ROD,        # order_type (委託條件): IOC, ROD, FOK
        octype=sj.constant.FuturesOCType.Auto,       # octype (倉別 ): Auto(自動), New(新倉), Cover(平倉), DayTrade(當沖)
        account=api.futopt_account                   # account (下單帳號)
    )
    print("委託內容", order)
    # 執行委託
    trade = api.place_order(contract, order, timeout=0)
    print("委託回傳內容", trade)


def sellOne(api, contract, quantity=1):
    _, max_sell = _get_future_max_values()
    price = max_sell
    order = api.Order(
        action=sj.constant.Action.Sell,               # action (買賣別): Buy, Sell
        price=0,                                      # price (價格)
        quantity=quantity,                            # quantity (委託數量)
        price_type=sj.constant.FuturesPriceType.MKT,  # price_type (委託價格類別): LMT(限價), MKT(市價), MKP(範圍市價)
        order_type=sj.constant.OrderType.ROD,         # order_type (委託條件): IOC, ROD, FOK
        octype=sj.constant.FuturesOCType.Auto,        # octype (倉別 ): Auto(自動), New(新倉), Cover(平倉), DayTrade(當沖)
        account=api.futopt_account                    # account (下單帳號)
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
