"""Compatibility import; all H direction rules live in backend h_signal.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from h_signal import parse_h_direction as parse_signal
