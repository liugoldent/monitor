import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import shioaji as sj


TZ = ZoneInfo("Asia/Taipei")
BASE_DIR = Path(__file__).resolve().parent


def load_env_file(path: str = ".env") -> None:
    env_path = BASE_DIR / path
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

ca_path = os.getenv("CA_PATH") or str(BASE_DIR / "Sinopac.pfx")
DISCORD_WEBHOOK_ENV = "DISCORD_SIX_STRATEGY_WEBHOOK_URL"
WEBHOOK_URL = (
    os.getenv(DISCORD_WEBHOOK_ENV)
    or os.getenv("DISCORD_H_TRADE_WEBHOOK_URL")
    or ""
).strip()
ENTRY_QUANTITY = int(os.getenv("SIX_STRATEGY_ENTRY_QUANTITY", "1"))
# Safety switch: keep this False while the six-strategy monitor is in observation mode.
ENABLE_ORDERS = False


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def send_discord_message(content: str) -> None:
    if not WEBHOOK_URL:
        print(f"Discord webhook 未設定: {DISCORD_WEBHOOK_ENV} 或 DISCORD_H_TRADE_WEBHOOK_URL")
        return

    payload = {
        "username": "NotifierBot",
        "content": content,
    }
    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"Discord 訊息發送失敗: {exc}")


def _strategy_label(strategy: dict | str | None = None) -> str:
    if isinstance(strategy, dict):
        name = str(strategy.get("strategy_name") or "").strip()
        code = str(strategy.get("strategy_code") or "").strip()
        if name and code:
            return f"{name}({code})"
        return name or code or "六策略"
    if strategy:
        return str(strategy)
    return "六策略"


def _strategy_context_int(strategy: dict | str | None, key: str, default: int = 0) -> int:
    if not isinstance(strategy, dict):
        return default
    try:
        return int(float(strategy.get(key, default)))
    except (TypeError, ValueError):
        return default


def _strategy_context_float(strategy: dict | str | None, key: str) -> float | None:
    if not isinstance(strategy, dict):
        return None
    try:
        return float(str(strategy.get(key, "")).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _position_value(position, key: str):
    try:
        return position[key]
    except Exception:
        return getattr(position, key, None)


def _position_direction_to_side(direction: object) -> str | None:
    direction_text = str(direction).strip().lower()
    if "." in direction_text:
        direction_text = direction_text.split(".")[-1]
    if direction_text == "buy":
        return "bull"
    if direction_text == "sell":
        return "bear"
    return None


def _position_quantity(position) -> int:
    quantity = _position_value(position, "quantity")
    try:
        return max(1, int(float(quantity)))
    except (TypeError, ValueError):
        return 1


def _normalize_order_quantity(quantity: object, default: int = ENTRY_QUANTITY) -> int:
    try:
        return max(0, int(float(quantity)))
    except (TypeError, ValueError):
        return default


def _get_contract(api):
    return api.Contracts.Futures.TMF.TMFR1


def _get_current_net_position(api) -> int:
    try:
        positions = api.list_positions(api.futopt_account)
    except Exception:
        return 0

    net_position = 0
    for position in positions or []:
        side = _position_direction_to_side(_position_value(position, "direction"))
        quantity = _position_quantity(position)
        if side == "bull":
            net_position += quantity
        elif side == "bear":
            net_position -= quantity
    return net_position


def _get_current_position(api) -> tuple[str | None, int]:
    net_position = _get_current_net_position(api)
    if net_position == 0:
        return None, 0

    if net_position > 0:
        return "bull", net_position
    return "bear", abs(net_position)


def _net_position_text(net_position: int) -> str:
    if net_position > 0:
        return f"多單 {net_position} 口"
    if net_position < 0:
        return f"空單 {abs(net_position)} 口"
    return "空手"


def _target_net_position(action: str, quantity: object) -> int:
    target_quantity = _normalize_order_quantity(quantity, default=ENTRY_QUANTITY)
    if action == "bull":
        return target_quantity
    if action == "bear":
        return -target_quantity
    return 0


def _dry_run_order_notice(
    *,
    action: str,
    strategy: dict | str | None,
    quantity: object = None,
) -> bool:
    test_now = datetime.now(TZ)
    label = _strategy_label(strategy)
    current_net_position = _strategy_context_int(strategy, "previous_net_position", 0)
    desired_net_position = _target_net_position(action, quantity)
    delta = desired_net_position - current_net_position

    if delta > 0:
        theoretical_action = f"理論動作：丟多單 {delta} 口"
    elif delta < 0:
        theoretical_action = f"理論動作：丟空單 {abs(delta)} 口"
    else:
        theoretical_action = "理論動作：不需送單"

    reconcile_text = ""
    if isinstance(strategy, dict) and strategy.get("state_reconciled"):
        reconcile_text = "，已依訊號前倉位校正本地狀態"

    reference_price = _strategy_context_float(strategy, "reference_price")
    price_text = f"，參考價位 {reference_price:g}" if reference_price is not None else ""

    message = (
        f"[{test_now:%H:%M:%S}]：{label}。觀察模式，不下單。"
        f"原策略淨倉位 {_net_position_text(current_net_position)}，"
        f"目標 {_net_position_text(desired_net_position)}，{theoretical_action}"
        f"{price_text}{reconcile_text}"
    )
    print(message)
    send_discord_message(message)
    return False


def orders_enabled() -> bool:
    return ENABLE_ORDERS


def send_observation_order_notice(
    action: str,
    strategy: dict | str | None = None,
    quantity: object = None,
) -> bool:
    return _dry_run_order_notice(action=action, strategy=strategy, quantity=quantity)


def _build_order(api, side: str, quantity: int):
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
    order = _build_order(api, side=side, quantity=quantity)
    print("委託內容", order)
    trade = api.place_order(contract, order, timeout=0)
    print("委託回傳內容", trade)
    return trade


def buyOne(api, contract, quantity: int = 1):
    return _place_order(api, contract, side="buy", quantity=quantity)


def sellOne(api, contract, quantity: int = 1):
    return _place_order(api, contract, side="sell", quantity=quantity)


def _login_api():
    if not Path(ca_path).exists():
        raise FileNotFoundError(f"找不到憑證檔案: {ca_path}")

    api = sj.Shioaji(simulation=True)
    api.login(require_env("API_KEY"), require_env("SECRET_KEY"))
    person_id = require_env("PERSON_ID")
    api.activate_ca(ca_path=ca_path, ca_passwd=person_id, person_id=person_id)
    return api


def _update_status(api) -> None:
    try:
        api.update_status(api.futopt_account)
    except TypeError:
        api.update_status()
    except Exception as exc:
        print(f"更新委託狀態失敗: {exc}")


def closePosition(api=None, strategy: dict | str | None = None) -> bool:
    if not ENABLE_ORDERS:
        return _dry_run_order_notice(action="close", strategy=strategy, quantity=0)

    should_logout = False
    test_now = datetime.now(TZ)
    label = _strategy_label(strategy)

    if api is None:
        try:
            api = _login_api()
            should_logout = True
        except Exception as exc:
            message = f"[{test_now:%H:%M:%S}]：{label}。Shioaji 登入/憑證啟用失敗，未平倉：{exc}"
            print(message)
            send_discord_message(message)
            return False

    try:
        _update_status(api)
        positions = api.list_positions(api.futopt_account)
        print("目前倉位", positions)
        if not positions:
            send_discord_message(f"[{test_now:%H:%M:%S}]：{label}。平倉略過，帳戶目前沒有倉位")
            return False

        contract = _get_contract(api)
        closed = False
        for position in positions:
            side = _position_direction_to_side(_position_value(position, "direction"))
            quantity = _position_quantity(position)
            if side == "bull":
                sellOne(api, contract, quantity)
                send_discord_message(f"[{test_now:%H:%M:%S}]：{label}。多單平倉，丟空單平 {quantity} 口")
                closed = True
            elif side == "bear":
                buyOne(api, contract, quantity)
                send_discord_message(f"[{test_now:%H:%M:%S}]：{label}。空單平倉，丟多單平 {quantity} 口")
                closed = True
            else:
                print(f"無法辨識倉位方向，略過: {position}")
        return closed
    except Exception as exc:
        message = f"[{test_now:%H:%M:%S}]：{label}。平倉錯誤：{exc}"
        print(message)
        send_discord_message(message)
        return False
    finally:
        if should_logout:
            try:
                api.logout()
            except Exception:
                pass


def auto_trade(
    action: str,
    strategy: dict | str | None = None,
    quantity: int | None = None,
) -> bool:
    target_action = str(action or "").strip().lower()
    if target_action in {"exit", "flat"}:
        target_action = "close"

    test_now = datetime.now(TZ)
    label = _strategy_label(strategy)

    if not ENABLE_ORDERS:
        return _dry_run_order_notice(action=target_action, strategy=strategy, quantity=quantity)

    if target_action == "close":
        return closePosition(strategy=strategy)

    if target_action not in {"bull", "bear"}:
        message = f"[{test_now:%H:%M:%S}]：{label}。下單方向錯誤：{action}"
        print(message)
        send_discord_message(message)
        return False

    api = None
    try:
        api = _login_api()
        print(f"憑證檔案路徑: {ca_path}")

        _update_status(api)
        contract = _get_contract(api)
        target_quantity = _normalize_order_quantity(quantity, default=ENTRY_QUANTITY)
        if target_quantity <= 0:
            return closePosition(api=api, strategy=strategy)

        desired_net_position = target_quantity if target_action == "bull" else -target_quantity
        current_net_position = _get_current_net_position(api)

        if current_net_position == desired_net_position:
            send_discord_message(
                f"[{test_now:%H:%M:%S}]：{label}。忽略重複訊號，"
                f"帳戶目前已是 {_net_position_text(current_net_position)}"
            )
            print(f"略過重複訊號: 目前倉位已符合目標 {_net_position_text(current_net_position)}")
            return False

        delta = desired_net_position - current_net_position

        if delta > 0:
            buyOne(api, contract, delta)
            send_discord_message(
                f"[{test_now:%H:%M:%S}]：{label}。目標 {_net_position_text(desired_net_position)}，"
                f"原本 {_net_position_text(current_net_position)}，丟多單 {delta} 口"
            )
        else:
            sell_quantity = abs(delta)
            sellOne(api, contract, sell_quantity)
            send_discord_message(
                f"[{test_now:%H:%M:%S}]：{label}。目標 {_net_position_text(desired_net_position)}，"
                f"原本 {_net_position_text(current_net_position)}，丟空單 {sell_quantity} 口"
            )

        print("送單完成")
        return True
    except Exception as exc:
        message = f"[{test_now:%H:%M:%S}]：{label}。送單錯誤：{exc}"
        print(message)
        send_discord_message(message)
        return False
    finally:
        if api is not None:
            try:
                api.logout()
            except Exception:
                pass
