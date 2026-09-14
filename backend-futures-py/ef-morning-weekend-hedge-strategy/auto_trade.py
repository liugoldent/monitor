"""Dedicated API_KEY2 executor, with an explicit physical TMF contract."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

BACKEND_DIR = Path(__file__).resolve().parent.parent
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
        expected = required("EF_HEDGE_ACCOUNT_ID")
        if str(api.futopt_account.account_id) != expected:
            raise BrokerOrderError("API_KEY2 期貨帳號與 EF_HEDGE_ACCOUNT_ID 不符")
        return api
    except Exception:
        api.logout()
        raise


def execute_target_position(target: int, *, contract_code: str,
                            deadline: datetime, clock: Callable[[], datetime],
                            api: Any = None, sj: Any = None):
    if isinstance(target, bool) or not isinstance(target, int) or abs(target) > 240:
        raise ValueError("避險目標須為 -240 至 240 整數")
    if not contract_code.startswith("TMF") or contract_code in {"TMFR1", "TMFR2"}:
        raise ValueError("須指定實際月份的 TMF 合約代碼，不能使用連續月代碼")
    if sj is None:
        import shioaji as sj
    owned = api is None
    if owned:
        api = login(sj)
    try:
        contract = api.Contracts.Futures.TMF[contract_code]
        if contract is None or contract.code != contract_code:
            raise BrokerOrderError("指定合約與券商合約代碼不符")
        _shared._refresh_status(api)
        for trade in api.list_trades():
            code = _shared._position_code(_shared._position_value(trade, "contract"))
            if code.startswith("TMF") and _shared._status_text(trade).lower() not in {
                "filled", "cancelled", "failed", "inactive",
            }:
                raise BrokerOrderError("API_KEY2 有未確認結束的 TMF 委託；不重複下單")
        previous = 0
        held_sides = set()
        for position in api.list_positions(api.futopt_account) or []:
            code = _shared._position_code(position)
            quantity = _shared._position_quantity(position)
            if not code.startswith("TMF") or not quantity:
                continue
            if code != contract_code:
                raise BrokerOrderError("API_KEY2 存在其他月份 TMF；請先確認部位")
            side = _shared._position_side(position)
            held_sides.add(side)
            previous += side * quantity
        if len(held_sides) > 1:
            raise BrokerOrderError("API_KEY2 同時有多空庫存，不能僅以淨額判定已平倉")
        delta = target - previous
        if not delta:
            return _shared.OrderResult(previous, target, previous, None, 0)
        # Login and status queries may consume the whole last minute of trading.
        if clock() >= deadline:
            raise BrokerOrderError("已超過本次下單期限，不追補休市前委託")
        side = "buy" if delta > 0 else "sell"
        order = _shared._build_order(api, sj, side, abs(delta))
        trade = api.place_order(contract, order, timeout=_shared.ORDER_TIMEOUT_MS)
        _shared._refresh_status(api, trade=trade)
        if _shared._status_text(trade).lower() in {"failed", "inactive"}:
            raise BrokerOrderError("券商未接受避險委託")
        actual = _shared._verify_target_position(api, target, trade)
        return _shared.OrderResult(previous, target, actual, side, abs(delta), trade)
    finally:
        if owned:
            api.logout()
