"""Shioaji target-position adapter for the opening-range retest strategy.

The monitor is shadow-only unless OPENING_RETEST_ENABLE_ORDERS=true.  This
adapter deliberately reuses the already verified H3 TMF reconciliation code.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
SHARED_ADAPTER_PATH = BACKEND_DIR / "h3-ef-012-strategy" / "auto_trade.py"
POSITION_UNIT_ENV = "OPENING_RETEST_POSITION_UNIT"
MAX_POSITION_UNIT = 20


def _load_shared_adapter():
    module_name = "_h3_ef_012_auto_trade_shared_for_opening_retest"
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


def _strategy_credential(primary: str, fallback: str) -> str:
    return os.getenv(primary, "").strip() or _required_env(fallback)


def _position_unit() -> int:
    try:
        unit = int(os.getenv(POSITION_UNIT_ENV, "1"))
    except ValueError as exc:
        raise ValueError(f"{POSITION_UNIT_ENV}必須是1到{MAX_POSITION_UNIT}的整數") from exc
    if not 1 <= unit <= MAX_POSITION_UNIT:
        raise ValueError(f"{POSITION_UNIT_ENV}必須是1到{MAX_POSITION_UNIT}的整數")
    return unit


def _login(sj: Any) -> Any:
    ca_path = Path(os.getenv("CA_PATH") or BACKEND_DIR / "Sinopac.pfx")
    if not ca_path.is_file():
        raise FileNotFoundError(f"找不到永豐憑證檔: {ca_path}")
    api = sj.Shioaji(simulation=False)
    api.login(
        _strategy_credential("OPENING_RETEST_API_KEY", "API_KEY2"),
        _strategy_credential("OPENING_RETEST_SECRET_KEY", "SECRET_KEY2"),
    )
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
) -> OrderResult:
    unit = _position_unit()
    if target_position not in {-unit, 0, unit} or isinstance(target_position, bool):
        raise ValueError(
            f"開盤突破回踩實單目標只能是-{unit}、0或{unit}口，目前為{target_position!r}"
        )
    if api is not None:
        return _shared.execute_target_position(target_position, api=api, sj=sj)
    if sj is None:
        try:
            import shioaji as sj  # type: ignore[no-redef]
        except ImportError as exc:
            raise RuntimeError("尚未安裝 shioaji，無法執行實單") from exc
    api = _login(sj)
    try:
        return _shared.execute_target_position(target_position, api=api, sj=sj)
    finally:
        try:
            api.logout()
        except Exception:
            pass

