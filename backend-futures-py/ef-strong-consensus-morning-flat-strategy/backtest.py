from __future__ import annotations

import argparse
import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from strategy import (
    ALL_STRATEGIES,
    PriceBar,
    SignalEvent,
    consensus_target,
    evaluate_event,
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
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    realized: float = 0.0
    unrealized: float = 0.0
    turnover: int = 0
    closed_legs: int = 0
    ending_position: int = 0
    max_drawdown: float = 0.0
    exposure_bars: int = 0
    marked_bars: int = 0
    missing_fills: int = 0
    trade_pnls: list[float] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.realized + self.unrealized

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf


def load_events(path: Path) -> tuple[list[SignalEvent], int, int]:
    events: list[SignalEvent] = []
    untimed = 0
    duplicates = 0
    seen: set[tuple[object, ...]] = set()
    for row_number, row in enumerate(load_signal_rows(path), start=1):
        if not str(row.get("received_at") or "").strip():
            untimed += 1
            continue
        event = parse_signal_row(row, row_number)
        if event is None:
            continue
        key = (
            event.timestamp,
            event.strategy_code,
            event.previous_position,
            event.new_position,
            str(row.get("account") or ""),
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        events.append(event)
    events.sort(key=lambda value: (value.timestamp, value.row_number))
    return events, untimed, duplicates


def price_at_or_after(bars: list[PriceBar], timestamp: datetime) -> PriceBar | None:
    times = [bar.bar_time for bar in bars]
    index = bisect.bisect_left(times, timestamp)
    return None if index >= len(bars) else bars[index]


def run(
    *,
    events: list[SignalEvent],
    bars: list[PriceBar],
    start: datetime,
    end: datetime,
    threshold: int,
) -> Result:
    eligible_bars = [bar for bar in bars if start <= bar.bar_time <= end]
    if not eligible_bars:
        raise RuntimeError("回測區間沒有價格資料")
    first_bar = eligible_bars[0]
    last_bar = eligible_bars[-1]
    raw_positions = {code: 0 for code in ALL_STRATEGIES}
    for event in events:
        if event.timestamp >= start:
            break
        raw_positions[event.strategy_code] = event.new_position

    position = consensus_target(raw_positions, threshold)[0]
    entry_price: float | None = first_bar.open if position else None
    realized = 0.0
    peak = 0.0
    result = Result(ending_position=position)

    actions: list[tuple[datetime, int, SignalEvent | None]] = []
    for boundary in morning_boundaries(bars):
        if start <= boundary.bar_time <= end:
            actions.append((boundary.bar_time, 0, None))
    for event in events:
        if not start <= event.timestamp <= end:
            continue
        execution = next_minute_open(bars, event.timestamp)
        if execution is None or execution.bar_time > last_bar.bar_time:
            continue
        actions.append((execution.bar_time, 1, event))
    actions.sort(
        key=lambda item: (
            item[0],
            item[1],
            0 if item[2] is None else item[2].row_number,
        )
    )

    action_index = 0
    for bar in eligible_bars:
        while action_index < len(actions) and actions[action_index][0] <= bar.bar_time:
            action_time, kind, event = actions[action_index]
            fill_bar = price_at_or_after(bars, action_time)
            if fill_bar is None or fill_bar.bar_time > last_bar.bar_time:
                result.missing_fills += 1
                action_index += 1
                continue
            if kind == 0:
                target = 0
            else:
                assert event is not None
                decision = evaluate_event(
                    raw_positions,
                    position,
                    event,
                    fill_bar,
                    threshold=threshold,
                )
                target = decision.target_position
            if target != position:
                if position:
                    assert entry_price is not None
                    pnl = (fill_bar.open - entry_price) * position
                    result.trade_pnls.append(pnl)
                    result.closed_legs += 1
                    realized += pnl
                    if pnl > 0:
                        result.gross_profit += pnl
                    elif pnl < 0:
                        result.gross_loss += -pnl
                result.turnover += abs(target - position)
                position = target
                entry_price = fill_bar.open if target else None
            action_index += 1

        unrealized = 0.0
        if position and entry_price is not None:
            unrealized = (bar.close - entry_price) * position
            result.exposure_bars += 1
        equity = realized + unrealized
        peak = max(peak, equity)
        result.max_drawdown = min(result.max_drawdown, equity - peak)
        result.marked_bars += 1
        result.unrealized = unrealized

    result.realized = realized
    result.ending_position = position
    if abs(result.gross_profit - result.gross_loss - result.realized) > 1e-9:
        raise AssertionError("gross profit/loss reconciliation failed")
    if abs(result.realized + result.unrealized - result.total) > 1e-9:
        raise AssertionError("total P&L reconciliation failed")
    return result


def fmt(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest EF strong consensus with 04:59 morning flatten"
    )
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--start", default="2026-06-24 00:00:00")
    parser.add_argument("--end", required=True)
    parser.add_argument("--threshold", type=int, default=2)
    parser.add_argument("--one-way-cost", type=float, default=2.0)
    parser.add_argument("--point-value", type=float, default=10.0)
    args = parser.parse_args()

    start = parse_time(args.start)
    end = parse_time(args.end)
    events, untimed, duplicate_events = load_events(args.signals)
    bars = load_price_bars(args.prices)
    result = run(
        events=events,
        bars=bars,
        start=start,
        end=end,
        threshold=args.threshold,
    )
    estimated_net = result.total - result.turnover * args.one_way_cost
    win_rate = (
        sum(pnl > 0 for pnl in result.trade_pnls) / len(result.trade_pnls)
        if result.trade_pnls
        else math.nan
    )
    exposure = result.exposure_bars / result.marked_bars if result.marked_bars else 0.0
    print(
        f"period={start}..{end} event_time=received_at fill=next_minute_open "
        f"untimed_skipped={untimed} duplicate_events={duplicate_events} "
        f"one_way_cost_points={args.one_way_cost:g} point_value={args.point_value:g}"
    )
    print(
        "gross_profit gross_loss PF closed_legs win_rate realized unrealized total "
        "turnover estimated_net max_drawdown_mtm ending_position exposure missing_fills"
    )
    print(
        fmt(result.gross_profit),
        fmt(result.gross_loss),
        fmt(result.profit_factor),
        result.closed_legs,
        fmt(win_rate),
        fmt(result.realized),
        fmt(result.unrealized),
        fmt(result.total),
        result.turnover,
        fmt(estimated_net),
        fmt(result.max_drawdown),
        result.ending_position,
        fmt(exposure),
        result.missing_fills,
    )
    print(
        f"estimated_net_twd={estimated_net * args.point_value:.2f} "
        f"max_drawdown_twd={result.max_drawdown * args.point_value:.2f}"
    )


if __name__ == "__main__":
    main()
