"""One-shot Shioaji adapter for EF Hysteresis + 01:00 morning flat.

The order reconciliation implementation is shared by active TMF strategies.
This strategy uses the primary API credential pair selected by the operator.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
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


def _login(sj: Any) -> Any:
    ca_path = Path(os.getenv("CA_PATH") or BACKEND_DIR / "Sinopac.pfx")
    if not ca_path.is_file():
        raise FileNotFoundError(f"找不到永豐憑證檔: {ca_path}")

    api = sj.Shioaji(simulation=True)
    api.login(_required_env("API_KEY"), _required_env("SECRET_KEY"))
    person_id = _required_env("PERSON_ID")
    api.activate_ca(
        ca_path=str(ca_path),
        ca_passwd=person_id,
        person_id=person_id,
    )
    return api


def execute_target_position(
    target_position: int,
    *,
    api: Any = None,
    sj: Any = None,
    deadline: datetime | None = None,
    clock=now_local,
    guard: dict | None = None,
    persist_guard=None,
    on_submitted=None,
) -> OrderResult:
    """Read API_KEY inventory, submit the target delta once, checkpoint before logout."""
    unit = _position_unit()
    if target_position not in {-unit, 0, unit} or isinstance(target_position, bool):
        raise ValueError(
            f"Hysteresis實單目標只能是-{unit}、0或{unit}口，目前為{target_position!r}"
        )
    deadline = deadline or order_deadline(clock(), target_position)
    def check_deadline():
        check_order_deadline(deadline, clock, BrokerOrderError)
    tracking = {"submission_only": True}
    if guard is not None:
        tracking.update(guard=guard, persist_guard=persist_guard)
    def submit(api):
        result = _shared.execute_target_position(target_position, api=api, sj=sj,
                                                before_order=check_deadline, strict_tmf=True, **tracking)
        if on_submitted is not None:
            on_submitted(result)
        return result
    if api is not None:
        return submit(api)
    if sj is None:
        try:
            import shioaji as sj  # type: ignore[no-redef]
        except ImportError as exc:
            raise RuntimeError("尚未安裝 shioaji，無法執行實單") from exc

    api = _login(sj)
    try:
        return submit(api)
    finally:
        try:
            print("登出開始", flush=True)
            api.logout()
            print("登出完成", flush=True)
        except Exception:
            pass
