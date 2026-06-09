import shioaji as sj # 載入永豐金Python API
import os
import requests
import json
import csv
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
ORDER_EVENT_CSV_PATH = Path(base_dir) / "tv_doc" / "h_reverse_loss_guard_order_events.csv"
WEBHOOK_URL = "https://discord.com/api/webhooks/1379030995348488212/4wjckp5NQhvB2v-YJ5RzUASN_H96RqOm2fzmuz9H26px6cLGcnNHfcBBLq7AKfychT5w"
API_LOCK = threading.RLock()
API_CLIENT = None
NEXT_MONTH_ENTRY_QUANTITY = 1

ORDER_EVENT_HEADER = [
    "timestamp",
    "strategy",
    "result",
    "signal_action",
    "order_action",
    "side",
    "quantity",
    "contract_code",
    "h_position_timestamp",
    "h_side",
    "h_entry_price",
    "h_unrealized_points",
    "signal_reason",
    "broker_status",
    "broker_order_id",
    "broker_message",
    "broker_trade",
    "error",
]

def _get_contract(api):
    # Use the far-month TMF contract for the webhook-driven add-on strategies.
    return api.Contracts.Futures.TMF.TMFR1


def _safe_text(value) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return repr(value)


def _trade_attr_text(trade, path: tuple[str, ...]) -> str:
    value = trade
    for name in path:
        value = getattr(value, name, None)
        if value is None:
            return ""
    return _safe_text(value)


def _append_order_event(
    *,
    signal: dict,
    result: str,
    order_action: str = "",
    contract=None,
    trade=None,
    error: object = "",
) -> None:
    try:
        ORDER_EVENT_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        exists = ORDER_EVENT_CSV_PATH.exists()
        row = {
            "timestamp": datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S"),
            "strategy": signal.get("strategy", "h_reverse_loss_guard"),
            "result": result,
            "signal_action": signal.get("action", ""),
            "order_action": order_action,
            "side": signal.get("side", ""),
            "quantity": signal.get("quantity", ""),
            "contract_code": _safe_text(getattr(contract, "code", "")),
            "h_position_timestamp": signal.get("h_position_timestamp", ""),
            "h_side": signal.get("h_side", ""),
            "h_entry_price": signal.get("h_entry_price", ""),
            "h_unrealized_points": signal.get("h_unrealized_points", ""),
            "signal_reason": signal.get("reason", ""),
            "broker_status": _trade_attr_text(trade, ("status", "status")),
            "broker_order_id": _trade_attr_text(trade, ("status", "id")),
            "broker_message": _trade_attr_text(trade, ("status", "msg")),
            "broker_trade": _safe_text(trade),
            "error": _safe_text(error),
        }
        with ORDER_EVENT_CSV_PATH.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ORDER_EVENT_HEADER)
            if not exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        print("遠月鎖損下單紀錄寫入錯誤", e)


def _signal_label(signal: dict) -> str:
    return str(signal.get("strategy_label") or "H 175點遠月鎖損")


def _mark_next_month_guard_entry(side: str, reason: str = "H1 同步進場") -> None:
    try:
        from strategy_h_next_month_loss_guard import mark_h_next_month_guard_entry

        mark_h_next_month_guard_entry(side, reason=reason)
    except Exception as exc:
        print("遠月一口護欄進場狀態紀錄錯誤", exc)


def _mark_next_month_guard_exit(reason: str = "遠月護欄出場") -> None:
    try:
        from strategy_h_next_month_loss_guard import mark_h_next_month_guard_exit

        mark_h_next_month_guard_exit(reason)
    except Exception as exc:
        print("遠月一口護欄出場狀態紀錄錯誤", exc)

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
    if not api_key or not secret_key:
        raise RuntimeError("Missing API_KEY2 or SECRET_KEY2")
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


def _close_position_with_api(api, test_now: datetime) -> bool:
    positions = api.list_positions(api.futopt_account)
    contract = _get_contract(api)
    closed_position = False

    if len(positions) > 0:
        pos = positions[0]
        pos_qty = int(pos['quantity'])
        side = _position_direction_to_side(pos['direction'])
        if side == 'bull':
            sellOne(api, contract, pos_qty)
            send_discord_message(f'[{test_now:%H:%M:%S}]：主帳號遠月。丟空單平倉')
            closed_position = True

        if side == 'bear':
            buyOne(api, contract, pos_qty)
            send_discord_message(f'[{test_now:%H:%M:%S}]：主帳號遠月。丟多單平倉')
            closed_position = True

    if closed_position:
        _mark_next_month_guard_exit("遠月帳號反手前/手動平倉，同步清除一口護欄狀態")
    return closed_position


def _position_direction_to_side(direction: object) -> str | None:
    direction_text = str(direction).strip().lower()
    if direction_text == "buy":
        return "bull"
    if direction_text == "sell":
        return "bear"
    return None


def _get_current_position(api) -> tuple[str | None, int]:
    """Read the current broker position the same way auto_trade.py does."""
    positions = api.list_positions(api.futopt_account)
    if not positions:
        return None, 0

    pos = positions[0]
    side = _position_direction_to_side(pos["direction"])
    return side, int(pos["quantity"])


def execute_h_reverse_loss_guard_signal(signal: dict) -> bool:
    """Place far-month account orders for H 175-point loss-lock signals."""
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))
    signal_for_log = dict(signal or {})
    strategy_label = _signal_label(signal_for_log)
    action = str(signal_for_log.get("action") or "").strip().lower()
    side = str(signal_for_log.get("side") or "").strip().lower()
    if action not in {"enter", "exit"} or side not in {"bull", "bear"}:
        signal_for_log["action"] = action
        signal_for_log["side"] = side
        _append_order_event(signal=signal_for_log, result="invalid_signal")
        return False

    try:
        quantity = max(1, int(float(signal_for_log.get("quantity", 1))))
    except (TypeError, ValueError):
        quantity = 1
    signal_for_log["action"] = action
    signal_for_log["side"] = side
    signal_for_log["quantity"] = quantity

    try:
        with API_LOCK:
            api = _get_api_client()
            contract = _get_contract(api)
            try:
                api.update_status(api.futopt_account)
            except TypeError:
                api.update_status()
            current_side, current_qty = _get_current_position(api)

            if action == "enter":
                if current_side is not None:
                    send_discord_message(
                        f'[{testNow:%H:%M:%S}]：主帳號遠月。{strategy_label}進場略過，'
                        f'帳戶目前已有 {current_side} {current_qty} 口，訊號為 {side} {quantity} 口'
                    )
                    _append_order_event(
                        signal=signal_for_log,
                        result="skipped_existing_position",
                        contract=contract,
                    )
                    return False

                if side == "bull":
                    trade = buyOne(api, contract, quantity)
                    order_action = "buy"
                else:
                    trade = sellOne(api, contract, quantity)
                    order_action = "sell"
                _append_order_event(
                    signal=signal_for_log,
                    result="sent",
                    order_action=order_action,
                    contract=contract,
                    trade=trade,
                )
                send_discord_message(
                    f'[{testNow:%H:%M:%S}]：主帳號遠月。{strategy_label}進場 {side} {quantity} 口'
                )
                return True

            if current_side != side or current_qty <= 0:
                send_discord_message(
                    f'[{testNow:%H:%M:%S}]：主帳號遠月。{strategy_label}出場略過，'
                    f'帳戶沒有可退的 {side} 鎖損部位'
                )
                _append_order_event(
                    signal=signal_for_log,
                    result="skipped_no_position",
                    contract=contract,
                )
                return False

            exit_quantity = min(quantity, current_qty)
            exit_signal_for_log = dict(signal_for_log)
            exit_signal_for_log["quantity"] = exit_quantity
            if side == "bull":
                trade = sellOne(api, contract, exit_quantity)
                order_action = "sell"
            else:
                trade = buyOne(api, contract, exit_quantity)
                order_action = "buy"
            _append_order_event(
                signal=exit_signal_for_log,
                result="sent",
                order_action=order_action,
                contract=contract,
                trade=trade,
            )
            send_discord_message(
                f'[{testNow:%H:%M:%S}]：主帳號遠月。{strategy_label}出場 {side} {exit_quantity} 口'
            )
            return True
    except Exception as e:
        print(f'{strategy_label}送單錯誤', e)
        _append_order_event(signal=signal_for_log, result="error", error=e)
        send_discord_message(f'[{testNow:%H:%M:%S}]：主帳號遠月。{strategy_label}送單錯誤：{e}')
    return False


def execute_h_next_month_guard_signal(signal: dict) -> bool:
    """Place exit orders for the H far-month same-direction 1-lot loss guard."""
    signal_for_order = dict(signal or {})
    signal_for_order.setdefault("strategy", "h_next_month_loss_guard")
    signal_for_order.setdefault("strategy_label", "H遠月一口護欄")
    signal_for_order["quantity"] = NEXT_MONTH_ENTRY_QUANTITY

    sent = execute_h_reverse_loss_guard_signal(signal_for_order)
    if sent and str(signal_for_order.get("action") or "").strip().lower() == "exit":
        _mark_next_month_guard_exit(str(signal_for_order.get("reason") or "H遠月一口護欄出場"))
    return sent


# 純下單func
def auto_trade(type):
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))
    target_side = str(type or "").strip().lower()
    if target_side not in {"bull", "bear"}:
        print("遠月下單方向錯誤", type)
        return

    try:
        with API_LOCK:
            api = _get_api_client()
            contract = _get_contract(api)
            api.update_status()
            current_side, current_qty = _get_current_position(api)

            if current_side == target_side and current_qty == NEXT_MONTH_ENTRY_QUANTITY:
                _mark_next_month_guard_entry(target_side, reason="H1 同方向訊號，遠月已持有固定1口，補同步護欄狀態")
                send_discord_message(
                    f'[{testNow:%H:%M:%S}]：主帳號遠月。忽略重複訊號，'
                    f'目前已是 {target_side} {current_qty} 口'
                )
                print(f'略過遠月重複訊號: 已持有同方向倉位 {target_side} {current_qty} 口')
                return

            # 先平倉
            _close_position_with_api(api, testNow)

            # 平倉後進新倉，遠月護欄策略固定 1 口，不加碼。
            if target_side == 'bull':
                buyOne(api, contract, NEXT_MONTH_ENTRY_QUANTITY)
                _mark_next_month_guard_entry(target_side)
                send_discord_message(f'[{testNow:%H:%M:%S}]：主帳號遠月。固定1口多單進場 go bull')

            if target_side == 'bear':
                sellOne(api, contract, NEXT_MONTH_ENTRY_QUANTITY)
                _mark_next_month_guard_entry(target_side)
                send_discord_message(f'[{testNow:%H:%M:%S}]：主帳號遠月。固定1口空單進場 go bear')
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
