from __future__ import annotations

import argparse
import bisect
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import backtest_ef_strong_h_consensus as h_base  # noqa: E402
import backtest_five_strategies_next_open as base  # noqa: E402
from backtest_thresholds import (  # noqa: E402
    Book,
    EventStats,
    inferred_start_positions,
    load_events_strict,
)


@dataclass(frozen=True)
class Action:
    timestamp: datetime
    priority: int
    sequence: int
    kind: str
    price: float
    payload: object


@dataclass(frozen=True)
class Trigger:
    timestamp: datetime
    price: float
    threshold: int
    ef_net: int
    ef_gross: int
    h_position: int
    exited_quantity: int
    exit_pnl: float


@dataclass
class Portfolio:
    books: dict[str, Book] = field(
        default_factory=lambda: {code: Book() for code in base.ALL_STRATEGIES}
    )

    def initialize(self, positions: dict[str, int], price: float) -> None:
        for code, book in self.books.items():
            book.initialize(positions.get(code, 0), price)

    def trade_code(self, code: str, target: int, price: float, cost: float) -> None:
        self.books[code].trade_to(target, price, cost)

    def trade_all(
        self, targets: dict[str, int], price: float, cost: float
    ) -> None:
        for code in base.ALL_STRATEGIES:
            self.trade_code(code, targets.get(code, 0), price, cost)

    def flatten(self, price: float, cost: float) -> int:
        quantity = self.gross_position
        for code in base.ALL_STRATEGIES:
            self.trade_code(code, 0, price, cost)
        return quantity

    @property
    def position(self) -> int:
        return sum(book.position for book in self.books.values())

    @property
    def gross_position(self) -> int:
        return sum(abs(book.position) for book in self.books.values())

    @property
    def turnover(self) -> int:
        return sum(book.turnover for book in self.books.values())

    @property
    def costs(self) -> float:
        return sum(book.costs for book in self.books.values())

    @property
    def realized(self) -> float:
        return sum(book.realized for book in self.books.values())

    @property
    def closed_pnls(self) -> list[float]:
        return [pnl for book in self.books.values() for pnl in book.closed_pnls]

    def unrealized(self, price: float) -> float:
        return sum(book.unrealized(price) for book in self.books.values())

    def net_equity(self, price: float) -> float:
        return sum(book.net_equity(price) for book in self.books.values())


@dataclass
class Result:
    name: str
    threshold: int | None
    portfolio: Portfolio
    equities: list[float]
    triggers: list[Trigger]
    suppressed_ef_events: int = 0

    @property
    def gross_profit(self) -> float:
        return sum(pnl for pnl in self.portfolio.closed_pnls if pnl > 0)

    @property
    def gross_loss(self) -> float:
        return -sum(pnl for pnl in self.portfolio.closed_pnls if pnl < 0)

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf

    @property
    def max_drawdown(self) -> float:
        peak = 0.0
        worst = 0.0
        for equity in self.equities:
            peak = max(peak, equity)
            worst = max(worst, peak - equity)
        return worst

    @property
    def unrealized(self) -> float:
        return self.equities[-1] + self.portfolio.costs - self.portfolio.realized

    @property
    def total(self) -> float:
        return self.equities[-1]


def sign(value: int | float) -> int:
    return (value > 0) - (value < 0)


def position_net(positions: dict[str, int]) -> int:
    return sum(positions.get(code, 0) for code in base.ALL_STRATEGIES)


def position_gross(positions: dict[str, int]) -> int:
    return sum(abs(positions.get(code, 0)) for code in base.ALL_STRATEGIES)


def in_opening_window(timestamp: datetime, window_minutes: int) -> bool:
    if window_minutes <= 0:
        return True
    clock = timestamp.time()
    for opening in (time(8, 45), time(15, 0)):
        start = datetime.combine(timestamp.date(), opening)
        if start <= timestamp < start + timedelta(minutes=window_minutes):
            return True
    return False


def map_ef_actions(
    events: list[base.EfEvent],
    bars: list[base.PriceBar],
    start: datetime,
    end: datetime,
) -> tuple[list[Action], int]:
    times = [bar.bar_time for bar in bars]
    actions: list[Action] = []
    missing = 0
    for event in events:
        if event.timestamp < start or event.timestamp > end:
            continue
        target = event.timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        index = bisect.bisect_left(times, target)
        if index >= len(bars) or bars[index].bar_time > end:
            missing += 1
            continue
        bar = bars[index]
        actions.append(
            Action(bar.bar_time, 0, event.row_number, "ef", bar.open, event)
        )
    return actions, missing


def map_h_actions(
    events: list[h_base.HEvent],
    bars: list[base.PriceBar],
    start: datetime,
    end: datetime,
    fill_mode: str,
) -> tuple[list[Action], int]:
    times = [bar.bar_time for bar in bars]
    actions: list[Action] = []
    missing = 0
    for sequence, event in enumerate(events):
        if event.timestamp < start or event.timestamp > end:
            continue
        if fill_mode == "recorded" and event.price is not None:
            timestamp = event.timestamp
            price = event.price
        else:
            target = event.timestamp.replace(second=0, microsecond=0) + timedelta(
                minutes=1
            )
            index = bisect.bisect_left(times, target)
            if index >= len(bars) or bars[index].bar_time > end:
                missing += 1
                continue
            timestamp = bars[index].bar_time
            price = bars[index].open
        actions.append(Action(timestamp, 1, sequence, "h", price, event))
    return actions, missing


def initial_h_position(events: list[h_base.HEvent], start: datetime) -> int:
    position = 0
    for event in events:
        if event.timestamp >= start:
            break
        position = event.position
    return position


def run_baseline(
    *,
    bars: list[base.PriceBar],
    initial_positions: dict[str, int],
    ef_actions: list[Action],
    one_way_cost: float,
) -> Result:
    portfolio = Portfolio()
    portfolio.initialize(initial_positions, bars[0].open)
    equities: list[float] = []
    action_index = 0
    for bar in bars:
        while (
            action_index < len(ef_actions)
            and ef_actions[action_index].timestamp <= bar.bar_time
        ):
            action = ef_actions[action_index]
            event = action.payload
            assert isinstance(event, base.EfEvent)
            portfolio.trade_code(
                event.strategy_code, event.new_position, action.price, one_way_cost
            )
            action_index += 1
        equities.append(portfolio.net_equity(bar.close))
    result = Result("baseline_pure_ef", None, portfolio, equities, [])
    validate_result(result, bars[-1].close)
    return result


def run_h3_exit(
    *,
    name: str,
    threshold: int,
    reentry_policy: str,
    bars: list[base.PriceBar],
    initial_positions: dict[str, int],
    initial_h: int,
    actions: list[Action],
    one_way_cost: float,
    opening_window_minutes: int = 0,
    min_add_streak: int = 0,
    require_wave_peak: bool = False,
    max_minutes_after_add: int = 0,
) -> Result:
    desired = dict(initial_positions)
    portfolio = Portfolio()
    portfolio.initialize(initial_positions, bars[0].open)
    h_position = initial_h
    source_net = position_net(desired)
    wave_peak_abs = abs(source_net)
    add_streak = 0
    last_add_at: datetime | None = None
    armed_direction = sign(source_net) if abs(source_net) >= threshold else 0
    armed_at = bars[0].bar_time if armed_direction else None
    wave_triggered = False
    stopped = False
    stopped_direction = 0
    triggers: list[Trigger] = []
    suppressed = 0
    equities: list[float] = []
    action_index = 0

    for bar in bars:
        while action_index < len(actions) and actions[action_index].timestamp <= bar.bar_time:
            action = actions[action_index]
            old_source_net = source_net

            if action.kind == "ef":
                event = action.payload
                assert isinstance(event, base.EfEvent)
                desired[event.strategy_code] = event.new_position
                source_net = position_net(desired)
                source_direction = sign(source_net)

                old_direction = sign(old_source_net)
                if source_direction and source_direction == old_direction:
                    if abs(source_net) > abs(old_source_net):
                        add_streak += 1
                        last_add_at = action.timestamp
                    elif abs(source_net) < abs(old_source_net):
                        add_streak = 0
                elif source_direction:
                    add_streak = 1
                    last_add_at = action.timestamp
                else:
                    add_streak = 0
                    last_add_at = None

                if source_direction != old_direction:
                    wave_peak_abs = abs(source_net)
                else:
                    wave_peak_abs = max(wave_peak_abs, abs(source_net))

                if stopped and reentry_policy == "h_align":
                    if source_direction and h_position == source_direction:
                        portfolio.trade_all(desired, action.price, one_way_cost)
                        stopped = False
                        stopped_direction = 0
                    else:
                        suppressed += 1
                elif stopped and reentry_policy == "below_threshold":
                    if (
                        abs(source_net) < threshold
                        or (source_direction and source_direction != stopped_direction)
                    ):
                        portfolio.trade_all(desired, action.price, one_way_cost)
                        stopped = False
                        stopped_direction = 0
                    else:
                        suppressed += 1
                else:
                    portfolio.trade_code(
                        event.strategy_code,
                        event.new_position,
                        action.price,
                        one_way_cost,
                    )

                crossed_up = abs(old_source_net) < threshold <= abs(source_net)
                changed_high_direction = (
                    source_direction != old_direction and abs(source_net) >= threshold
                )
                if abs(source_net) < threshold or source_direction == 0:
                    armed_direction = 0
                    armed_at = None
                    wave_triggered = False
                elif changed_high_direction or crossed_up:
                    armed_direction = source_direction
                    armed_at = action.timestamp
                    wave_triggered = False

            else:
                h_event = action.payload
                assert isinstance(h_event, h_base.HEvent)
                previous_h = h_position
                h_position = h_event.position
                source_net = position_net(desired)
                source_direction = sign(source_net)

                if (
                    stopped
                    and reentry_policy == "h_align"
                    and source_direction
                    and h_position == source_direction
                ):
                    portfolio.trade_all(desired, action.price, one_way_cost)
                    stopped = False
                    stopped_direction = 0

                is_reversal = previous_h != 0 and h_position == -previous_h
                h_opposes_ef = source_direction != 0 and h_position == -source_direction
                armed_before_h = armed_at is not None and armed_at < action.timestamp
                opening_ok = in_opening_window(
                    h_event.timestamp, opening_window_minutes
                )
                streak_ok = add_streak >= min_add_streak
                peak_ok = not require_wave_peak or abs(source_net) == wave_peak_abs
                recency_ok = (
                    max_minutes_after_add <= 0
                    or (
                        last_add_at is not None
                        and action.timestamp - last_add_at
                        <= timedelta(minutes=max_minutes_after_add)
                    )
                )
                if (
                    is_reversal
                    and h_opposes_ef
                    and opening_ok
                    and streak_ok
                    and peak_ok
                    and recency_ok
                    and armed_direction == source_direction
                    and armed_before_h
                    and not wave_triggered
                    and portfolio.gross_position > 0
                ):
                    realized_before_exit = portfolio.realized
                    exited = portfolio.flatten(action.price, one_way_cost)
                    triggers.append(
                        Trigger(
                            timestamp=action.timestamp,
                            price=action.price,
                            threshold=threshold,
                            ef_net=source_net,
                            ef_gross=position_gross(desired),
                            h_position=h_position,
                            exited_quantity=exited,
                            exit_pnl=portfolio.realized - realized_before_exit,
                        )
                    )
                    wave_triggered = True
                    armed_direction = 0
                    armed_at = None
                    stopped = reentry_policy != "native"
                    stopped_direction = source_direction if stopped else 0

            action_index += 1

        equities.append(portfolio.net_equity(bar.close))

    result = Result(
        name=name,
        threshold=threshold,
        portfolio=portfolio,
        equities=equities,
        triggers=triggers,
        suppressed_ef_events=suppressed,
    )
    validate_result(result, bars[-1].close)
    return result


def validate_result(result: Result, final_price: float) -> None:
    realized = result.portfolio.realized
    unrealized = result.portfolio.unrealized(final_price)
    costs = result.portfolio.costs
    if abs(result.gross_profit - result.gross_loss - realized) > 1e-6:
        raise AssertionError(f"{result.name}: gross P&L reconciliation failed")
    if abs(realized + unrealized - costs - result.total) > 1e-6:
        raise AssertionError(f"{result.name}: final equity reconciliation failed")


def format_pf(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.3f}"


def monthly_deltas(
    bars: list[base.PriceBar], equities: list[float]
) -> dict[str, float]:
    last_equity: dict[str, float] = {}
    for bar, equity in zip(bars, equities):
        last_equity[bar.bar_time.strftime("%Y-%m")] = equity
    deltas: dict[str, float] = {}
    previous = 0.0
    for month, equity in last_equity.items():
        deltas[month] = equity - previous
        previous = equity
    return deltas


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest global pure-EF exit after a high-position H3 reversal"
    )
    parser.add_argument("--start", default="2026-06-24 00:00:00")
    parser.add_argument("--end", required=True)
    parser.add_argument("--threshold-min", type=int, default=3)
    parser.add_argument("--threshold-max", type=int, default=10)
    parser.add_argument("--one-way-cost", type=float, default=2.4)
    parser.add_argument(
        "--opening-window-minutes",
        type=int,
        default=0,
        help="Only accept H3 reversals this many minutes after 08:45 or 15:00; zero disables the filter.",
    )
    parser.add_argument(
        "--min-add-streak",
        type=int,
        default=0,
        help="Require this many consecutive same-direction EF net additions before H3 reverses.",
    )
    parser.add_argument(
        "--require-wave-peak",
        action="store_true",
        help="Require EF absolute net to still equal its high-water mark for the current direction wave.",
    )
    parser.add_argument(
        "--max-minutes-after-add",
        type=int,
        default=0,
        help="Maximum time since the latest EF net addition; zero disables the recency filter.",
    )
    parser.add_argument(
        "--h-fill",
        choices=("recorded", "strict-next"),
        default="recorded",
        help="Use recorded TradingView H3 fills where present, or proxy every H3 signal at the strict next 1m open.",
    )
    parser.add_argument("--show-triggers", action="store_true")
    args = parser.parse_args()

    if args.threshold_min < 1 or args.threshold_max < args.threshold_min:
        raise ValueError("invalid threshold range")

    backend = REPO_ROOT / "backend-futures-py"
    price_path = backend / "tv_doc" / "webhook_data_1min.csv"
    event_path = backend / "tv_doc" / "six_strategy_signal_events.csv"
    h_export_path = Path.home() / "Downloads" / "h3.csv"
    h_live_path = backend / "telegram-relay-records" / "h_position_events.csv"
    start = base.parse_time(args.start)
    requested_end = base.parse_time(args.end)

    all_bars = base.load_prices(price_path)
    bars = [bar for bar in all_bars if start <= bar.bar_time <= requested_end]
    if not bars:
        raise RuntimeError("No eligible price bars in the requested period")
    end = bars[-1].bar_time
    events, event_stats = load_events_strict(event_path)
    h_events, h_without_price = h_base.load_continuous_h(h_export_path, h_live_path)
    initial_positions = inferred_start_positions(events, start)
    ef_actions, missing_ef = map_ef_actions(events, all_bars, start, end)
    h_actions, missing_h = map_h_actions(
        h_events, all_bars, start, end, args.h_fill
    )
    ef_actions.sort(key=lambda action: (action.timestamp, action.priority, action.sequence))
    actions = sorted(
        ef_actions + h_actions,
        key=lambda action: (action.timestamp, action.priority, action.sequence),
    )

    baseline = run_baseline(
        bars=bars,
        initial_positions=initial_positions,
        ef_actions=ef_actions,
        one_way_cost=args.one_way_cost,
    )
    results = [
        run_h3_exit(
            name=f"h3_exit_{policy}_t{threshold}",
            threshold=threshold,
            reentry_policy=policy,
            bars=bars,
            initial_positions=initial_positions,
            initial_h=initial_h_position(h_events, start),
            actions=actions,
            one_way_cost=args.one_way_cost,
            opening_window_minutes=args.opening_window_minutes,
            min_add_streak=args.min_add_streak,
            require_wave_peak=args.require_wave_peak,
            max_minutes_after_add=args.max_minutes_after_add,
        )
        for policy in ("native", "h_align", "below_threshold")
        for threshold in range(args.threshold_min, args.threshold_max + 1)
    ]

    print(
        f"period={start:%Y-%m-%d %H:%M:%S}..{end:%Y-%m-%d %H:%M:%S} "
        "ef_event_time=received_at ef_fill=strict_next_1m_open "
        f"h_fill={args.h_fill} point_value_twd=10 "
        f"one_way_cost={args.one_way_cost:.1f}_points"
    )
    print(
        "rule=arm_on_abs_ef_net_threshold_crossing; "
        "trigger_on_later_H3_true_reversal_opposite_to_EF; "
        "execution=flatten_all_actual_EF_books; one_trigger_per_high_position_wave "
        f"opening_window_minutes={args.opening_window_minutes} "
        f"min_add_streak={args.min_add_streak} "
        f"require_wave_peak={args.require_wave_peak} "
        f"max_minutes_after_add={args.max_minutes_after_add}"
    )
    print(
        f"data bars={len(bars)} csv_rows={event_stats.csv_rows} "
        f"ef_events={event_stats.loaded} missing_received={event_stats.missing_received_at} "
        f"invalid={event_stats.invalid} duplicates={event_stats.duplicates} "
        f"missing_ef_fills={missing_ef} h_actions_in_window={len(h_actions)} "
        f"h_without_recorded_price={h_without_price} missing_h_fills={missing_h} "
        f"h_last={h_events[-1].timestamp:%Y-%m-%d %H:%M:%S}"
    )
    print(
        "strategy threshold triggers profitable_exits exit_qty exit_pnl "
        "gross_profit gross_loss PF closed_legs "
        "realized unrealized costs net_total delta_vs_base MDD mdd_improvement "
        "turnover ending_net ending_gross suppressed_ef"
    )
    ordered = [baseline] + results
    for result in ordered:
        portfolio = result.portfolio
        unrealized = portfolio.unrealized(bars[-1].close)
        print(
            result.name,
            "-" if result.threshold is None else result.threshold,
            len(result.triggers),
            sum(trigger.exit_pnl > 0 for trigger in result.triggers),
            sum(trigger.exited_quantity for trigger in result.triggers),
            f"{sum(trigger.exit_pnl for trigger in result.triggers):.1f}",
            f"{result.gross_profit:.1f}",
            f"{result.gross_loss:.1f}",
            format_pf(result.profit_factor),
            len(portfolio.closed_pnls),
            f"{portfolio.realized:.1f}",
            f"{unrealized:.1f}",
            f"{portfolio.costs:.1f}",
            f"{result.total:.1f}",
            f"{result.total - baseline.total:.1f}",
            f"{result.max_drawdown:.1f}",
            f"{baseline.max_drawdown - result.max_drawdown:.1f}",
            portfolio.turnover,
            portfolio.position,
            portfolio.gross_position,
            result.suppressed_ef_events,
        )

    months = list(monthly_deltas(bars, baseline.equities))
    print("monthly_net strategy " + " ".join(months))
    for result in ordered:
        monthly = monthly_deltas(bars, result.equities)
        print(
            "monthly_net",
            result.name,
            *(f"{monthly.get(month, 0.0):.1f}" for month in months),
        )

    if args.show_triggers:
        print(
            "trigger strategy time price threshold ef_net ef_gross h_position "
            "exited_qty exit_pnl"
        )
        for result in results:
            for trigger in result.triggers:
                print(
                    "trigger",
                    result.name,
                    f"{trigger.timestamp:%Y-%m-%d_%H:%M:%S}",
                    f"{trigger.price:.0f}",
                    trigger.threshold,
                    trigger.ef_net,
                    trigger.ef_gross,
                    trigger.h_position,
                    trigger.exited_quantity,
                    f"{trigger.exit_pnl:.1f}",
                )


if __name__ == "__main__":
    main()
