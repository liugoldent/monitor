"""API_KEY2 adapter using the same TMFR1/IOC reconciliation as account 1."""
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
        def check_deadline():
            check_order_deadline(deadline, clock, BrokerOrderError)

        return _shared.execute_target_position(
            target, api=api, sj=sj, before_order=check_deadline, strict_tmf=True,
        )
    finally:
        if owned:
            try:
                api.logout()
            except Exception:
                pass
