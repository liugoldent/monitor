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


def _get_contract(api):
    return api.Contracts.Futures.TMF.TMFR1


def _get_position_quantity(position) -> int:
    raw_qty = getattr(position, "quantity", 0)
    try:
        qty = int(abs(float(raw_qty)))
    except Exception:
        qty = 0
    return qty or 1


def _get_position_direction(position) -> str:
    return str(getattr(position, "direction", "")).strip()


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


def _build_order(api, side: str, quantity: int = 1, price: float | None = None, price_type=None, octype=None):
    action = sj.constant.Action.Buy if side == "buy" else sj.constant.Action.Sell
    resolved_price_type = price_type
    resolved_price = 0 if price is None else price

    if resolved_price_type is None:
        resolved_price_type = (
            sj.constant.FuturesPriceType.MKT
            if price is None
            else sj.constant.FuturesPriceType.LMT
        )

    if resolved_price_type == sj.constant.FuturesPriceType.MKT:
        resolved_price = 0
    else:
        if resolved_price is None:
            raise ValueError("限價單需要提供 price")
        resolved_price = int(round(float(resolved_price)))

    return api.Order(
        action=action,
        price=resolved_price,
        quantity=int(quantity),
        price_type=resolved_price_type,
        order_type=sj.constant.OrderType.ROD,
        octype=octype or sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )


def _place_order(api, contract, side: str, quantity: int = 1, price: float | None = None, price_type=None, octype=None):
    order = _build_order(
        api,
        side=side,
        quantity=quantity,
        price=price,
        price_type=price_type,
        octype=octype,
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


def describe_trade(trade) -> dict:
    order = getattr(trade, "order", None)
    status = getattr(trade, "status", None)
    return {
        "action": _normalize_trade_action(getattr(order, "action", "")),
        "price": getattr(order, "price", None),
        "quantity": getattr(order, "quantity", None),
        "status": _normalize_trade_status(getattr(status, "status", "")),
        "order_id": getattr(status, "id", None),
        "seqno": getattr(status, "seqno", None),
    }


def amend_trade_price(api, trade=None, price: float | None = None, quantity: int | None = None, side: str | None = None):
    target_trade = trade or get_latest_open_trade(api, side=side)
    if target_trade is None:
        raise ValueError("找不到可改價的未成交委託")
    if price is None and quantity is None:
        raise ValueError("至少要提供 price 或 quantity 其中一個")

    kwargs = {}
    if price is not None:
        kwargs["price"] = int(round(float(price)))
    if quantity is not None:
        kwargs["qty"] = int(quantity)

    try:
        api.update_order(target_trade, **kwargs)
    except TypeError:
        api.update_order(trade=target_trade, **kwargs)
    return target_trade


def cancel_trade(api, trade=None, side: str | None = None):
    target_trade = trade or get_latest_open_trade(api, side=side)
    if target_trade is None:
        raise ValueError("找不到可刪除的未成交委託")
    api.cancel_order(target_trade)
    return target_trade


def cancel_all_open_trades(api) -> list:
    trades = list_open_trades(api)
    cancelled = []
    for trade in trades:
        try:
            api.cancel_order(trade)
            cancelled.append(trade)
        except Exception as exc:
            print("刪單失敗", describe_trade(trade), exc)
    return cancelled


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

    if len(positions) == 0:
        _clear_h_trade_flatten_state()
        return

    if len(positions) > 0:
        pos = positions[0]
        pos_qty = _get_position_quantity(pos)
        direction = _get_position_direction(pos)
        if direction == 'Buy':
            sellOne(api, contract, pos_qty)
            send_discord_message(f'[{test_now:%H:%M:%S}]：短線。丟空單平倉')
            _clear_h_trade_flatten_state()

        if direction == 'Sell':
            buyOne(api, contract, pos_qty)
            send_discord_message(f'[{test_now:%H:%M:%S}]：短線。丟多單平倉')
            _clear_h_trade_flatten_state()


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
    return _place_order(api, contract, side="buy", quantity=quantity)


def sellOne(api, contract, quantity=1):
    return _place_order(api, contract, side="sell", quantity=quantity)


def buyOneLimit(api, contract, price: float, quantity=1):
    return _place_order(
        api,
        contract,
        side="buy",
        quantity=quantity,
        price=price,
        price_type=sj.constant.FuturesPriceType.LMT,
    )


def sellOneLimit(api, contract, price: float, quantity=1):
    return _place_order(
        api,
        contract,
        side="sell",
        quantity=quantity,
        price=price,
        price_type=sj.constant.FuturesPriceType.LMT,
    )


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
