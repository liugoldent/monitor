"""Pure EF two-group consensus target with exit hysteresis."""
from __future__ import annotations

from typing import Mapping

from strategy import (
    ConsensusDecision,
    PORTFOLIO_E,
    PORTFOLIO_F,
    PriceBar,
    SignalEvent,
    normalized_positions,
    signal_is_in_morning_block,
    validate_threshold,
)


def hysteresis_target(
    positions: Mapping[str, int],
    current_position: int,
    *,
    entry_threshold: int = 2,
    hold_threshold: int = 1,
) -> tuple[int, int, int, str]:
    """Return target, E net, F net and reason.

    Enter only when both groups reach ``entry_threshold`` in the same direction.
    Once in a position, keep it while both groups retain ``hold_threshold`` in
    that direction. Opposite entry consensus reverses immediately.
    """
    entry_threshold = validate_threshold(entry_threshold)
    if isinstance(hold_threshold, bool) or not isinstance(hold_threshold, int):
        raise ValueError("續抱門檻必須是整數")
    if not 0 <= hold_threshold <= entry_threshold:
        raise ValueError("續抱門檻必須介於0與進場門檻")
    if current_position not in {-1, 0, 1}:
        raise ValueError("目前部位必須是-1、0或1")

    values = normalized_positions(positions)
    e_net = sum(values[code] for code in PORTFOLIO_E)
    f_net = sum(values[code] for code in PORTFOLIO_F)
    if e_net >= entry_threshold and f_net >= entry_threshold:
        return 1, e_net, f_net, "bull_entry_consensus"
    if e_net <= -entry_threshold and f_net <= -entry_threshold:
        return -1, e_net, f_net, "bear_entry_consensus"
    if current_position > 0 and e_net >= hold_threshold and f_net >= hold_threshold:
        return 1, e_net, f_net, "hold_long"
    if current_position < 0 and e_net <= -hold_threshold and f_net <= -hold_threshold:
        return -1, e_net, f_net, "hold_short"
    return 0, e_net, f_net, "consensus_lost"


def evaluate_hysteresis_event(
    positions: dict[str, int],
    current_position: int,
    event: SignalEvent,
    execution_bar: PriceBar,
    *,
    entry_threshold: int = 2,
    hold_threshold: int = 1,
) -> ConsensusDecision:
    """Apply one EF event and produce the live/shadow trading decision."""
    positions[event.strategy_code] = event.new_position
    target, e_net, f_net, relation = hysteresis_target(
        positions,
        current_position,
        entry_threshold=entry_threshold,
        hold_threshold=hold_threshold,
    )
    if signal_is_in_morning_block(event.timestamp, execution_bar.bar_time):
        target = 0
        relation = "morning_block"
        reason = "01:00～08:45為早晨風控區間，不建立Hysteresis組合部位"
    elif relation == "bull_entry_consensus":
        reason = f"E淨部位{e_net}、F淨部位{f_net}，兩組皆達多方進場門檻{entry_threshold}"
    elif relation == "bear_entry_consensus":
        reason = f"E淨部位{e_net}、F淨部位{f_net}，兩組皆達空方進場門檻-{entry_threshold}"
    elif relation == "hold_long":
        reason = f"多單續抱：E淨部位{e_net}、F淨部位{f_net}，兩組仍達續抱門檻{hold_threshold}"
    elif relation == "hold_short":
        reason = f"空單續抱：E淨部位{e_net}、F淨部位{f_net}，兩組仍達續抱門檻-{hold_threshold}"
    else:
        reason = (
            f"共識退出：E淨部位{e_net}、F淨部位{f_net}，"
            f"未同時保留續抱門檻{hold_threshold}"
        )
    values = normalized_positions(positions)
    return ConsensusDecision(
        event=event,
        execution_time=execution_bar.bar_time,
        execution_price=execution_bar.open,
        e_net=e_net,
        f_net=f_net,
        previous_position=current_position,
        target_position=target,
        threshold=entry_threshold,
        relation=relation,
        reason=reason,
        e_positions=tuple((code, values[code]) for code in PORTFOLIO_E),
        f_positions=tuple((code, values[code]) for code in PORTFOLIO_F),
    )
