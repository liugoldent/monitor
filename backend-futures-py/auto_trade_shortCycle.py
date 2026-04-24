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

base_dir = os.path.dirname(os.path.abspath(__file__))
ca_path = os.getenv("CA_PATH") or os.path.join(base_dir, "Sinopac.pfx")
WEBHOOK_URL = "https://discord.com/api/webhooks/1379030995348488212/4wjckp5NQhvB2v-YJ5RzUASN_H96RqOm2fzmuz9H26px6cLGcnNHfcBBLq7AKfychT5w"
API_LOCK = threading.RLock()
API_CLIENT = None

def _get_contract(api):
    return api.Contracts.Futures.TMF.TMFR1

def _normalize_trade_status(value) -> str:
    text = str(value).strip().lower().replace("_", "").replace("-", "")
    if "." in text:
        text = text.split(".")[-1]
    return text


def _normalize_trade_action(value) -> str:
    text = str(value).strip().lower()
    if "." in text:
        text = text.split(".")[-1]
    return text


def _build_order(api, side: str, quantity: int = 1):
    action = sj.constant.Action.Buy if side == "buy" else sj.constant.Action.Sell

    return api.Order(
        action=action,
        price=0,
        quantity=int(quantity),
        price_type=sj.constant.FuturesPriceType.MKT,
        order_type=sj.constant.OrderType.IOC,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )


def _place_order(api, contract, side: str, quantity: int = 1):
    order = _build_order(
        api,
        side=side,
        quantity=quantity,
    )
    print("委託內容", order)
    trade = api.place_order(contract, order, timeout=0)
    print("委託回傳內容", trade)
    return trade


def list_open_trades(api) -> list:
    try:
        api.update_status(api.futopt_account)
    except TypeError:
        api.update_status()
    trades = api.list_trades()
    active_trades = []
    for trade in trades:
        print(trade)
        status = _normalize_trade_status(getattr(getattr(trade, "status", None), "status", ""))
        if status in {"filled", "cancelled", "failed", "inactive"}:
            continue
        active_trades.append(trade)
    return active_trades


def get_latest_open_trade(api, side: str | None = None):
    trades = list_open_trades(api)
    if side is None:
        return trades[-1] if trades else None

    expected = _normalize_trade_action(side)
    for trade in reversed(trades):
        action = _normalize_trade_action(getattr(getattr(trade, "order", None), "action", ""))
        if action == expected:
            return trade
    return None


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
    contract = _get_contract(api)

    if len(positions) > 0:
        pos = positions[0]
        pos_qty = int(pos['quantity'])
        direction = pos['direction']
        if direction == 'Buy':
            sellOne(api, contract, pos_qty)
            send_discord_message(f'[{test_now:%H:%M:%S}]：短線。丟空單平倉')

        if direction == 'Sell':
            buyOne(api, contract, pos_qty)
            send_discord_message(f'[{test_now:%H:%M:%S}]：短線。丟多單平倉')


# 純下單func
def auto_trade(type):
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))

    try:
        with API_LOCK:
            api = _get_api_client()
            contract = _get_contract(api)
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


def closePosition():
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))
    try:
        with API_LOCK:
            api = _get_api_client()
            _close_position_with_api(api, testNow)
    except Exception as e:
        print('送單錯誤',e)


def buyOne(api, contract, quantity=1):
    return _place_order(api, contract, side="buy", quantity=quantity)


def sellOne(api, contract, quantity=1):
    return _place_order(api, contract, side="sell", quantity=quantity)


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
