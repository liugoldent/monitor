"""API_KEY2 one-shot TMFR1/IOC submission; no fill verification or retry."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from ef_trade_runtime import check_order_deadline
_name = "_tmf_shared_for_ef_closure_hedge"
_spec = importlib.util.spec_from_file_location(_name, BACKEND_DIR / "shioaji_tmf_target.py")
_shared = importlib.util.module_from_spec(_spec)
sys.modules[_name] = _shared
_spec.loader.exec_module(_shared)
BrokerOrderError = _shared.BrokerOrderError


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"缺少 {name}")
    return value


def login(sj: Any):
    # Deliberately never fall back to API_KEY / SECRET_KEY.
    key, secret = required("API_KEY2"), required("SECRET_KEY2")
    person = os.getenv("PERSON_ID2") or required("PERSON_ID")
    ca = Path(os.getenv("CA_PATH2") or os.getenv("CA_PATH") or BACKEND_DIR / "Sinopac.pfx")
    if not ca.is_file():
        raise FileNotFoundError(f"找不到憑證 {ca}")
    api = sj.Shioaji(simulation=False)
    try:
        api.login(key, secret)
        api.activate_ca(ca_path=str(ca), ca_passwd=person, person_id=person)
        expected = os.getenv("EF_HEDGE_ACCOUNT_ID", "").strip()
        if expected and str(api.futopt_account.account_id) != expected:
            raise BrokerOrderError("API_KEY2 期貨帳號與 EF_HEDGE_ACCOUNT_ID 不符")
        return api
    except Exception:
        api.logout()
        raise


def _deal_quantity(trade: Any) -> int | None:
    value = _shared._position_value(_shared._position_value(trade, "status"), "deal_quantity")
    if isinstance(value, bool):
        return None
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None
    return quantity if quantity >= 0 else None


def confirm_flat(*, api: Any = None, sj: Any = None) -> bool:
    """Read-only reset guard: unknown inventory must never count as flat."""
    owned = api is None
    if owned:
        if sj is None:
            import shioaji as sj
        api = login(sj)
    try:
        _shared._refresh_status(api)
        _shared.validate_tmf_account(api)
        positions = api.list_positions(api.futopt_account)
        if positions is None:
            raise BrokerOrderError("庫存查詢未回傳資料")
        return not any(_shared._position_code(p).startswith("TMF")
                       and _shared._position_quantity(p) for p in positions)
    finally:
        if owned:
            api.logout()


def execute_target_position(target: int, *, deadline: datetime,
                            clock: Callable[[], datetime], api: Any = None,
                            sj: Any = None, on_prepared=None, on_submitted=None):
    """Read current TMF inventory and submit the difference to one final target."""
    if isinstance(target, bool) or not isinstance(target, int):
        raise ValueError(f"最終目標口數必須是整數，目前為 {target!r}")
    if sj is None:
        import shioaji as sj
    owned = api is None
    if owned:
        api = login(sj)
    try:
        from types import SimpleNamespace
        check_order_deadline(deadline, clock, BrokerOrderError)
        before_position = _shared.current_tmf_position(api)
        delta = target - before_position
        if abs(delta) > 40:
            raise BrokerOrderError(
                f"券商庫存 {before_position} 口到目標 {target} 口需下 {abs(delta)} 口，超過單次上限 40 口"
            )
        side = "buy" if delta > 0 else "sell" if delta < 0 else None
        prepared = dict(broker_before_position=before_position,
                        broker_contract=str(_shared._contract(api).code), broker_side=side,
                        broker_quantity=abs(delta), target_position=target,
                        broker_request_at=clock().isoformat())
        if on_prepared is not None:
            on_prepared(prepared)
        if delta == 0:
            result = SimpleNamespace(
                side=None, quantity=0, submitted=False,
                previous_position=before_position, target_position=target,
                broker_before_position=before_position, broker_trade_id="",
                broker_status="NoOrderNeeded", broker_deal_quantity=0,
            )
            if on_submitted is not None:
                on_submitted(result)
            return result
        quantity = abs(delta)
        order = _shared._build_order(api, sj, side, quantity)
        contract = _shared._contract(api)
        check_order_deadline(deadline, clock, BrokerOrderError)
        print(f"委託內容 TMFR1 {side} {quantity}口 MKT IOC Auto", flush=True)
        trade = api.place_order(contract, order, timeout=0)
        print(f"委託回傳狀態：{_shared._status_text(trade)}（非成交確認）", flush=True)
        if trade is None:
            raise BrokerOrderError("送單未取得回傳")
        # A returned order is submission acknowledgement, never proof of fill.
        broker_status = _shared._status_text(trade)
        result = SimpleNamespace(
            side=side, quantity=quantity, submitted=True, trade=trade,
            previous_position=before_position, target_position=target,
            broker_before_position=before_position,
            broker_trade_id=_shared._trade_id(trade), broker_status=broker_status,
            broker_deal_quantity=_deal_quantity(trade),
        )
        # Persist the API return BEFORE SDK cleanup can crash or hang.
        if on_submitted is not None:
            on_submitted(result)
        if broker_status.lower() in {"failed", "inactive"}:
            raise BrokerOrderError("券商即時回覆拒絕委託")
        return result
    finally:
        if owned:
            try:
                print("登出開始", flush=True)
                api.logout()
                print("登出完成", flush=True)
            except Exception:
                pass
