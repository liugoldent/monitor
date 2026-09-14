"""API_KEY2 adapter using the same TMFR1/IOC reconciliation as account 1."""
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
        expected = os.getenv("EF_HEDGE_ACCOUNT_ID", "").strip()
        if expected and str(api.futopt_account.account_id) != expected:
            raise BrokerOrderError("API_KEY2 期貨帳號與 EF_HEDGE_ACCOUNT_ID 不符")
        return api
    except Exception:
        api.logout()
        raise


def execute_target_position(target: int, *, deadline: datetime, clock: Callable[[], datetime],
                            api: Any = None, sj: Any = None):
    if isinstance(target, bool) or not isinstance(target, int) or abs(target) > 240:
        raise ValueError("純 EF 目標須為 -240 至 240 整數")
    if sj is None:
        import shioaji as sj
    owned = api is None
    if owned:
        api = login(sj)
    try:
        contract = _shared._contract(api)
        # TMFR1 resolves to the broker's current near-month physical contract.
        contract_code = contract.code
        _shared._refresh_status(api)
        for trade in api.list_trades():
            code = _shared._position_code(_shared._position_value(trade, "contract"))
            if code.startswith("TMF") and _shared._status_text(trade).lower() not in {
                "filled", "cancelled", "failed", "inactive",
            }:
                raise BrokerOrderError("API_KEY2 有未確認結束的 TMF 委託；不重複下單")
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
        if len(held_sides) > 1:
            raise BrokerOrderError("API_KEY2 同時有多空庫存，不能僅以淨額判定已平倉")
        def check_deadline():
            if clock() >= deadline:
                raise BrokerOrderError("已超過本次下單期限，不追補休市前委託")

        return _shared.execute_target_position(
            target, api=api, sj=sj, before_order=check_deadline,
        )
    finally:
        if owned:
            try:
                api.logout()
            except Exception:
                pass
