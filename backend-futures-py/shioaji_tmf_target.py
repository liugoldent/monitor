"""Shared Shioaji execution adapter for TMF target-position strategies.

This module deliberately knows nothing about Telegram or strategy decisions.  Its
only public operation reconciles the account's TMF position to a requested net
position.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


BACKEND_DIR = Path(__file__).resolve().parent
ORDER_TIMEOUT_MS = 30_000
POSITION_VERIFY_ATTEMPTS = 20
POSITION_VERIFY_DELAY_SECONDS = 0.5


class BrokerOrderError(RuntimeError):
    """The broker rejected an order or did not confirm the requested position."""


@dataclass(frozen=True)
class OrderResult:
    previous_position: int
    target_position: int
    actual_position: int | None
    side: str | None
    quantity: int
    trade: Any = None
    confirmed: bool = True

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
    value = _position_value(position, "quantity")
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"無法辨識永豐部位口數: {value!r}") from exc
    if isinstance(value, bool) or str(value).strip() != str(quantity) or quantity < 0:
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
    positions = _read_positions(api)
    return _net_position(positions)


def _read_positions(api: Any) -> list:
    positions = api.list_positions(api.futopt_account)
    if not isinstance(positions, list):
        raise BrokerOrderError("庫存查詢未回傳有效清單，不能當成空手")
    if any(not _position_code(p) for p in positions):
        raise BrokerOrderError("庫存缺少合約代碼，無法確認 TMF 部位")
    return positions


def _net_position(positions: list) -> int:
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


def validate_tmf_account(api: Any, positions: list | None = None) -> None:
    """Reject ambiguous inventory/orders before either EF account reconciles."""
    contract_code = _contract(api).code
    trades = api.list_trades()
    if not isinstance(trades, list):
        raise BrokerOrderError("委託查詢未回傳有效清單")
    for trade in trades:
        code = _position_code(_position_value(trade, "contract"))
        if code.startswith("TMF") and _status_text(trade).lower() not in {
            "filled", "cancelled", "failed", "inactive",
        }:
            raise BrokerOrderError("有未確認結束的 TMF 委託；不重複下單")
    sides = set()
    for position in _read_positions(api) if positions is None else positions:
        code = _position_code(position)
        if not code.startswith("TMF") or not _position_quantity(position):
            continue
        if code != contract_code:
            raise BrokerOrderError("存在其他月份 TMF；請先確認部位")
        sides.add(_position_side(position))
    if len(sides) > 1:
        raise BrokerOrderError("同時有多空庫存，不能僅以淨額判定已平倉")


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
        if actual == target_position and _status_text(trade).lower() in {"filled", "cancelled"}:
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


def _trade_id(trade: Any) -> str:
    return str(_position_value(_position_value(trade, "order"), "id", "") or
               _position_value(_position_value(trade, "status"), "id", "") or "")


def _resolve_pending(api: Any, guard: dict, persist: Callable[[], None]) -> None:
    """Require terminal order evidence AND inventory reflecting its fills."""
    pending = guard.get("pending")
    if not pending:
        return
    trades = api.list_trades()
    if not isinstance(trades, list):
        raise BrokerOrderError("前筆委託查詢失敗，實際部位尚未確認")
    trade_id = pending.get("trade_id")
    matches = [t for t in trades if trade_id and _trade_id(t) == trade_id]
    if len(matches) != 1:
        raise BrokerOrderError("前筆委託結果不明，無法唯一核對委託；本筆不送單，需人工核對")
    trade = matches[0]
    _refresh_status(api, trade=trade)
    status = _status_text(trade).lower()
    if status not in {"filled", "cancelled", "failed", "inactive"}:
        raise BrokerOrderError(f"前筆委託尚未結束（{status}），本筆不送單")
    filled = _position_value(_position_value(trade, "status"), "deal_quantity")
    if isinstance(filled, bool) or not isinstance(filled, int) or not 0 <= filled <= pending["quantity"]:
        raise BrokerOrderError("前筆成交口數未確認，本筆不送單")
    expected = pending["previous_position"] + (1 if pending["side"] == "buy" else -1) * filled
    positions = _read_positions(api)
    validate_tmf_account(api, positions)
    if _net_position(positions) != expected:
        raise BrokerOrderError("前筆成交與券商庫存尚未一致，本筆不送單")
    guard["last_resolved"] = dict(pending, status=status, filled_quantity=filled,
                                  actual_position=expected)
    guard.pop("pending")
    persist()


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


def execute_target_position(target_position: int, *, api: Any = None, sj: Any = None,
                            before_order: Callable[[], None] | None = None,
                            strict_tmf: bool = False, guard: dict | None = None,
                            persist_guard: Callable[[], None] | None = None,
                            submission_only: bool = False) -> OrderResult:
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
        guard = guard if guard is not None else {}
        persist_guard = persist_guard or (lambda: None)
        _resolve_pending(api, guard, persist_guard)
        positions = _read_positions(api)
        if strict_tmf:
            validate_tmf_account(api, positions)

        previous = _net_position(positions)
        delta = target_position - previous
        if delta == 0:
            return OrderResult(previous, target_position, previous, None, 0)

        side = "buy" if delta > 0 else "sell"
        quantity = abs(delta)
        order = _build_order(api, sj, side, quantity)
        contract = _contract(api)
        if before_order is not None:
            before_order()
        guard["pending"] = dict(previous_position=previous, target_position=target_position,
                                side=side, quantity=quantity, trade_id="")
        persist_guard()  # Durable intent before a request can reach the broker.
        if before_order is not None:
            try:
                before_order()  # Persistence may have crossed the execution deadline.
            except Exception:
                guard.pop("pending")  # The broker has not been called.
                persist_guard()
                raise
        print(f"委託內容 TMFR1 {side} {quantity}口 MKT IOC Auto", flush=True)
        trade = api.place_order(contract, order, timeout=0 if submission_only else ORDER_TIMEOUT_MS)
        print(f"委託回傳狀態：{_status_text(trade)}（非成交確認）", flush=True)
        if trade is None:
            raise BrokerOrderError("送單未取得回傳")
        guard["pending"]["trade_id"] = _trade_id(trade)
        persist_guard()

        if not submission_only:
            _refresh_status(api, trade=trade)
        status = _status_text(trade).lower()
        if status in {"failed", "inactive"}:
            message = _status_message(trade) or "券商未接受委託"
            raise BrokerOrderError(f"永豐委託失敗（{_status_text(trade)}）：{message}")

        if submission_only:
            guard["last_submission"] = dict(guard.pop("pending"), status="api_returned")
            persist_guard()
            return OrderResult(previous, target_position, None, side, quantity, trade, confirmed=False)

        actual = _verify_target_position(api, target_position, trade)
        _resolve_pending(api, guard, persist_guard)
        return OrderResult(previous, target_position, actual, side, quantity, trade)
    finally:
        if owns_api and api is not None:
            try:
                api.logout()
            except Exception:
                pass
