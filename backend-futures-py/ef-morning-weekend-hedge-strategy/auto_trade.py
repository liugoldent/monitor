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


def execute_target_position(target: int | None, *, deadline: datetime,
                            clock: Callable[[], datetime], api: Any = None,
                            sj: Any = None, delta: int | None = None):
    """Submit once. Signals use their delta directly; flat queries inventory once."""
    if delta is not None:
        if isinstance(delta, bool) or not isinstance(delta, int) or not 1 <= abs(delta) <= 40:
            raise ValueError("訊號差額須為非零整數，最多 40 口")
    elif target != 0:
        raise ValueError("僅接受新訊號差額或清倉目標 0")
    if sj is None:
        import shioaji as sj
    owned = api is None
    if owned:
        api = login(sj)
    try:
        # New signal: no inventory reconciliation or old-target catch-up.
        if delta is None:
            _shared.validate_tmf_account(api)
            delta = -_shared.current_tmf_position(api)
        from types import SimpleNamespace
        if delta == 0:
            return SimpleNamespace(side=None, quantity=0, submitted=False)
        side, quantity = ("buy" if delta > 0 else "sell"), abs(delta)
        order = _shared._build_order(api, sj, side, quantity)
        contract = _shared._contract(api)
        check_order_deadline(deadline, clock, BrokerOrderError)
        trade = api.place_order(contract, order, timeout=_shared.ORDER_TIMEOUT_MS)
        if trade is None:
            raise BrokerOrderError("送單未取得回傳")
        if _shared._status_text(trade).lower() in {"failed", "inactive"}:
            raise BrokerOrderError("券商即時回覆拒絕委託")
        # A returned order is submission acknowledgement, never proof of fill.
        return SimpleNamespace(side=side, quantity=quantity, submitted=True, trade=trade)
    finally:
        if owned:
            try:
                api.logout()
            except Exception:
                pass
