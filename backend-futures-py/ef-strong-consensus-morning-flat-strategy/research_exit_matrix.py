from __future__ import annotations

import argparse
import bisect
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from backtest import load_events
from strategy import (
    ALL_STRATEGIES,
    PORTFOLIO_E,
    PORTFOLIO_F,
    PriceBar,
    SignalEvent,
    consensus_target,
    load_price_bars,
    next_minute_open,
    normalized_positions,
    parse_time,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
DEFAULT_SIGNALS = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
DEFAULT_PRICES = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
NIGHT_OPEN = time(15, 0)
NIGHT_END = time(5, 0)
MORNING_BLOCK_END = time(8, 45)


@dataclass
class Metrics:
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    realized: float = 0.0
    turnover: int = 0
    closed_legs: int = 0
    wins: int = 0
    sessions: int = 0
    trade_pnls: list[float] = field(default_factory=list)

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf

    @property
    def win_rate(self) -> float:
        return self.wins / self.closed_legs if self.closed_legs else math.nan

    def close_leg(self, pnl: float) -> None:
        self.closed_legs += 1
        self.trade_pnls.append(pnl)
        self.realized += pnl
        if pnl > 0:
            self.gross_profit += pnl
            self.wins += 1
        elif pnl < 0:
            self.gross_loss += -pnl

    def merge(self, other: "Metrics") -> None:
        self.gross_profit += other.gross_profit
        self.gross_loss += other.gross_loss
        self.realized += other.realized
        self.turnover += other.turnover
        self.closed_legs += other.closed_legs
        self.wins += other.wins
        self.sessions += other.sessions
        self.trade_pnls.extend(other.trade_pnls)

    def validate(self) -> None:
        if abs(self.gross_profit - self.gross_loss - self.realized) > 1e-9:
            raise AssertionError("gross profit/loss reconciliation failed")


def is_us_dst(day: date) -> bool:
    """US DST dates, sufficient for grouping Taiwan night sessions."""
    march_first = date(day.year, 3, 1)
    first_sunday_march = march_first + timedelta(days=(6 - march_first.weekday()) % 7)
    start = first_sunday_march + timedelta(days=7)
    november_first = date(day.year, 11, 1)
    end = november_first + timedelta(days=(6 - november_first.weekday()) % 7)
    return start <= day < end


def target_for(positions: dict[str, int], strong: bool, threshold: int) -> int:
    if strong:
        return consensus_target(positions, threshold)[0]
    net = sum(normalized_positions(positions).values())
    return 1 if net > 0 else -1 if net < 0 else 0


def price_at(bars_by_time: dict[datetime, PriceBar], timestamp: datetime) -> PriceBar | None:
    return bars_by_time.get(timestamp)


def raw_positions_before(events: list[SignalEvent], timestamp: datetime) -> dict[str, int]:
    positions = {code: 0 for code in ALL_STRATEGIES}
    for event in events:
        if event.timestamp >= timestamp:
            break
        positions[event.strategy_code] = event.new_position
    return positions


def night_sessions(
    bars_by_time: dict[datetime, PriceBar], start: datetime, end: datetime, exit_clock: time
) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for timestamp in sorted(bars_by_time):
        if timestamp.time() != NIGHT_OPEN or not start <= timestamp <= end:
            continue
        exit_at = datetime.combine(timestamp.date() + timedelta(days=1), exit_clock)
        if exit_at <= end and exit_at in bars_by_time:
            result.append((timestamp, exit_at))
    return result


def run_night_only(
    *,
    events: list[SignalEvent],
    bars: list[PriceBar],
    start: datetime,
    end: datetime,
    exit_clock: time,
    strong: bool,
    threshold: int,
    allowed_session_opens: set[datetime] | None = None,
) -> dict[str, Metrics]:
    """Replay only new night-session signals and settle before the day session.

    Every night starts flat.  Positions are calculated from the complete raw E/F
    state at 15:00, but entry requires a new actionable event during that night.
    Events fill at strict next-minute open.  A fill at/after the selected exit
    boundary is not allowed; an open leg is settled at the boundary open.
    """
    bars_by_time = {bar.bar_time: bar for bar in bars}
    event_times = [event.timestamp for event in events]
    grouped = {"all": Metrics(), "summer": Metrics(), "winter": Metrics()}
    for session_open, exit_at in night_sessions(bars_by_time, start, end, exit_clock):
        if allowed_session_opens is not None and session_open not in allowed_session_opens:
            continue
        metrics = Metrics(sessions=1)
        positions = raw_positions_before(events, session_open)
        position = 0
        entry_price: float | None = None
        begin = bisect.bisect_left(event_times, session_open)
        finish = bisect.bisect_left(event_times, exit_at)
        for event in events[begin:finish]:
            fill = next_minute_open(bars, event.timestamp)
            positions[event.strategy_code] = event.new_position
            if fill is None or fill.bar_time >= exit_at:
                continue
            target = target_for(positions, strong, threshold)
            if target == position:
                continue
            if position:
                assert entry_price is not None
                metrics.close_leg((fill.open - entry_price) * position)
            metrics.turnover += abs(target - position)
            position = target
            entry_price = fill.open if target else None
        if position:
            exit_bar = price_at(bars_by_time, exit_at)
            assert exit_bar is not None and entry_price is not None
            metrics.close_leg((exit_bar.open - entry_price) * position)
            metrics.turnover += abs(position)
        metrics.validate()
        season = "summer" if is_us_dst(session_open.date()) else "winter"
        grouped["all"].merge(metrics)
        grouped[season].merge(metrics)
    for metrics in grouped.values():
        metrics.validate()
    return grouped


def morning_boundaries_for_clock(
    bars_by_time: dict[datetime, PriceBar], start: datetime, end: datetime, clock: time
) -> list[datetime]:
    return [
        timestamp
        for timestamp in sorted(bars_by_time)
        if timestamp.time() == clock and start <= timestamp <= end
    ]


def in_morning_block(event_time: datetime, fill_time: datetime) -> bool:
    return (
        time(4, 59) <= event_time.time() < MORNING_BLOCK_END
        or time(4, 59) <= fill_time.time() < MORNING_BLOCK_END
    )


def run_full_replay(
    *,
    events: list[SignalEvent],
    bars: list[PriceBar],
    start: datetime,
    end: datetime,
    strong: bool,
    fixed_exit: bool,
    threshold: int,
) -> Metrics:
    """Full production-style replay used for the 2x2 attribution matrix."""
    eligible = [bar for bar in bars if start <= bar.bar_time <= end]
    if not eligible:
        raise RuntimeError("no eligible price bars")
    bars_by_time = {bar.bar_time: bar for bar in bars}
    positions = raw_positions_before(events, start)
    position = target_for(positions, strong, threshold)
    entry_price = eligible[0].open if position else None
    result = Metrics()
    actions: list[tuple[datetime, int, SignalEvent | None]] = []
    if fixed_exit:
        actions.extend(
            (timestamp, 0, None)
            for timestamp in morning_boundaries_for_clock(
                bars_by_time, start, end, time(4, 59)
            )
        )
    for event in events:
        if not start <= event.timestamp <= end:
            continue
        fill = next_minute_open(bars, event.timestamp)
        if fill is not None and fill.bar_time <= end:
            actions.append((fill.bar_time, 1, event))
    actions.sort(key=lambda item: (item[0], item[1], getattr(item[2], "row_number", 0)))
    for fill_time, kind, event in actions:
        fill = bars_by_time.get(fill_time)
        if fill is None:
            continue
        if kind == 0:
            target = 0
        else:
            assert event is not None
            positions[event.strategy_code] = event.new_position
            target = target_for(positions, strong, threshold)
            if fixed_exit and in_morning_block(event.timestamp, fill_time):
                target = 0
        if target == position:
            continue
        if position:
            assert entry_price is not None
            result.close_leg((fill.open - entry_price) * position)
        result.turnover += abs(target - position)
        position = target
        entry_price = fill.open if target else None
    if position:
        result.close_leg((eligible[-1].close - entry_price) * position)
        result.turnover += abs(position)
    result.validate()
    return result


def parse_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def fmt(value: float) -> str:
    if math.isnan(value):
        return "n/a"
    return "inf" if math.isinf(value) else f"{value:.2f}"


def print_row(label: str, metrics: Metrics, one_way_cost: float) -> None:
    net = metrics.realized - metrics.turnover * one_way_cost
    print(
        f"{label:30s} sessions={metrics.sessions:3d} gross={metrics.realized:9.2f} "
        f"net={net:9.2f} GP={metrics.gross_profit:9.2f} GL={metrics.gross_loss:9.2f} "
        f"PF={fmt(metrics.profit_factor):>7s} legs={metrics.closed_legs:3d} "
        f"win={fmt(metrics.win_rate):>5s} turnover={metrics.turnover:3d}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="EF exit-time, DST, night-only and 2x2 study")
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--start", default="2026-06-24 00:00:00")
    parser.add_argument("--end", required=True)
    parser.add_argument("--threshold", type=int, default=2)
    parser.add_argument("--one-way-cost", type=float, default=2.0)
    parser.add_argument(
        "--exit-times", nargs="+", default=["03:59", "04:00", "04:30", "04:59"]
    )
    args = parser.parse_args()

    start = parse_time(args.start)
    end = parse_time(args.end)
    events, untimed, duplicates = load_events(args.signals)
    bars = load_price_bars(args.prices)
    print(
        f"period={start}..{end} event_time=received_at fill=next_minute_open "
        f"untimed_skipped={untimed} duplicate_events={duplicates} cost={args.one_way_cost:g}pt/way"
    )
    print("\nNIGHT ONLY: starts flat at 15:00, requires a new night event, settles at boundary open")
    exit_clocks = [parse_clock(value) for value in args.exit_times]
    bars_by_time = {bar.bar_time: bar for bar in bars}
    session_sets = [
        {session_open for session_open, _ in night_sessions(bars_by_time, start, end, clock)}
        for clock in exit_clocks
    ]
    common_sessions = set.intersection(*session_sets) if session_sets else set()
    print(f"complete_case_sessions={len(common_sessions)}")
    for clock_text, exit_clock in zip(args.exit_times, exit_clocks):
        grouped = run_night_only(
            events=events,
            bars=bars,
            start=start,
            end=end,
            exit_clock=exit_clock,
            strong=True,
            threshold=args.threshold,
            allowed_session_opens=common_sessions,
        )
        for season in ("all", "summer", "winter"):
            print_row(f"strong exit={clock_text} {season}", grouped[season], args.one_way_cost)

    print("\n2x2 FULL REPLAY: total-net/strong-consensus x signal-only/04:59-flat")
    for strong in (False, True):
        for fixed_exit in (False, True):
            result = run_full_replay(
                events=events,
                bars=bars,
                start=start,
                end=end,
                strong=strong,
                fixed_exit=fixed_exit,
                threshold=args.threshold,
            )
            print_row(
                f"{'strong' if strong else 'total_net'} + {'04:59' if fixed_exit else 'no_flat'}",
                result,
                args.one_way_cost,
            )


if __name__ == "__main__":
    main()
