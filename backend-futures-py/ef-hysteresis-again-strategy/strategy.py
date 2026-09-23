"""Pure decision state for the EF Hysteresis Again shadow strategy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Decision:
    target: int
    e_net: int
    f_net: int
    long_locked: bool
    short_locked: bool
    reason: str


def decide(
    positions: Mapping[str, int],
    e_codes: tuple[str, ...],
    f_codes: tuple[str, ...],
    current: int,
    long_locked: bool,
    short_locked: bool = False,
    *,
    entry_threshold: int = 2,
    hold_threshold: int = 1,
) -> Decision:
    """Apply fixed 2/1 hysteresis and symmetric re-cross locks."""
    e_net = sum(int(positions.get(code, 0)) for code in e_codes)
    f_net = sum(int(positions.get(code, 0)) for code in f_codes)
    bull = e_net >= entry_threshold and f_net >= entry_threshold
    bear = e_net <= -entry_threshold and f_net <= -entry_threshold

    if long_locked and not bull:
        long_locked = False
    if short_locked and not bear:
        short_locked = False

    if bear:
        if short_locked and current >= 0:
            return Decision(0, e_net, f_net, long_locked, True,
                            "早盤前空方共識已形成；等待先升破-2/-2再重新跌破")
        return Decision(-1, e_net, f_net, long_locked, short_locked,
                        "空方由未達門檻重新達到-2/-2")
    if bull:
        if long_locked and current <= 0:
            return Decision(0, e_net, f_net, True, short_locked,
                            "早盤前多方共識已形成；等待先跌破+2/+2再重新突破")
        return Decision(1, e_net, f_net, long_locked, short_locked,
                        "多方由未達門檻重新達到+2/+2")
    if current > 0 and e_net >= hold_threshold and f_net >= hold_threshold:
        return Decision(1, e_net, f_net, long_locked, short_locked,
                        "多單續抱：E/F皆至少+1")
    if current < 0 and e_net <= -hold_threshold and f_net <= -hold_threshold:
        return Decision(-1, e_net, f_net, long_locked, short_locked,
                        "空單續抱：E/F皆至多-1")
    return Decision(0, e_net, f_net, long_locked, short_locked, "共識退出")


def should_lock_long(positions: Mapping[str, int], e_codes: tuple[str, ...],
                     f_codes: tuple[str, ...]) -> bool:
    return (sum(int(positions.get(code, 0)) for code in e_codes) >= 2
            and sum(int(positions.get(code, 0)) for code in f_codes) >= 2)


def should_lock_short(positions: Mapping[str, int], e_codes: tuple[str, ...],
                      f_codes: tuple[str, ...]) -> bool:
    return (sum(int(positions.get(code, 0)) for code in e_codes) <= -2
            and sum(int(positions.get(code, 0)) for code in f_codes) <= -2)
