import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import shioaji as sj


def load_env_file(path: str = ".env") -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue

            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CA_PATH = os.getenv("CA_PATH") or os.path.join(BASE_DIR, "Sinopac.pfx")
WEBHOOK_URL = "https://discord.com/api/webhooks/1379030995348488212/4wjckp5NQhvB2v-YJ5RzUASN_H96RqOm2fzmuz9H26px6cLGcnNHfcBBLq7AKfychT5w"


def _get_contract(api: sj.Shioaji, code: str):
    try:
        return api.Contracts.Stocks[code]
    except Exception:
        pass
    for market in ("TSE", "OTC"):
        try:
            return getattr(api.Contracts.Stocks, market)[code]
        except Exception:
            continue
    return None


def _get_intraday_odd_lot() -> sj.constant.StockOrderLot:
    try:
        return sj.constant.StockOrderLot.IntradayOdd
    except Exception:
        return sj.constant.StockOrderLot.Odd


def send_discord_message(content: str) -> None:
    payload = {"username": "NotifierBot", "content": content}
    try:
        response = requests.post(WEBHOOK_URL, json=payload)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"❌ 發送 Discord 訊息失敗: {exc}")


def place_intraday_odd_lot(action: str, code: str, price: float, quantity: int) -> str:
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    api_key = os.getenv("ODD_API_KEY")
    secret_key = os.getenv("ODD_API_SECRET")

    if not api_key or not secret_key:
        raise RuntimeError("Missing API_KEY or SECRET_KEY")

    if not os.path.exists(CA_PATH):
        raise RuntimeError(f"找不到憑證檔案: {CA_PATH}")

    api = sj.Shioaji() 
    api.login(api_key, secret_key)
    api.activate_ca(
        ca_path=CA_PATH,
        ca_passwd=os.getenv("PERSON_ID"),
        person_id=os.getenv("PERSON_ID"),
    )

    try:
        # contract = _get_contract(api, code)
        # if not contract:
        #     raise RuntimeError(f"找不到股票代號: {code}")

        # action_enum = sj.constant.Action.Buy if action.lower() == "buy" else sj.constant.Action.Sell
        # computed_quantity = int(10000 // price)
        # if computed_quantity <= 0:
        #     raise RuntimeError(f"計算股數為 0，請調整價格或預算 (price={price})")
        print(111)
        # order = api.Order(
        #     price=price,
        #     quantity=computed_quantity,
        #     action=action_enum,
        #     price_type='LMT',
        #     order_type='ROD',
        #     order_lot='IntradayOdd',
        #     account=api.stock_account
        # )
        # trade = api.place_order(contract, order, timeout=0)
        # send_discord_message(
        #     f"[{now:%H:%M:%S}] 零股下單成功 {code} {action_enum.name} {computed_quantity} 股 @ {price}"
        # )
        # return str(trade)
    except Exception as exc:
        error_message = f"[{now:%H:%M:%S}] 零股下單失敗 {code} {action} @ {price}: {exc}"
        print(error_message)
        send_discord_message(error_message)
        raise
    finally:
        api.logout()
