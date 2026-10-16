"""Reuse the Capital executor's signal, state and watcher without a second rule set."""
import sys
from pathlib import Path

CAPITAL = Path(__file__).resolve().parents[1] / 'h3-capital-flatten-reentry-strategy'
sys.path.insert(0, str(CAPITAL))
from h_core import OrderRejected, Store, TradingError, execute_h, parse_signal
from h_monitor import watch
from local_env import load_env
