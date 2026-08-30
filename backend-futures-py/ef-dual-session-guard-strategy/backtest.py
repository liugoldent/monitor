from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from strategy import (
    ALL_STRATEGIES,
    BoundaryEvent,
    PriceBar,
    SignalEvent,
    apply_signal,
    flatten_positions,
    load_price_bars,
    load_signal_rows,
    next_minute_open,
    normalized_positions,
    parse_position_row,
    parse_signal_row,
    parse_time,
    restore_latest_positions,
    session_boundaries,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
DEFAULT_SIGNALS = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
DEFAULT_PRICES = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"


@dataclass
class Result:
    realized: float = 0.0
    unrealized: float = 0.0
    turnover: int = 0
    max_drawdown: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    closed_pnls: list[float] = field(default_factory=list)
    ending_position: int = 0

    @property
    def total(self) -> float:
        return self.realized + self.unrealized

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf


def load_events(path: Path) -> tuple[list[SignalEvent], dict[str, int], int]:
    events: list[SignalEvent] = []
    initial = {code: 0 for code in ALL_STRATEGIES}
    untimed = 0
    for row_number, row in enumerate(load_signal_rows(path), start=1):
        event = parse_signal_row(row, row_number)
        if event is not None:
            events.append(event)
            continue
        parsed = parse_position_row(row)
        if parsed is not None and not str(row.get("received_at") or "").strip():
            initial[parsed[0]] = parsed[1]
            untimed += 1
    events.sort(key=lambda event: (event.timestamp, event.row_number))
    return events, initial, untimed


def _actions(
    events: list[SignalEvent],
    bars: list[PriceBar],
    *,
    guarded: bool,
) -> list[tuple[datetime, int, str, object]]:
    actions: list[tuple[datetime, int, str, object]] = []
    for event in events:
        execution = next_minute_open(bars, event.timestamp)
        if execution is not None:
            actions.append((execution.bar_time, 0, "signal", (event, execution)))
    if guarded:
        for boundary in session_boundaries(bars):
            actions.append((boundary.timestamp, 1, "boundary", boundary))
    return sorted(actions, key=lambda item: (item[0], item[1]))


def _apply_boundary(
    raw: dict[str, int],
    active: dict[str, int],
    boundary: BoundaryEvent,
) -> dict[str, int]:
    if boundary.kind == "night_restore":
        return restore_latest_positions(raw, active)[0]
    return flatten_positions(active)[0]


def _state_before(
    actions: list[tuple[datetime, int, str, object]],
    initial: dict[str, int],
    start: datetime,
    *,
    guarded: bool,
) -> tuple[dict[str, int], dict[str, int]]:
    raw = normalized_positions(initial)
    active = dict(raw)
    for timestamp, _, kind, payload in actions:
        if timestamp >= start:
            break
        if kind == "boundary":
            active = _apply_boundary(raw, active, payload)
            continue
        event, execution = payload
        if guarded:
            apply_signal(raw, active, event, execution)
        else:
            raw[event.strategy_code] = event.new_position
            active[event.strategy_code] = event.new_position
    return raw, active


def run(
    *,
    events: list[SignalEvent],
    initial: dict[str, int],
    bars: list[PriceBar],
    start: datetime,
    end: datetime,
    guarded: bool,
) -> Result:
    eligible = [bar for bar in bars if start <= bar.bar_time <= end]
    if not eligible:
        raise RuntimeError("回測區間沒有價格資料")
    actions = _actions(events, bars, guarded=guarded)
    raw, active = _state_before(actions, initial, start, guarded=guarded)
    entries = {
        code: eligible[0].open for code, position in active.items() if position
    }
    realized = 0.0
    peak = 0.0
    result = Result()
    relevant_actions = [
        action for action in actions if start <= action[0] <= eligible[-1].bar_time
    ]
    action_index = 0

    for bar in eligible:
        while (
            action_index < len(relevant_actions)
            and relevant_actions[action_index][0] <= bar.bar_time
        ):
            _, _, kind, payload = relevant_actions[action_index]
            previous = dict(active)
            if kind == "boundary":
                boundary = payload
                active = _apply_boundary(raw, active, boundary)
                price = boundary.price
            else:
                event, execution = payload
                price = execution.open
                if guarded:
                    apply_signal(raw, active, event, execution)
                else:
                    raw[event.strategy_code] = event.new_position
                    active[event.strategy_code] = event.new_position

            for code in ALL_STRATEGIES:
                old = previous[code]
                new = active[code]
                if old == new:
                    continue
                if old and code in entries:
                    pnl = (price - entries[code]) * old * 10
                    realized += pnl
                    result.closed_pnls.append(pnl)
                    if pnl > 0:
                        result.gross_profit += pnl
                    elif pnl < 0:
                        result.gross_loss += -pnl
                    entries.pop(code, None)
                if new:
                    entries[code] = price
                result.turnover += abs(new - old)
            action_index += 1

        unrealized = sum(
            (bar.close - entries[code]) * position * 10
            for code, position in active.items()
            if position and code in entries
        )
        equity = realized + unrealized
        peak = max(peak, equity)
        result.max_drawdown = max(result.max_drawdown, peak - equity)
        result.unrealized = unrealized

    result.realized = realized
    result.ending_position = sum(active.values())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest EF dual-session guard")
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--start", default="2026-07-01 00:00:00")
    parser.add_argument("--end", default="")
    parser.add_argument("--one-way-cost-twd", type=float, default=24.0)
    args = parser.parse_args()

    bars = load_price_bars(args.prices)
    events, initial, untimed = load_events(args.signals)
    start = parse_time(args.start)
    end = parse_time(args.end) if args.end else bars[-1].bar_time
    print(
        f"period={start}..{end} event_time=received_at fill=next_minute_open "
        f"untimed_initial_rows={untimed} one_way_cost_twd={args.one_way_cost_twd:g}"
    )
    print(
        "policy gross_twd estimated_net_twd max_drawdown_twd PF one_way "
        "ending_position"
    )
    for name, guarded in (("raw_ef", False), ("dual_session_guard", True)):
        result = run(
            events=events,
            initial=initial,
            bars=bars,
            start=start,
            end=end,
            guarded=guarded,
        )
        estimated_net = result.total - result.turnover * args.one_way_cost_twd
        pf = "inf" if math.isinf(result.profit_factor) else f"{result.profit_factor:.2f}"
        print(
            name,
            round(result.total),
            round(estimated_net),
            round(result.max_drawdown),
            pf,
            result.turnover,
            result.ending_position,
        )


if __name__ == "__main__":
    main()
