"""Process-long Shioaji adapter for EF Hysteresis + 01:00 morning flat.

Each call reads the current TMF inventory, calculates one target delta and
submits that delta once through one broker session shared by the monitor.
Previous orders are deliberately not scanned or replayed here; the next EF
event always starts from the broker inventory then visible at that time.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from threading import Lock
from typing import Any
from datetime import datetime


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
from ef_trade_runtime import now_local, order_deadline, check_order_deadline
SHARED_ADAPTER_PATH = BACKEND_DIR / "shioaji_tmf_target.py"
POSITION_UNIT_ENV = "EF_HYSTERESIS_MORNING_FLAT_POSITION_UNIT"
LEGACY_POSITION_UNIT_ENV = "EF_STRONG_MORNING_FLAT_POSITION_UNIT"
MAX_POSITION_UNIT = 20


def _load_shared_adapter():
    module_name = "_shioaji_tmf_target_shared_for_ef_hysteresis_morning_flat"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, SHARED_ADAPTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"無法載入共用下單模組: {SHARED_ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_shared = _load_shared_adapter()
BrokerOrderError = _shared.BrokerOrderError
OrderResult = _shared.OrderResult
current_tmf_position = _shared.current_tmf_position

# pysolace owns native resources whose repeated construction/destruction can
# terminate the interpreter outside Python's exception handling.  Retain one
# Shioaji object for the entire monitor process instead of logging in and out
# for every signal and the 01:00 flat.
_broker_api: Any = None
_broker_sj: Any = None
_broker_lock = Lock()
_failed_apis: list[Any] = []


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必要環境變數: {name}")
    return value


def _position_unit() -> int:
    try:
        unit = int(os.getenv(POSITION_UNIT_ENV) or os.getenv(LEGACY_POSITION_UNIT_ENV, "1"))
    except ValueError as exc:
        raise ValueError(f"{POSITION_UNIT_ENV}必須是1到{MAX_POSITION_UNIT}的整數") from exc
    if not 1 <= unit <= MAX_POSITION_UNIT:
        raise ValueError(f"{POSITION_UNIT_ENV}必須是1到{MAX_POSITION_UNIT}的整數")
    return unit


def broker_error_summary(exc: BaseException) -> str:
    """Return an operator-safe error without echoing broker credentials."""
    message = str(exc).lower()
    if "expired" in message:
        return "API 憑證已過期"
    if isinstance(exc, (ValueError, FileNotFoundError, BrokerOrderError)):
        return str(exc)[:300]
    return type(exc).__name__


def _login(sj: Any) -> Any:
    ca_path = Path(os.getenv("CA_PATH") or BACKEND_DIR / "Sinopac.pfx")
    if not ca_path.is_file():
        raise FileNotFoundError(f"找不到永豐憑證檔: {ca_path}")

    api = sj.Shioaji(simulation=False)
    try:
        api.login(_required_env("API_KEY"), _required_env("SECRET_KEY"))
        person_id = _required_env("PERSON_ID")
        api.activate_ca(
            ca_path=str(ca_path),
            ca_passwd=person_id,
            person_id=person_id,
        )
        return api
    except Exception:
        # Do not destroy or logout a partially connected pysolace object in the
        # running monitor.  Its native destructor has previously exited the
        # process with SIGSEGV; keep it referenced until process termination.
        _failed_apis.append(api)
        raise


def initialize_broker_session(*, sj: Any = None) -> Any:
    """Log in once and retain the native Shioaji session until process exit."""
    global _broker_api, _broker_sj
    with _broker_lock:
        if _broker_api is not None:
            return _broker_api
        if sj is None:
            try:
                import shioaji as sj
            except ImportError as exc:
                raise RuntimeError("尚未安裝 shioaji，無法執行實單") from exc
        api = _login(sj)
        _broker_api, _broker_sj = api, sj
        print("Shioaji 長效連線已建立；強共識訊號與01:00清倉共用此連線", flush=True)
        return api


def execute_target_position(
    target_position: int,
    *,
    api: Any = None,
    sj: Any = None,
    deadline: datetime | None = None,
    clock=now_local,
    on_prepared=None,
    on_submitted=None,
) -> OrderResult:
    """Read API_KEY inventory and submit its difference from the final target once."""
    unit = _position_unit()
    if target_position not in {-unit, 0, unit} or isinstance(target_position, bool):
        raise ValueError(
            f"Hysteresis實單目標只能是-{unit}、0或{unit}口，目前為{target_position!r}"
        )
    deadline = deadline or order_deadline(clock(), target_position)

    def submit(api):
        check_order_deadline(deadline, clock, BrokerOrderError)
        previous = current_tmf_position(api)
        delta = target_position - previous
        side = "buy" if delta > 0 else "sell" if delta < 0 else None
        quantity = abs(delta)
        contract = _shared._contract(api)
        prepared = {
            "broker_before_position": previous,
            "broker_contract": str(contract.code),
            "broker_side": side,
            "broker_quantity": quantity,
            "target_position": target_position,
            "broker_request_at": clock().isoformat(),
        }
        if on_prepared is not None:
            on_prepared(prepared)
        if delta == 0:
            result = OrderResult(previous, target_position, previous, None, 0)
            if on_submitted is not None:
                on_submitted(result)
            return result

        order = _shared._build_order(api, sj, side, quantity)
        check_order_deadline(deadline, clock, BrokerOrderError)
        print(f"委託內容 TMFR1 {side} {quantity}口 MKT IOC Auto", flush=True)
        trade = api.place_order(contract, order, timeout=0)
        if trade is None:
            raise BrokerOrderError("送單未取得回傳")
        status = _shared._status_text(trade)
        print(f"委託回傳狀態：{status}（非成交確認）", flush=True)
        result = OrderResult(
            previous, target_position, None, side, quantity, trade, confirmed=False
        )
        if on_submitted is not None:
            on_submitted(result)
        if status.lower() in {"failed", "inactive"}:
            message = _shared._status_message(trade) or "券商未接受委託"
            raise BrokerOrderError(f"永豐委託失敗（{status}）：{message}")
        return result
    if api is not None:
        return submit(api)
    api = initialize_broker_session(sj=sj)
    return submit(api)
