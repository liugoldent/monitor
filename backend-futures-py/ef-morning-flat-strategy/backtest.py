from __future__ import annotations

import argparse
import bisect
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from strategy import (
    ALL_STRATEGIES,
    PriceBar,
    SignalEvent,
    apply_signal,
    load_price_bars,
    load_signal_rows,
    morning_boundaries,
    next_minute_open,
    parse_signal_row,
    parse_time,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
DEFAULT_SIGNALS = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
DEFAULT_PRICES = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"


@dataclass
class Result:
    realized: float
    unrealized: float
    one_way: int
    ending_position: int

    @property
    def total(self) -> float:
        return self.realized + self.unrealized


def price_at_or_after(bars: list[PriceBar], timestamp: datetime) -> PriceBar | None:
    times = [bar.bar_time for bar in bars]
    index = bisect.bisect_left(times, timestamp)
    return None if index >= len(bars) else bars[index]


def load_events(path: Path) -> list[SignalEvent]:
    rows = load_signal_rows(path)
    events = [
        event
        for row_number, row in enumerate(rows, start=1)
        if (event := parse_signal_row(row, row_number)) is not None
    ]
    return sorted(events, key=lambda event: (event.timestamp, event.row_number))


def transition(
    positions: dict[str, int],
    entries: dict[str, float],
    code: str,
    target: int,
    price: float,
) -> tuple[float, int]:
    previous = positions.get(code, 0)
    if previous == target:
        return 0.0, 0
    pnl = 0.0
    if previous and code in entries:
        pnl = (price - entries[code]) * previous * 10
    positions[code] = target
    if target:
        entries[code] = price
    else:
        entries.pop(code, None)
    return pnl, abs(target - previous)


def initial_positions(events: list[SignalEvent], start: datetime) -> dict[str, int]:
    positions = {code: 0 for code in ALL_STRATEGIES}
    for event in events:
        if event.timestamp >= start:
            break
        positions[event.strategy_code] = event.new_position
    return positions


def run(
    *,
    events: list[SignalEvent],
    bars: list[PriceBar],
    start: datetime,
    end: datetime,
    morning_flat: bool,
) -> Result:
    first_bar = price_at_or_after(bars, start)
    eligible_end = [bar for bar in bars if bar.bar_time <= end]
    if first_bar is None or not eligible_end:
        raise RuntimeError("回測區間沒有價格資料")
    last_bar = eligible_end[-1]
    positions = initial_positions(events, start)
    entries = {
        code: first_bar.open for code, position in positions.items() if position
    }
    actions: list[tuple[datetime, int, str, float, SignalEvent | None]] = []
    if morning_flat:
        for boundary in morning_boundaries(bars):
            if start <= boundary.bar_time <= last_bar.bar_time:
                actions.append(
                    (boundary.bar_time, 0, "flatten", boundary.open, None)
                )
    for event in events:
        if event.timestamp < start:
            continue
        execution = next_minute_open(bars, event.timestamp)
        if execution is None or execution.bar_time > last_bar.bar_time:
            continue
        actions.append((execution.bar_time, 1, "signal", execution.open, event))

    realized = 0.0
    one_way = 0
    for _, _, kind, price, event in sorted(actions, key=lambda item: (item[0], item[1])):
        if kind == "flatten":
            for code in ALL_STRATEGIES:
                pnl, quantity = transition(positions, entries, code, 0, price)
                realized += pnl
                one_way += quantity
            continue
        assert event is not None
        if morning_flat:
            decision = apply_signal(positions, event, next_minute_open(bars, event.timestamp))
            target = decision.shadow_position
            # apply_signal already updates positions; temporarily restore so the
            # common transition function can book PnL and turnover once.
            positions[event.strategy_code] = decision.previous_shadow_position
        else:
            target = event.new_position
        pnl, quantity = transition(
            positions, entries, event.strategy_code, target, price
        )
        realized += pnl
        one_way += quantity

    unrealized = sum(
        (last_bar.close - entries[code]) * position * 10
        for code, position in positions.items()
        if position and code in entries
    )
    return Result(realized, unrealized, one_way, sum(positions.values()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest EF 04:59 morning-flat shadow")
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--start", default="2026-08-01 00:00:00")
    parser.add_argument("--end", default="")
    parser.add_argument("--one-way-cost", type=float, default=0.0)
    args = parser.parse_args()

    bars = load_price_bars(args.prices)
    events = load_events(args.signals)
    start = parse_time(args.start)
    end = parse_time(args.end) if args.end else bars[-1].bar_time
    print("policy gross_twd one_way estimated_net_twd end_position")
    for name, enabled in (("hold_through_break", False), ("04:59_flat_wait_signal", True)):
        result = run(
            events=events,
            bars=bars,
            start=start,
            end=end,
            morning_flat=enabled,
        )
        estimated_net = result.total - result.one_way * args.one_way_cost
        print(
            name,
            round(result.total),
            result.one_way,
            round(estimated_net),
            result.ending_position,
        )


if __name__ == "__main__":
    main()
