"""Shioaji execution adapter for the H3 + EF target-position strategy.

This module deliberately knows nothing about Telegram or strategy decisions.  Its
only public operation reconciles the account's TMF position to a requested net
position.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
ORDER_TIMEOUT_MS = 30_000
ORDER_CALLBACK_TIMEOUT_SECONDS = 5
POSITION_VERIFY_ATTEMPTS = 5
POSITION_VERIFY_DELAY_SECONDS = 0.5


class BrokerOrderError(RuntimeError):
    """The broker rejected an order or did not confirm the requested position."""


@dataclass(frozen=True)
class OrderResult:
    previous_position: int
    target_position: int
    actual_position: int
    side: str | None
    quantity: int
    trade: Any = None

    @property
    def order_sent(self) -> bool:
        return self.quantity > 0


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必要環境變數: {name}")
    return value


def _position_value(position: Any, name: str, default: Any = None) -> Any:
    if isinstance(position, dict):
        return position.get(name, default)
    return getattr(position, name, default)


def _position_quantity(position: Any) -> int:
    value = _position_value(position, "quantity", 0)
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"無法辨識永豐部位口數: {value!r}") from exc
    if quantity < 0:
        raise ValueError(f"永豐部位口數不可為負數: {quantity}")
    return quantity


def _position_side(position: Any) -> int:
    raw_direction = _position_value(position, "direction", "")
    enum_value = getattr(raw_direction, "value", raw_direction)
    direction = str(enum_value).strip().lower().rsplit(".", 1)[-1]
    if direction == "buy":
        return 1
    if direction == "sell":
        return -1
    raise ValueError(f"無法辨識永豐部位方向: {direction!r}")


def _position_code(position: Any) -> str:
    for name in ("code", "contract_code", "symbol"):
        value = _position_value(position, name)
        if value:
            return str(value).strip().upper()
    return ""


def current_tmf_position(api: Any) -> int:
    """Return signed TMF net quantity, ignoring unrelated futures positions."""
    positions = api.list_positions(api.futopt_account) or []
    total = 0
    for position in positions:
        code = _position_code(position)
        if not code.startswith("TMF"):
            continue
        total += _position_side(position) * _position_quantity(position)
    return total


def _contract(api: Any) -> Any:
    contract = api.Contracts.Futures.TMF.TMFR1
    if contract is None:
        raise RuntimeError("Shioaji 找不到微型台指近一合約 TMFR1")
    return contract


def _build_order(api: Any, sj: Any, side: str, quantity: int) -> Any:
    return api.Order(
        action=sj.constant.Action.Buy if side == "buy" else sj.constant.Action.Sell,
        price=0,
        quantity=quantity,
        price_type=sj.constant.FuturesPriceType.MKT,
        order_type=sj.constant.OrderType.IOC,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )


def _status_text(trade: Any) -> str:
    status = _position_value(_position_value(trade, "status"), "status", "")
    value = getattr(status, "value", status)
    return str(value).strip()


def _status_message(trade: Any) -> str:
    status = _position_value(trade, "status")
    return str(_position_value(status, "msg", "") or "").strip()


def _operation_value(message: Any, name: str) -> str:
    operation = _position_value(message, "operation", {})
    return str(_position_value(operation, name, "") or "").strip()


def _refresh_status(api: Any, *, trade: Any = None) -> None:
    if trade is not None:
        try:
            api.update_status(trade=trade, timeout=ORDER_TIMEOUT_MS)
            return
        except TypeError:
            pass
    try:
        api.update_status(api.futopt_account, timeout=ORDER_TIMEOUT_MS)
    except TypeError:
        api.update_status()


def _verify_target_position(api: Any, target_position: int, trade: Any) -> int:
    actual = current_tmf_position(api)
    for attempt in range(POSITION_VERIFY_ATTEMPTS):
        if actual == target_position:
            return actual
        if attempt + 1 < POSITION_VERIFY_ATTEMPTS:
            time.sleep(POSITION_VERIFY_DELAY_SECONDS)
            _refresh_status(api, trade=trade)
            actual = current_tmf_position(api)

    status = _status_text(trade) or "Unknown"
    message = _status_message(trade)
    detail = f"，訊息：{message}" if message else ""
    raise BrokerOrderError(
        f"委託後部位未達目標（狀態：{status}{detail}）："
        f"目標 {target_position}，實際 {actual}"
    )


def _login(sj: Any) -> Any:
    ca_path = Path(os.getenv("CA_PATH") or BACKEND_DIR / "Sinopac.pfx")
    if not ca_path.is_file():
        raise FileNotFoundError(f"找不到永豐憑證檔: {ca_path}")

    api = sj.Shioaji(simulation=False)
    api.login(_required_env("API_KEY"), _required_env("SECRET_KEY"))
    person_id = _required_env("PERSON_ID")
    api.activate_ca(
        ca_path=str(ca_path),
        ca_passwd=person_id,
        person_id=person_id,
    )
    return api


def execute_target_position(target_position: int, *, api: Any = None, sj: Any = None) -> OrderResult:
    """Reconcile the real TMF position to ``target_position`` with one IOC order."""
    if isinstance(target_position, bool) or not isinstance(target_position, int):
        raise ValueError(f"目標部位必須是整數，目前為 {target_position!r}")

    owns_api = api is None
    if sj is None:
        try:
            import shioaji as sj  # type: ignore[no-redef]
        except ImportError as exc:
            raise RuntimeError("尚未安裝 shioaji，無法執行實單") from exc
    if api is None:
        api = _login(sj)

    try:
        _refresh_status(api)

        previous = current_tmf_position(api)
        delta = target_position - previous
        if delta == 0:
            return OrderResult(previous, target_position, previous, None, 0)

        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        order = _build_order(api, sj, side, quantity)
        order_event = threading.Event()
        order_operation: dict[str, str] = {}

        def capture_order_event(_state: Any, message: Any) -> None:
            # Never let an exception escape into Shioaji's native callback thread.
            try:
                if _operation_value(message, "op_type").lower() != "new":
                    return
                order_operation.update(
                    op_code=_operation_value(message, "op_code"),
                    op_msg=_operation_value(message, "op_msg"),
                )
                order_event.set()
            except BaseException:
                order_operation.update(op_code="CALLBACK_ERROR", op_msg="無法解析委託回報")
                order_event.set()

        api.set_order_callback(capture_order_event)
        print("委託內容", order)
        trade = api.place_order(_contract(api), order, timeout=ORDER_TIMEOUT_MS)
        print("委託回傳內容", trade)
        order_event.wait(ORDER_CALLBACK_TIMEOUT_SECONDS)

        op_code = order_operation.get("op_code", "")
        if op_code and op_code != "00":
            op_msg = order_operation.get("op_msg") or "無錯誤說明"
            raise BrokerOrderError(f"永豐拒絕委託（{op_code}）：{op_msg}")

        _refresh_status(api, trade=trade)
        status = _status_text(trade).lower()
        if status in {"failed", "inactive"}:
            message = _status_message(trade) or "券商未接受委託"
            raise BrokerOrderError(f"永豐委託失敗（{_status_text(trade)}）：{message}")

        actual = _verify_target_position(api, target_position, trade)
        return OrderResult(previous, target_position, actual, side, quantity, trade)
    finally:
        if owns_api and api is not None:
            try:
                api.logout()
            except Exception:
                pass
