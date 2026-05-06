"""Compatibility wrapper for TT/MXF strategies.

Prefer importing `strategy_tt_mxf_live` or `strategy_tt_mxf_draft` directly.
"""

from __future__ import annotations

from strategy_tt_mxf_draft import apply_tt_mxf_draft_strategy
from strategy_tt_mxf_live import apply_tt_mxf_live_strategy


def apply_tt_mxf_strategy() -> bool:
    """Backward-compatible alias for the live TT/MXF strategy."""
    return apply_tt_mxf_live_strategy()

