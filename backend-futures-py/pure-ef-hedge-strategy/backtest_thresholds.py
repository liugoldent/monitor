from __future__ import annotations

import argparse
import bisect
import csv
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import backtest_five_strategies_next_open as base  # noqa: E402


@dataclass
class Book:
    position: int = 0
    average_price: float = 0.0
    realized: float = 0.0
    turnover: int = 0
    costs: float = 0.0
    closed_pnls: list[float] = field(default_factory=list)

    def initialize(self, position: int, price: float) -> None:
        self.position = position
        self.average_price = price if position else 0.0

    def trade_to(self, target: int, price: float, one_way_cost: float) -> None:
        previous = self.position
        if target == previous:
            return
        quantity = abs(target - previous)
        self.turnover += quantity
        self.costs += quantity * one_way_cost

        if previous == 0:
            self.position = target
            self.average_price = price
            return

        previous_sign = sign(previous)
        target_sign = sign(target)
        if target_sign == previous_sign and abs(target) > abs(previous):
            added = abs(target) - abs(previous)
            self.average_price = (
                self.average_price * abs(previous) + price * added
            ) / abs(target)
            self.position = target
            return

        closed = abs(previous) if target_sign != previous_sign else abs(previous) - abs(target)
        pnl = (price - self.average_price) * previous_sign * closed
        self.realized += pnl
        self.closed_pnls.append(pnl)
        self.position = target
        if target == 0:
            self.average_price = 0.0
        elif target_sign != previous_sign:
            self.average_price = price

    def unrealized(self, price: float) -> float:
        return (price - self.average_price) * self.position if self.position else 0.0

    def gross_equity(self, price: float) -> float:
        return self.realized + self.unrealized(price)

    def net_equity(self, price: float) -> float:
        return self.gross_equity(price) - self.costs


@dataclass(frozen=True)
class EventStats:
    csv_rows: int
    loaded: int
    missing_received_at: int
    invalid: int
    duplicates: int


@dataclass
class SourceReplay:
    bars: list[base.PriceBar]
    nets: list[int]
    equities: list[float]
    books: dict[str, Book]
    start_net: int
    max_abs_net: int

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

    def unrealized(self) -> float:
        final_price = self.bars[-1].close
        return sum(book.unrealized(final_price) for book in self.books.values())


@dataclass
class Layer:
    entry_price: float
    favorable_peak: float
    peak_index: int


@dataclass
class MarginalLayer:
    level: int
    direction: int
    entry_time: datetime
    entry_price: float
    include: bool = True
    mfe: float = 0.0
    mae: float = 0.0


@dataclass(frozen=True)
class LayerOutcome:
    level: int
    entry_time: datetime
    exit_time: datetime
    pnl: float
    mfe: float
    mae: float
    marked_at_end: bool


@dataclass(frozen=True)
class TriggerConfig:
    loss_points: int
    retrace_points: int
    no_new_high_bars: int
    step_points: int = 50
    max_hedge: int = 3
    max_ratio: float = 0.5

    @property
    def name(self) -> str:
        return (
            f"loss{self.loss_points}_retrace{self.retrace_points}_"
            f"wait{self.no_new_high_bars}"
        )


@dataclass
class HedgeResult:
    threshold: int
    config: TriggerConfig
    total: float
    max_drawdown: float
    hedge_total: float
    hedge_max_drawdown: float
    hedge_turnover: int
    hedge_increases: int
    late_additions: int
    ending_hedge: int
    monthly: dict[str, float]
    closed_pnls: list[float]
    realized: float
    unrealized: float
    costs: float


def sign(value: int | float) -> int:
    return (value > 0) - (value < 0)


def parse_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), base.TIME_FORMAT)


def load_events_strict(path: Path) -> tuple[list[base.EfEvent], EventStats]:
    events: list[base.EfEvent] = []
    missing = 0
    invalid = 0
    rows = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=1):
            rows += 1
            received_at = str(row.get("received_at") or "").strip()
            if not received_at:
                missing += 1
                continue
            try:
                code = str(row.get("strategy_code") or "").strip()
                event = base.EfEvent(
                    row_number=row_number,
                    timestamp=parse_time(received_at),
                    strategy_code=code,
                    previous_position=int(float(row["previous_position"])),
                    new_position=int(float(row["new_position"])),
                )
            except (KeyError, TypeError, ValueError):
                invalid += 1
                continue
            if code not in base.ALL_STRATEGIES:
                invalid += 1
                continue
            events.append(event)

    ordered = sorted(events, key=lambda event: (event.timestamp, event.row_number))
    deduplicated: list[base.EfEvent] = []
    seen: set[tuple[datetime, str, int, int]] = set()
    duplicates = 0
    for event in ordered:
        key = (
            event.timestamp,
            event.strategy_code,
            event.previous_position,
            event.new_position,
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduplicated.append(event)
    return deduplicated, EventStats(rows, len(deduplicated), missing, invalid, duplicates)


def inferred_start_positions(
    events: list[base.EfEvent], start: datetime
) -> dict[str, int]:
    positions: dict[str, int] = {}
    for event in events:
        positions.setdefault(event.strategy_code, event.previous_position)
        if event.timestamp < start:
            positions[event.strategy_code] = event.new_position
    return positions


def map_event_fills(
    events: list[base.EfEvent], bars: list[base.PriceBar], start: datetime, end: datetime
) -> tuple[dict[datetime, list[base.EfEvent]], int]:
    times = [bar.bar_time for bar in bars]
    fills: dict[datetime, list[base.EfEvent]] = defaultdict(list)
    missing = 0
    for event in events:
        if event.timestamp < start or event.timestamp > end:
            continue
        target = event.timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        index = bisect.bisect_left(times, target)
        if index >= len(bars) or bars[index].bar_time > end:
            missing += 1
            continue
        fills[bars[index].bar_time].append(event)
    return fills, missing


def replay_source(
    *,
    all_bars: list[base.PriceBar],
    events: list[base.EfEvent],
    start: datetime,
    end: datetime,
    one_way_cost: float,
) -> tuple[SourceReplay, int]:
    bars = [bar for bar in all_bars if start <= bar.bar_time <= end]
    if not bars:
        raise RuntimeError("No eligible price bars in the requested period")
    start_positions = inferred_start_positions(events, start)
    books = {code: Book() for code in base.ALL_STRATEGIES}
    for code, book in books.items():
        book.initialize(start_positions.get(code, 0), bars[0].open)

    fills, missing_fills = map_event_fills(events, all_bars, start, end)
    nets: list[int] = []
    equities: list[float] = []
    max_abs_net = abs(sum(book.position for book in books.values()))
    for bar in bars:
        for event in fills.get(bar.bar_time, []):
            books[event.strategy_code].trade_to(
                event.new_position, bar.open, one_way_cost
            )
        net = sum(book.position for book in books.values())
        equity = sum(book.net_equity(bar.close) for book in books.values())
        nets.append(net)
        equities.append(equity)
        max_abs_net = max(max_abs_net, abs(net))
    return (
        SourceReplay(
            bars=bars,
            nets=nets,
            equities=equities,
            books=books,
            start_net=sum(start_positions.values()),
            max_abs_net=max_abs_net,
        ),
        missing_fills,
    )


def drawdown(equities: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for equity in equities:
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def monthly_returns(
    bars: list[base.PriceBar], equities: list[float]
) -> dict[str, float]:
    last_by_month: dict[str, float] = {}
    for bar, equity in zip(bars, equities):
        last_by_month[bar.bar_time.strftime("%Y-%m")] = equity
    results: dict[str, float] = {}
    previous = 0.0
    for month, equity in last_by_month.items():
        results[month] = equity - previous
        previous = equity
    return results


def analyze_marginal_layers(source: SourceReplay) -> list[LayerOutcome]:
    layers = [
        MarginalLayer(
            level=level,
            direction=sign(source.start_net),
            entry_time=source.bars[0].bar_time,
            entry_price=source.bars[0].open,
            include=False,
        )
        for level in range(1, abs(source.start_net) + 1)
    ]
    outcomes: list[LayerOutcome] = []
    previous_net = source.start_net

    def close_layers(count: int, timestamp: datetime, price: float) -> None:
        for _ in range(min(count, len(layers))):
            layer = layers.pop()
            if layer.include:
                outcomes.append(
                    LayerOutcome(
                        level=layer.level,
                        entry_time=layer.entry_time,
                        exit_time=timestamp,
                        pnl=layer.direction * (price - layer.entry_price),
                        mfe=layer.mfe,
                        mae=layer.mae,
                        marked_at_end=False,
                    )
                )

    for bar, net in zip(source.bars, source.nets):
        old_direction = sign(previous_net)
        new_direction = sign(net)
        old_abs = abs(previous_net)
        new_abs = abs(net)
        if new_direction != old_direction:
            close_layers(len(layers), bar.bar_time, bar.open)
            for level in range(1, new_abs + 1):
                layers.append(
                    MarginalLayer(level, new_direction, bar.bar_time, bar.open)
                )
        elif new_abs < old_abs:
            close_layers(old_abs - new_abs, bar.bar_time, bar.open)
        elif new_abs > old_abs:
            for level in range(old_abs + 1, new_abs + 1):
                layers.append(
                    MarginalLayer(level, new_direction, bar.bar_time, bar.open)
                )

        for layer in layers:
            excursion = layer.direction * (bar.close - layer.entry_price)
            layer.mfe = max(layer.mfe, excursion)
            layer.mae = max(layer.mae, -excursion)
        previous_net = net

    final_bar = source.bars[-1]
    for layer in layers:
        if layer.include:
            outcomes.append(
                LayerOutcome(
                    level=layer.level,
                    entry_time=layer.entry_time,
                    exit_time=final_bar.bar_time,
                    pnl=layer.direction * (final_bar.close - layer.entry_price),
                    mfe=layer.mfe,
                    mae=layer.mae,
                    marked_at_end=True,
                )
            )
    return outcomes


def simulate_hedge(
    *,
    source: SourceReplay,
    threshold: int,
    config: TriggerConfig,
    one_way_cost: float,
) -> HedgeResult:
    hedge = Book()
    layers: list[Layer] = []
    previous_net = source.start_net
    pending_quantity = 0
    pending_direction = 0
    combined_equities: list[float] = []
    hedge_equities: list[float] = []
    late_additions = 0
    hedge_increases = 0

    for index, (bar, net, source_equity) in enumerate(
        zip(source.bars, source.nets, source.equities)
    ):
        previous_direction = sign(previous_net)
        direction = sign(net)
        previous_abs = abs(previous_net)
        current_abs = abs(net)

        if direction == 0 or direction != previous_direction:
            layers = []
            if direction:
                for level in range(1, current_abs + 1):
                    if level >= threshold:
                        layers.append(Layer(bar.open, bar.open, index))
                        late_additions += 1
        elif current_abs > previous_abs:
            for level in range(previous_abs + 1, current_abs + 1):
                if level >= threshold:
                    layers.append(Layer(bar.open, bar.open, index))
                    late_additions += 1
        elif current_abs < previous_abs:
            required = max(0, current_abs - threshold + 1)
            layers = layers[:required]

        ratio_cap = math.floor(current_abs * config.max_ratio)
        cap = min(len(layers), config.max_hedge, ratio_cap)
        executable_quantity = (
            min(pending_quantity, cap) if pending_direction == direction else 0
        )
        target = -direction * executable_quantity
        if abs(target) > abs(hedge.position):
            hedge_increases += abs(target) - abs(hedge.position)
        hedge.trade_to(target, bar.open, one_way_cost)

        if not layers or direction == 0:
            pending_quantity = 0
            pending_direction = direction
        else:
            latest_peak_before_close = layers[-1].favorable_peak
            made_new_high = direction * (bar.close - latest_peak_before_close) > 0
            for layer in layers:
                favorable = direction * (bar.close - layer.favorable_peak)
                if favorable > 0:
                    layer.favorable_peak = bar.close
                    layer.peak_index = index

            latest = layers[-1]
            adverse = max(0.0, -direction * (bar.close - latest.entry_price))
            retrace = max(0.0, direction * (latest.favorable_peak - bar.close))
            bars_without_high = index - latest.peak_index

            loss_stage = 0
            if adverse >= config.loss_points:
                loss_stage = 1 + int((adverse - config.loss_points) // config.step_points)
            retrace_stage = 0
            if (
                retrace >= config.retrace_points
                and bars_without_high >= config.no_new_high_bars
            ):
                retrace_stage = 1 + int(
                    (retrace - config.retrace_points) // config.step_points
                )
            risk_stage = min(cap, max(loss_stage, retrace_stage))
            held_stage = min(cap, max(abs(hedge.position), pending_quantity))
            # Once protection starts, keep it through small rebounds.  Release it
            # only after price actually resumes in the EF direction and makes a
            # new post-addition high, or when source exposure reduces the cap.
            pending_quantity = 0 if made_new_high and held_stage else max(
                held_stage, risk_stage
            )
            pending_direction = direction

        hedge_equity = hedge.net_equity(bar.close)
        hedge_equities.append(hedge_equity)
        combined_equities.append(source_equity + hedge_equity)
        previous_net = net

    source_closed = source.closed_pnls
    combined_closed = source_closed + hedge.closed_pnls
    final_price = source.bars[-1].close
    realized = source.realized + hedge.realized
    unrealized = source.unrealized() + hedge.unrealized(final_price)
    costs = source.costs + hedge.costs
    gross_profit = sum(pnl for pnl in combined_closed if pnl > 0)
    gross_loss = -sum(pnl for pnl in combined_closed if pnl < 0)
    assert abs((gross_profit - gross_loss) - realized) < 1e-6
    assert abs((realized + unrealized - costs) - combined_equities[-1]) < 1e-6
    assert abs(
        (hedge.realized + hedge.unrealized(final_price) - hedge.costs)
        - hedge_equities[-1]
    ) < 1e-6
    return HedgeResult(
        threshold=threshold,
        config=config,
        total=combined_equities[-1],
        max_drawdown=drawdown(combined_equities),
        hedge_total=hedge_equities[-1],
        hedge_max_drawdown=drawdown(hedge_equities),
        hedge_turnover=hedge.turnover,
        hedge_increases=hedge_increases,
        late_additions=late_additions,
        ending_hedge=hedge.position,
        monthly=monthly_returns(source.bars, combined_equities),
        closed_pnls=combined_closed,
        realized=realized,
        unrealized=unrealized,
        costs=costs,
    )


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay progressive pure-EF hedge and compare position thresholds"
    )
    parser.add_argument("--start", default="2026-06-24 00:00:00")
    parser.add_argument("--end", required=True)
    parser.add_argument("--one-way-cost", type=float, default=2.4)
    parser.add_argument("--threshold-min", type=int, default=3)
    parser.add_argument("--threshold-max", type=int, default=10)
    args = parser.parse_args()

    backend = REPO_ROOT / "backend-futures-py"
    price_path = backend / "tv_doc" / "webhook_data_1min.csv"
    event_path = backend / "tv_doc" / "six_strategy_signal_events.csv"
    start = parse_time(args.start)
    end = parse_time(args.end)
    all_bars = base.load_prices(price_path)
    events, event_stats = load_events_strict(event_path)
    source, missing_fills = replay_source(
        all_bars=all_bars,
        events=events,
        start=start,
        end=end,
        one_way_cost=args.one_way_cost,
    )

    base_total = source.equities[-1]
    base_drawdown = drawdown(source.equities)
    base_monthly = monthly_returns(source.bars, source.equities)
    base_gross_profit = sum(pnl for pnl in source.closed_pnls if pnl > 0)
    base_gross_loss = -sum(pnl for pnl in source.closed_pnls if pnl < 0)
    base_pf = (
        base_gross_profit / base_gross_loss if base_gross_loss else float("inf")
    )
    layer_outcomes = analyze_marginal_layers(source)

    configs = [
        TriggerConfig(loss, retrace, wait)
        for loss in (20, 30, 50)
        for retrace in (30, 50, 80)
        for wait in (0, 5, 10)
    ]
    results = [
        simulate_hedge(
            source=source,
            threshold=threshold,
            config=config,
            one_way_cost=args.one_way_cost,
        )
        for threshold in range(args.threshold_min, args.threshold_max + 1)
        for config in configs
    ]

    print(
        f"period={start:%Y-%m-%d %H:%M:%S}..{end:%Y-%m-%d %H:%M:%S} "
        "event_time=received_at fill=strict_next_1m_open decision=1m_close "
        f"one_way_cost={args.one_way_cost:.1f}_points"
    )
    print(
        "data "
        f"bars={len(source.bars)} csv_rows={event_stats.csv_rows} "
        f"events={event_stats.loaded} missing_received={event_stats.missing_received_at} "
        f"invalid={event_stats.invalid} duplicates={event_stats.duplicates} "
        f"missing_fills={missing_fills} max_abs_ef_net={source.max_abs_net}"
    )
    print(
        "baseline "
        f"net={base_total:.1f} mdd={base_drawdown:.1f} "
        f"gross_profit={base_gross_profit:.1f} gross_loss={base_gross_loss:.1f} "
        f"pf={base_pf:.3f} closed_legs={len(source.closed_pnls)} "
        f"realized={source.realized:.1f} unrealized={source.unrealized():.1f} "
        f"costs={source.costs:.1f} turnover={source.turnover} "
        f"ending_position={source.nets[-1]}"
    )
    print("baseline_monthly " + " ".join(
        f"{month}={value:.1f}" for month, value in base_monthly.items()
    ))
    print(
        "marginal_level samples marked mean_pnl median_pnl win_rate p25_pnl "
        "mean_mfe mean_mae mean_giveback"
    )
    by_level: dict[int, list[LayerOutcome]] = defaultdict(list)
    for outcome in layer_outcomes:
        by_level[outcome.level].append(outcome)
    for level in sorted(by_level):
        group = by_level[level]
        pnls = [item.pnl for item in group]
        print(
            level,
            len(group),
            sum(item.marked_at_end for item in group),
            f"{statistics.mean(pnls):.1f}",
            f"{statistics.median(pnls):.1f}",
            f"{sum(pnl > 0 for pnl in pnls) / len(pnls):.1%}",
            f"{percentile(pnls, 0.25):.1f}",
            f"{statistics.mean(item.mfe for item in group):.1f}",
            f"{statistics.mean(item.mae for item in group):.1f}",
            f"{statistics.mean(item.mfe - item.pnl for item in group):.1f}",
        )
    print(
        "marginal_threshold samples mean_pnl win_rate total_pnl mean_mae "
        "mean_giveback"
    )
    for threshold in range(args.threshold_min, args.threshold_max + 1):
        group = [item for item in layer_outcomes if item.level >= threshold]
        if not group:
            continue
        pnls = [item.pnl for item in group]
        print(
            threshold,
            len(group),
            f"{statistics.mean(pnls):.1f}",
            f"{sum(pnl > 0 for pnl in pnls) / len(pnls):.1%}",
            f"{sum(pnls):.1f}",
            f"{statistics.mean(item.mae for item in group):.1f}",
            f"{statistics.mean(item.mfe - item.pnl for item in group):.1f}",
        )
    print("marginal_threshold_month threshold month samples mean_pnl win_rate total_pnl")
    for threshold in (4, 5, 6):
        threshold_group = [item for item in layer_outcomes if item.level >= threshold]
        month_groups: dict[str, list[LayerOutcome]] = defaultdict(list)
        for item in threshold_group:
            month_groups[item.entry_time.strftime("%Y-%m")].append(item)
        for month, group in sorted(month_groups.items()):
            pnls = [item.pnl for item in group]
            print(
                threshold,
                month,
                len(group),
                f"{statistics.mean(pnls):.1f}",
                f"{sum(pnl > 0 for pnl in pnls) / len(pnls):.1%}",
                f"{sum(pnls):.1f}",
            )
    print(
        "threshold variants median_net_delta p25_net_delta median_mdd_improvement "
        "mdd_improve_rate median_hedge_turnover median_late_additions "
        "jul_delta aug_delta sep_delta"
    )
    grouped: dict[int, list[HedgeResult]] = defaultdict(list)
    for result in results:
        grouped[result.threshold].append(result)
    for threshold in sorted(grouped):
        group = grouped[threshold]
        net_deltas = [result.total - base_total for result in group]
        mdd_improvements = [base_drawdown - result.max_drawdown for result in group]
        monthly_delta = {
            month: statistics.median(
                result.monthly.get(month, 0.0) - base_monthly.get(month, 0.0)
                for result in group
            )
            for month in ("2026-07", "2026-08", "2026-09")
        }
        print(
            threshold,
            len(group),
            f"{statistics.median(net_deltas):.1f}",
            f"{percentile(net_deltas, 0.25):.1f}",
            f"{statistics.median(mdd_improvements):.1f}",
            f"{sum(value > 0 for value in mdd_improvements) / len(group):.1%}",
            f"{statistics.median(result.hedge_turnover for result in group):.0f}",
            f"{statistics.median(result.late_additions for result in group):.0f}",
            f"{monthly_delta['2026-07']:.1f}",
            f"{monthly_delta['2026-08']:.1f}",
            f"{monthly_delta['2026-09']:.1f}",
        )

    print(
        "top_stable threshold config net mdd mdd_improvement hedge_net "
        "hedge_mdd turnover triggers late_additions jul aug sep pf closed realized unrealized costs"
    )
    eligible = [result for result in results if result.hedge_increases >= 5]
    ranked = sorted(
        eligible,
        key=lambda result: (
            result.max_drawdown,
            -result.total,
            result.hedge_turnover,
        ),
    )[:12]
    for result in ranked:
        gross_profit = sum(pnl for pnl in result.closed_pnls if pnl > 0)
        gross_loss = -sum(pnl for pnl in result.closed_pnls if pnl < 0)
        pf = gross_profit / gross_loss if gross_loss else float("inf")
        print(
            result.threshold,
            result.config.name,
            f"{result.total:.1f}",
            f"{result.max_drawdown:.1f}",
            f"{base_drawdown - result.max_drawdown:.1f}",
            f"{result.hedge_total:.1f}",
            f"{result.hedge_max_drawdown:.1f}",
            result.hedge_turnover,
            result.hedge_increases,
            result.late_additions,
            f"{result.monthly.get('2026-07', 0.0):.1f}",
            f"{result.monthly.get('2026-08', 0.0):.1f}",
            f"{result.monthly.get('2026-09', 0.0):.1f}",
            f"{pf:.3f}",
            len(result.closed_pnls),
            f"{result.realized:.1f}",
            f"{result.unrealized:.1f}",
            f"{result.costs:.1f}",
        )


if __name__ == "__main__":
    main()
