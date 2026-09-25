"""Pure rules for EF Hysteresis night-close TOTAL breakout."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Baseline:
    bull: int
    bear: int


@dataclass(frozen=True)
class Decision:
    target: int
    e_net: int
    f_net: int
    total: int
    reason: str


def capture_baseline(positions: Mapping[str, int], e_codes: tuple[str, ...],
                     f_codes: tuple[str, ...]) -> Baseline:
    total = sum(int(positions.get(code, 0)) for code in e_codes + f_codes)
    return Baseline(bull=max(total, 0), bear=max(-total, 0))


def decide(
    positions: Mapping[str, int],
    e_codes: tuple[str, ...],
    f_codes: tuple[str, ...],
    current: int,
    baseline: Baseline,
    *,
    entry_threshold: int = 2,
    hold_threshold: int = 1,
) -> Decision:
    e_net = sum(int(positions.get(code, 0)) for code in e_codes)
    f_net = sum(int(positions.get(code, 0)) for code in f_codes)
    total = e_net + f_net
    bull = e_net >= entry_threshold and f_net >= entry_threshold
    bear = e_net <= -entry_threshold and f_net <= -entry_threshold

    if bull:
        if current > 0:
            return Decision(1, e_net, f_net, total, "多單續抱；E/F仍達進場共識")
        if total > baseline.bull:
            return Decision(1, e_net, f_net, total, "多方TOTAL突破05:00前基準")
        return Decision(0, e_net, f_net, total, "多方共識存在，但TOTAL尚未突破05:00前基準")
    if bear:
        if current < 0:
            return Decision(-1, e_net, f_net, total, "空單續抱；E/F仍達進場共識")
        if -total > baseline.bear:
            return Decision(-1, e_net, f_net, total, "空方TOTAL突破05:00前基準")
        return Decision(0, e_net, f_net, total, "空方共識存在，但TOTAL尚未突破05:00前基準")
    if current > 0 and e_net >= hold_threshold and f_net >= hold_threshold:
        return Decision(1, e_net, f_net, total, "多單續抱：E/F皆至少+1")
    if current < 0 and e_net <= -hold_threshold and f_net <= -hold_threshold:
        return Decision(-1, e_net, f_net, total, "空單續抱：E/F皆至多-1")
    return Decision(0, e_net, f_net, total, "共識退出")
