"""API_KEY2 TMFR1/IOC submission through one process-long broker session."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path
from threading import Lock
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

# pysolace owns native resources whose repeated construction/destruction can
# segfault the interpreter.  Keep one Shioaji object for the entire monitor
# process instead of logging in and out for every signal.
_broker_api: Any = None
_broker_sj: Any = None
_broker_lock = Lock()
_failed_apis: list[Any] = []


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"缺少 {name}")
    return value


def broker_error_summary(exc: BaseException) -> str:
    """Return an operator-safe error without echoing broker tokens/person IDs."""
    message = str(exc).lower()
    if "expired" in message:
        return "API 憑證已過期"
    if isinstance(exc, (ValueError, FileNotFoundError, BrokerOrderError)):
        return str(exc)[:300]
    return type(exc).__name__


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
        # Do not invoke pysolace cleanup on a partially connected object.  Keep
        # it referenced until process exit so __del__ cannot run mid-monitor.
        _failed_apis.append(api)
        raise


def initialize_broker_session(*, sj: Any = None):
    """Log in once and retain the native Shioaji session until process exit."""
    global _broker_api, _broker_sj
    with _broker_lock:
        if _broker_api is not None:
            return _broker_api
        if sj is None:
            import shioaji as sj
        api = login(sj)
        _broker_api, _broker_sj = api, sj
        print("Shioaji 長效連線已建立；本程序後續訊號共用此連線", flush=True)
        return api


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
    if api is None:
        api = initialize_broker_session(sj=sj)
    _shared._refresh_status(api)
    _shared.validate_tmf_account(api)
    positions = api.list_positions(api.futopt_account)
    if positions is None:
        raise BrokerOrderError("庫存查詢未回傳資料")
    return not any(_shared._position_code(p).startswith("TMF")
                   and _shared._position_quantity(p) for p in positions)


def execute_target_position(target: int, *, deadline: datetime,
                            clock: Callable[[], datetime], api: Any = None,
                            sj: Any = None, on_prepared=None, on_submitted=None):
    """Read current TMF inventory and submit the difference to one final target."""
    if isinstance(target, bool) or not isinstance(target, int):
        raise ValueError(f"最終目標口數必須是整數，目前為 {target!r}")
    owned = api is None
    if owned:
        api = initialize_broker_session(sj=sj)
        sj = _broker_sj
    elif sj is None:
        import shioaji as sj
    from types import SimpleNamespace
    try:
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
        # trade = api.place_order(contract, order, timeout=0)  # 實單停用
        trade = None
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
    except Exception as exc:
        print(f"券商操作失敗：{broker_error_summary(exc)}", file=sys.stderr, flush=True)
        raise
