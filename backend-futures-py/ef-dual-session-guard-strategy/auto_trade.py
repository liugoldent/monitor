"""Verified Shioaji adapter for the EF dual-session guard strategy.

Live mode deliberately requires its own API credential names so this strategy
cannot accidentally reconcile the H3, strong-consensus, or RSI account.
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
MAX_ABS_TARGET = 10


def _load_shared_adapter():
    module_name = "_h3_ef_012_auto_trade_shared_for_dual_session_guard"
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


def _login(sj: Any) -> Any:
    ca_path = Path(
        os.getenv("EF_DUAL_SESSION_CA_PATH")
        or os.getenv("CA_PATH")
        or BACKEND_DIR / "Sinopac.pfx"
    )
    if not ca_path.is_file():
        raise FileNotFoundError(f"找不到永豐憑證檔: {ca_path}")

    api = sj.Shioaji(simulation=False)
    api.login(
        _required_env("EF_DUAL_SESSION_API_KEY"),
        _required_env("EF_DUAL_SESSION_SECRET_KEY"),
    )
    person_id = (
        os.getenv("EF_DUAL_SESSION_PERSON_ID", "").strip()
        or _required_env("PERSON_ID")
    )
    api.activate_ca(
        ca_path=str(ca_path),
        ca_passwd=person_id,
        person_id=person_id,
    )
    return api


def validate_target(target_position: object) -> int:
    if isinstance(target_position, bool) or not isinstance(target_position, int):
        raise ValueError("EF雙時段策略目標口數必須是整數")
    if abs(target_position) > MAX_ABS_TARGET:
        raise ValueError(
            f"EF雙時段策略目標不得超過正負{MAX_ABS_TARGET}口，目前為{target_position}"
        )
    return target_position


def execute_target_position(
    target_position: int,
    *,
    api: Any = None,
    sj: Any = None,
) -> OrderResult:
    """Reconcile the dedicated account to the complete filtered EF net target."""
    target = validate_target(target_position)
    if api is not None:
        return _shared.execute_target_position(target, api=api, sj=sj)
    if sj is None:
        try:
            import shioaji as sj  # type: ignore[no-redef]
        except ImportError as exc:
            raise RuntimeError("尚未安裝 shioaji，無法執行實單") from exc

    api = _login(sj)
    try:
        return _shared.execute_target_position(target, api=api, sj=sj)
    finally:
        try:
            api.logout()
        except Exception:
            pass
