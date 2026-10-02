"""API_KEY live TMF execution, reusing the account-one adapter."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "ef-strong-consensus-morning-flat-strategy/auto_trade.py"
SPEC = importlib.util.spec_from_file_location("_again_account_one_adapter", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"無法載入 API_KEY 下單模組: {SOURCE}")
_adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = _adapter
SPEC.loader.exec_module(_adapter)

# The imported adapter owns the actual Shioaji constructor. This strategy uses
# the production account; the legacy adapter remains in simulation mode.
_adapter.BROKER_SIMULATION = False
BROKER_SIMULATION = False
BrokerOrderError = _adapter.BrokerOrderError
broker_error_summary = _adapter.broker_error_summary
check_startup_broker = _adapter.check_startup_broker
execute_target_position = _adapter.execute_target_position
initialize_broker_session = _adapter.initialize_broker_session
log_attribute_error = _adapter.log_attribute_error
position_unit = _adapter._position_unit
