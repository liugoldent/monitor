"""Use the established H3 broker/feeds without shadowing their module names."""
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
for directory in (BACKEND / 'h3-capital-flatten-reentry-strategy',
                  BACKEND / 'h3-flatten-reentry-strategy', BACKEND,
                  BACKEND / 'h3-follow-add-strategy'):
    sys.path.insert(0, str(directory))
