import shioaji as sj # 載入永豐金Python API
import os
import json
import requests
import csv
import time as pytime
from pathlib import Path
from collections import deque
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
WEBHOOK_DATA_PATH = Path(__file__).resolve().parent / "tv_doc" / "webhook_data_1min.csv"


def _ensure_trade_log() -> None:
    if TRADE_LOG_PATH.exists():
        return
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADE_LOG_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "action", "side", "price", "pnl", "quantity"])


def _append_trade(
    action: str,
    side: str,
    price: float,
    pnl: float | None = None,
    quantity: int | None = None,
) -> None:
    _ensure_trade_log()
    timestamp = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
    with TRADE_LOG_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [timestamp, action, side, price, "" if pnl is None else pnl, "" if quantity is None else quantity]
        )


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


def _parse_pnl_value(raw_value: object) -> float | None:
    raw = str(raw_value).strip()
    if raw == "":
        return None
    # CSV 內可能會出現全形負號或千分位逗號，先正規化再轉數字
    raw = raw.replace(",", "")
    raw = raw.replace("－", "-").replace("−", "-").replace("﹣", "-")
    try:
        return float(raw)
    except ValueError:
        return None


def _get_recent_exiting_pnls(limit: int = 3) -> list[float]:
    if not TRADE_LOG_PATH.exists():
        return []
    pnls: list[float] = []
    with TRADE_LOG_PATH.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    for row in reversed(rows[1:]):
        if len(row) < 5:
            continue
        action = str(row[1]).strip().lower()
        if action != "exiting":
            continue
        pnl = _parse_pnl_value(row[4])
        if pnl is None:
            continue
        pnls.append(pnl)
        if len(pnls) >= limit:
            break
    return pnls


def _get_latest_loss_streak_pnl() -> float:
    pnls = _get_recent_exiting_pnls(50)
    if not pnls:
        return 0.0

    streak_total = 0.0
    for pnl in pnls:
        if pnl >= 0:
            break
        streak_total += pnl
    return streak_total


def _get_entry_quantity() -> int:
    loss_streak_pnl = _get_latest_loss_streak_pnl()
    if loss_streak_pnl == 0:
        return 1

    if loss_streak_pnl <= -20000:
        return 3
    if loss_streak_pnl <= -10000:
        return 2
    return 1


def _get_latest_webhook_close() -> float | None:
    if not WEBHOOK_DATA_PATH.exists():
        return None
    with WEBHOOK_DATA_PATH.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        last_row = None
        for row in reader:
            last_row = row
    if not last_row:
        return None
    close_value = last_row.get("Close")
    if close_value is None:
        return None
    try:
        return float(str(close_value).replace(",", "").strip())
    except ValueError:
        return None


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


# 刪單
def _cancel_all_open_orders(api) -> int:
    try:
        try:
            api.update_status(api.futopt_account)
        except TypeError:
            api.update_status()
        trades = api.list_trades()
    except Exception as exc:
        print(f"⚠️ 查詢掛單失敗: {exc}")
        return 0

    cancelled = 0
    for trade in trades:
        try:
            status = str(getattr(trade.status, "status", "")).strip().lower()
            if "." in status:
                status = status.split(".")[-1]
            status = status.replace("_", "").replace("-", "")
            if status in {"filled", "cancelled", "failed", "inactive"}:
                continue
            api.cancel_order(trade)
            cancelled += 1
        except Exception as exc:
            print(f"⚠️ 刪單失敗: {exc}")
    return cancelled


# 純下單func
def auto_trade(type):
    api = sj.Shioaji(simulation=False)
    api.login(os.getenv("API_KEY"), os.getenv("SECRET_KEY"))
    api.activate_ca(ca_path=ca_path, ca_passwd=os.getenv("PERSON_ID"), person_id=os.getenv("PERSON_ID"))
    testNow = datetime.now(ZoneInfo("Asia/Taipei"))

    try:
        if not os.path.exists(ca_path):
            print(f"❌ 找不到憑證檔案，目前嘗試路徑為: {ca_path}")
            return
        else:
            print(f"✅ 憑證檔案路徑: {ca_path}")

        contract = api.Contracts.Futures.TMF.TMFR1
        current_side = _get_current_position_side(api)

        if current_side == type:
            send_discord_message(f'[{testNow:%H:%M:%S}]：長線。忽略重複訊號，當前已是 {type}')
            api.logout()
            print(f'略過重複訊號: 已持有同方向倉位 {type}')
            return

        # 先平倉
        closePosition(api)
        entry_qty = _get_entry_quantity()
        latest_close = _get_latest_webhook_close()
        
        # 平倉後進新倉
        if type == 'bull':
            buyOne(api, contract, entry_qty)
            entry_price = latest_close
            _append_trade("enter", "bull", entry_price, quantity=entry_qty)
            send_discord_message(f'[{testNow:%H:%M:%S}]：長線。近月多單進場 go bull')

        if type == 'bear':
            sellOne(api, contract, entry_qty)
            entry_price = latest_close
            _append_trade("enter", "bear", entry_price, quantity=entry_qty)
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
        print("目前倉位", positions)
        contract = api.Contracts.Futures.TMF.TMFR1

        if len(positions) > 0:
            pos = positions[0]
            print(pos['quantity'], '目前倉位數量') # 這個可以用
            pos_qty = int(pos['quantity'])
            if pos['direction'] == 'Buy':
                sellOne(api, contract, pos_qty)
                last_entry = _get_last_entry()
                exit_price = _get_latest_webhook_close()
                if last_entry:
                    _, entry_price = last_entry
                    pnl = (exit_price - entry_price) * 10
                else:
                    pnl = None
                _append_trade("exiting", "bull", exit_price, pnl, quantity=pos_qty)
                send_discord_message(f'[{testNow:%H:%M:%S}] 長線。丟空單平倉')
            if pos['direction'] == 'Sell':
                buyOne(api, contract, pos_qty)
                last_entry = _get_last_entry()
                exit_price = _get_latest_webhook_close()
                if last_entry:
                    _, entry_price = last_entry
                    pnl = (entry_price - exit_price) * 10
                else:
                    pnl = None
                _append_trade("exiting", "bear", exit_price, pnl, quantity=pos_qty)
                send_discord_message(f'[{testNow:%H:%M:%S}] 長線。丟多單平倉')
    except Exception as e:
        # api.logout()
        print('送單錯誤',e)


def buyOne(api, contract, quantity=1):
    order = api.Order(
        action=sj.constant.Action.Buy,               # action (買賣別): Buy, Sell
        price=0,                                    # price (價格)
        quantity=quantity,                        # quantity (委託數量)
        price_type=sj.constant.FuturesPriceType.MKT,        # price_type (委託價格類別): LMT(限價), MKT(市價), MKP(範圍市價)
        order_type=sj.constant.OrderType.IOC,           # order_type (委託條件): IOC, ROD, FOK
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
        order_type=sj.constant.OrderType.IOC,           # order_type (委託條件): IOC, ROD, FOK
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
