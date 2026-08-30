from __future__ import annotations

import argparse
import bisect
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

import backtest_five_strategies_next_open as base


@dataclass(frozen=True)
class Action:
    timestamp: datetime
    delta: int
    price: float
    gross_after: int
    one_way: int


@dataclass(frozen=True)
class RiskResult:
    name: str
    total: float
    max_drawdown: float
    one_way: int
    max_gross: int
    max_abs_net: int
    end_position: int

    @property
    def return_over_drawdown(self) -> float:
        return float("inf") if self.max_drawdown == 0 else self.total / self.max_drawdown


class BarLookup:
    def __init__(self, bars: list[base.PriceBar], *, fill_mode: str = "strict-next") -> None:
        self.bars = bars
        self.times = [bar.bar_time for bar in bars]
        self.minute_offset = 1 if fill_mode == "strict-next" else 0
        self.fill_mode = fill_mode
        recorded = sorted(bars, key=lambda bar: (bar.record_time, bar.bar_time))
        self.record_times = [bar.record_time for bar in recorded]
        self.visible_latest: list[base.PriceBar] = []
        latest: base.PriceBar | None = None
        for bar in recorded:
            if latest is None or bar.bar_time > latest.bar_time:
                latest = bar
            self.visible_latest.append(latest)

    def next_open_bar(self, timestamp: datetime) -> base.PriceBar | None:
        if self.fill_mode == "visible-next":
            record_index = bisect.bisect_right(self.record_times, timestamp) - 1
            if record_index < 0:
                return None
            visible = self.visible_latest[record_index]
            index = bisect.bisect_right(self.times, visible.bar_time)
            return None if index >= len(self.bars) else self.bars[index]
        target = timestamp.replace(second=0, microsecond=0) + timedelta(
            minutes=self.minute_offset
        )
        index = bisect.bisect_left(self.times, target)
        return None if index >= len(self.bars) else self.bars[index]

    def first_at_or_after(self, timestamp: datetime) -> base.PriceBar | None:
        index = bisect.bisect_left(self.times, timestamp)
        return None if index >= len(self.bars) else self.bars[index]


def append_action(
    actions: list[Action],
    *,
    timestamp: datetime,
    previous: int,
    target: int,
    price: float,
    gross_after: int,
    one_way: int | None = None,
) -> None:
    delta = target - previous
    quantity = abs(delta) if one_way is None else one_way
    if delta != 0 or quantity:
        actions.append(
            Action(
                timestamp,
                delta,
                price,
                gross_after,
                quantity,
            )
        )


def evaluate(
    *,
    name: str,
    bars: list[base.PriceBar],
    actions: list[Action],
    start: datetime,
    end: datetime,
    start_price: float,
    start_position: int,
    start_gross: int,
) -> RiskResult:
    cash = -start_position * start_price * 10
    position = start_position
    peak = 0.0
    max_drawdown = 0.0
    max_gross = start_gross
    max_abs_net = abs(start_position)
    one_way = 0
    timeline: list[tuple[datetime, int, str, float | Action]] = []
    for action in actions:
        if start <= action.timestamp <= end:
            timeline.append((action.timestamp, 0, "action", action))
    for bar in bars:
        if start <= bar.bar_time <= end:
            timeline.append((bar.bar_time + timedelta(minutes=1), 1, "mark", bar.close))

    equity = 0.0
    for _, _, kind, value in sorted(timeline, key=lambda item: (item[0], item[1])):
        if kind == "action":
            assert isinstance(value, Action)
            # Mark the old position at the executable price before changing it.
            equity = cash + position * value.price * 10
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            cash -= value.delta * value.price * 10
            position += value.delta
            max_abs_net = max(max_abs_net, abs(position))
            one_way += value.one_way
            max_gross = max(max_gross, value.gross_after)
        else:
            price = float(value)
            equity = cash + position * price * 10
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
    return RiskResult(
        name, equity, max_drawdown, one_way, max_gross, max_abs_net, position
    )


def ef_state_before(events: list[base.EfEvent], start: datetime) -> dict[str, int]:
    return base.state_before_ef(events, start)


def pure_h_actions(
    h_events: list[base.HEvent], lookup: BarLookup, start: datetime, end: datetime
) -> tuple[int, int, list[Action]]:
    h_position = 0
    for event in h_events:
        if event.timestamp >= start:
            break
        h_position = event.new_position
    position = h_position * 2
    actions: list[Action] = []
    for event in h_events:
        if event.timestamp < start:
            continue
        if event.timestamp > end:
            break
        bar = lookup.next_open_bar(event.timestamp)
        price = event.exact_price if event.exact_price is not None else (None if bar is None else bar.open)
        if price is None:
            continue
        target = event.new_position * 2
        append_action(
            actions,
            timestamp=event.timestamp if event.exact_price is not None else bar.bar_time,
            previous=position,
            target=target,
            price=price,
            gross_after=abs(target),
        )
        position = target
    return h_position * 2, 2 if h_position else 0, actions


def separate_ef_actions(
    *,
    events: list[base.EfEvent],
    lookup: BarLookup,
    start: datetime,
    end: datetime,
    targets: dict[int, int] | None = None,
) -> tuple[int, int, list[Action]]:
    positions: dict[str, int] = {}
    for event in events:
        if event.timestamp >= start:
            break
        positions[event.strategy_code] = (
            event.new_position if targets is None else targets[event.row_number]
        )
    start_position = sum(positions.values())
    start_gross = sum(abs(value) for value in positions.values())
    actions: list[Action] = []
    for event in events:
        if event.timestamp < start:
            continue
        if event.timestamp > end:
            break
        bar = lookup.next_open_bar(event.timestamp)
        if bar is None or bar.bar_time > end:
            continue
        previous = positions.get(event.strategy_code, 0)
        target = event.new_position if targets is None else targets[event.row_number]
        positions[event.strategy_code] = target
        append_action(
            actions,
            timestamp=bar.bar_time,
            previous=previous,
            target=target,
            price=bar.open,
            gross_after=sum(abs(value) for value in positions.values()),
        )
    return start_position, start_gross, actions


def target_strategy_actions(
    *,
    updates: list[tuple[datetime, str, object]],
    state: dict[str, object],
    update_state,
    get_target,
    get_price,
    start: datetime,
    end: datetime,
) -> tuple[int, int, list[Action]]:
    for timestamp, kind, payload in updates:
        if timestamp >= start:
            break
        update_state(state, kind, payload)
    target = int(get_target(state))
    initial = target
    actions: list[Action] = []
    for timestamp, kind, payload in updates:
        if timestamp < start:
            continue
        if timestamp > end:
            break
        priced = get_price(timestamp, kind, payload)
        if priced is None:
            continue
        execution_time, price = priced
        if execution_time > end:
            continue
        update_state(state, kind, payload)
        new_target = int(get_target(state))
        append_action(
            actions,
            timestamp=execution_time,
            previous=target,
            target=new_target,
            price=price,
            gross_after=abs(new_target),
        )
        target = new_target
    return initial, abs(initial), actions


def morning_session_key(timestamp: datetime) -> tuple[datetime.date, str] | None:
    clock = timestamp.time()
    if time(8, 45) <= clock < time(13, 45):
        return timestamp.date(), "day"
    if clock >= time(15, 0):
        return timestamp.date(), "night"
    if clock < time(5, 0):
        return (timestamp - timedelta(days=1)).date(), "night"
    return None


def morning_flat_actions(
    events: list[base.EfEvent],
    bars: list[base.PriceBar],
    lookup: BarLookup,
    start: datetime,
    end: datetime,
) -> tuple[int, int, list[Action]]:
    positions = ef_state_before(events, start)
    start_position = sum(positions.values())
    start_gross = sum(abs(value) for value in positions.values())
    scheduled: list[tuple[datetime, int, str, float, base.EfEvent | None]] = []
    sessions: dict[tuple[datetime.date, str], list[base.PriceBar]] = {}
    for bar in bars:
        if not start <= bar.bar_time <= end:
            continue
        key = morning_session_key(bar.bar_time)
        if key is not None:
            sessions.setdefault(key, []).append(bar)
    ordered = sorted(sessions.items(), key=lambda item: item[1][0].bar_time)
    for index, (key, group) in enumerate(ordered[:-1]):
        if key[1] != "night":
            continue
        last_bar = group[-1]
        scheduled_time = datetime.combine(last_bar.bar_time.date(), time(4, 59))
        scheduled.append((scheduled_time, 0, "flatten", last_bar.open, None))
    for event in events:
        if event.timestamp < start or event.timestamp > end:
            continue
        bar = lookup.next_open_bar(event.timestamp)
        if bar is not None and bar.bar_time <= end:
            scheduled.append((bar.bar_time, 1, "signal", bar.open, event))

    actions: list[Action] = []
    for timestamp, _, kind, price, event in sorted(scheduled, key=lambda item: (item[0], item[1])):
        if kind == "flatten":
            previous_net = sum(positions.values())
            flatten_quantity = sum(abs(value) for value in positions.values())
            positions = {code: 0 for code in base.ALL_STRATEGIES}
            append_action(
                actions,
                timestamp=timestamp,
                previous=previous_net,
                target=0,
                price=price,
                gross_after=0,
                one_way=flatten_quantity,
            )
            continue
        assert event is not None
        previous = positions.get(event.strategy_code, 0)
        target = event.new_position
        positions[event.strategy_code] = target
        append_action(
            actions,
            timestamp=timestamp,
            previous=previous,
            target=target,
            price=price,
            gross_after=sum(abs(value) for value in positions.values()),
        )
    return start_position, start_gross, actions


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare six strategy risk profiles")
    parser.add_argument("--start", default="2026-08-01 00:00:00")
    parser.add_argument("--end", default="")
    parser.add_argument(
        "--fill-mode",
        choices=("strict-next", "signal-minute", "visible-next"),
        default="strict-next",
    )
    parser.add_argument(
        "--event-time",
        choices=("message", "received"),
        default="received",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    backend = root / "backend-futures-py"
    price_path = backend / "tv_doc" / "webhook_data_1min.csv"
    bars = base.load_prices(price_path)
    events = base.load_ef_events(
        backend / "tv_doc" / "six_strategy_signal_events.csv",
        event_time=args.event_time,
    )
    h_events = base.load_h_events(
        backend / "h3-ef-012-strategy" / "records" / "h3_position_events.csv"
    )
    start = base.parse_time(args.start)
    end = base.parse_time(args.end) if args.end else bars[-1].bar_time
    bars = [bar for bar in bars if bar.bar_time <= end]
    lookup = BarLookup(bars, fill_mode=args.fill_mode)
    first_bar = lookup.first_at_or_after(start)
    if first_bar is None:
        raise RuntimeError("No start price")

    pure_h = pure_h_actions(h_events, lookup, start, end)
    pure_ef = separate_ef_actions(events=events, lookup=lookup, start=start, end=end)
    portfolios: list[tuple[str, int, int, list[Action]]] = [
        ("pure_h_2", *pure_h),
        ("pure_ef_raw_12", *pure_ef),
    ]

    def update_ef(state: dict[str, object], _kind: str, payload: object) -> None:
        assert isinstance(payload, base.EfEvent)
        state[payload.strategy_code] = payload.new_position

    def strong_target(state: dict[str, object]) -> int:
        e_net = sum(int(state.get(code, 0)) for code in base.PORTFOLIO_E)
        f_net = sum(int(state.get(code, 0)) for code in base.PORTFOLIO_F)
        if e_net >= 2 and f_net >= 2:
            return 1
        if e_net <= -2 and f_net <= -2:
            return -1
        return 0

    def ef_price(timestamp: datetime, _kind: str, _payload: object):
        bar = lookup.next_open_bar(timestamp)
        return None if bar is None else (bar.bar_time, bar.open)

    strong_updates = [(event.timestamp, "ef", event) for event in events]
    strong = target_strategy_actions(
        updates=strong_updates,
        state={},
        update_state=update_ef,
        get_target=strong_target,
        get_price=ef_price,
        start=start,
        end=end,
    )

    combined_state: dict[str, object] = {"h": 0, "ef": {}}
    combined_updates = sorted(
        [(event.timestamp, "ef", event) for event in events]
        + [(event.timestamp, "h", event) for event in h_events],
        key=lambda item: (item[0], item[1]),
    )

    def update_combined(state: dict[str, object], kind: str, payload: object) -> None:
        if kind == "h":
            assert isinstance(payload, base.HEvent)
            state["h"] = payload.new_position
        else:
            assert isinstance(payload, base.EfEvent)
            ef_positions = state["ef"]
            assert isinstance(ef_positions, dict)
            ef_positions[payload.strategy_code] = payload.new_position

    def combined_target(state: dict[str, object]) -> int:
        h_position = int(state["h"])
        ef_positions = state["ef"]
        assert isinstance(ef_positions, dict)
        ef_net = sum(int(ef_positions.get(code, 0)) for code in base.ALL_STRATEGIES)
        ef_direction = (ef_net > 0) - (ef_net < 0)
        if ef_direction == -h_position:
            return 0
        if ef_direction == h_position:
            return 2 * h_position
        return h_position

    def combined_price(timestamp: datetime, kind: str, payload: object):
        if kind == "h":
            assert isinstance(payload, base.HEvent)
            if payload.exact_price is not None:
                return timestamp, payload.exact_price
        return ef_price(timestamp, kind, payload)

    combined = target_strategy_actions(
        updates=combined_updates,
        state=combined_state,
        update_state=update_combined,
        get_target=combined_target,
        get_price=combined_price,
        start=start,
        end=end,
    )

    morning = morning_flat_actions(events, bars, lookup, start, end)

    portfolios.extend(
        [
            ("h3_ef_u1", *combined),
            ("ef_strong_threshold2", *strong),
            ("ef_0459_flat_wait", *morning),
        ]
    )
    print(
        f"period={start:%Y-%m-%d %H:%M:%S}..{end:%Y-%m-%d %H:%M:%S} "
        f"event_time={args.event_time} fill={args.fill_mode}_1m_open "
        "drawdown=1m_close_mark_to_market"
    )
    print(
        "strategy total_twd max_drawdown_twd return_over_dd one_way "
        "max_gross max_abs_net end_position"
    )
    for name, start_position, start_gross, actions in portfolios:
        result = evaluate(
            name=name,
            bars=bars,
            actions=actions,
            start=start,
            end=end,
            start_price=first_bar.open,
            start_position=start_position,
            start_gross=start_gross,
        )
        print(
            result.name,
            round(result.total),
            round(result.max_drawdown),
            round(result.return_over_drawdown, 2),
            result.one_way,
            result.max_gross,
            result.max_abs_net,
            result.end_position,
        )

    def combined_portfolio(name: str, *parts: tuple[int, int, list[Action]]):
        start_position = sum(part[0] for part in parts)
        start_gross = sum(part[1] for part in parts)
        actions = [action for part in parts for action in part[2]]
        return evaluate(
            name=name,
            bars=bars,
            actions=actions,
            start=start,
            end=end,
            start_price=first_bar.open,
            start_position=start_position,
            start_gross=start_gross,
        )

    print("portfolio total_twd max_drawdown_twd return_over_dd one_way")
    combinations = [
        combined_portfolio("base_h_plus_ef", pure_h, pure_ef),
        combined_portfolio("base_plus_h3ef", pure_h, pure_ef, combined),
        combined_portfolio("base_plus_strong", pure_h, pure_ef, strong),
        combined_portfolio("base_plus_0459", pure_h, pure_ef, morning),
        combined_portfolio("base_plus_h3ef_plus_0459", pure_h, pure_ef, combined, morning),
    ]
    for result in combinations:
        print(
            result.name,
            round(result.total),
            round(result.max_drawdown),
            round(result.return_over_drawdown, 2),
            result.one_way,
        )


if __name__ == "__main__":
    main()
