"""Import the established H3 signal, ledger, and Shioaji adapters."""
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
for directory in (BACKEND / 'h3-common', BACKEND, BACKEND / 'h3-follow-add-strategy'):
    sys.path.insert(0, str(directory))
