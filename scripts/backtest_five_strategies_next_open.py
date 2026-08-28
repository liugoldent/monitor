from __future__ import annotations

import argparse
import bisect
import csv
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
PORTFOLIO_E = (
    "CFC07m",
    "CFCTX17m",
    "CFCTX18m",
    "CFCTX19m",
    "CFCTX20m",
    "CFCTX21m",
)
PORTFOLIO_F = (
    "CFCWIN01m",
    "CFCPW3m",
    "CFCCPm",
    "CFCTX16m",
    "CFCTX22m",
    "CFCTX23m",
)
ALL_STRATEGIES = PORTFOLIO_E + PORTFOLIO_F


@dataclass(frozen=True)
class PriceBar:
    bar_time: datetime
    record_time: datetime
    open: float
    close: float


@dataclass(frozen=True)
class EfEvent:
    row_number: int
    timestamp: datetime
    strategy_code: str
    previous_position: int
    new_position: int


@dataclass(frozen=True)
class HEvent:
    timestamp: datetime
    previous_position: int | None
    new_position: int
    exact_price: float | None


@dataclass
class Result:
    name: str
    realized: float
    unrealized: float
    one_way: int
    ending_position: int
    closed_pnls: list[float] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.realized + self.unrealized

    @property
    def gross_profit(self) -> float:
        return sum(pnl for pnl in self.closed_pnls if pnl > 0)

    @property
    def gross_loss(self) -> float:
        return -sum(pnl for pnl in self.closed_pnls if pnl < 0)

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else float("inf")


def parse_time(value: str) -> datetime:
    return datetime.strptime(value.strip(), TIME_FORMAT)


def load_prices(path: Path) -> list[PriceBar]:
    bars: dict[datetime, PriceBar] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                bar = PriceBar(
                    bar_time=parse_time(row["TradingView Time"]),
                    record_time=parse_time(row["Record Time"]),
                    open=float(row["Open"].replace(",", "")),
                    close=float(row["Close"].replace(",", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            bars[bar.bar_time] = bar
    return [bars[key] for key in sorted(bars)]


def load_ef_events(path: Path, *, event_time: str = "received") -> list[EfEvent]:
    events: list[EfEvent] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=1):
            try:
                if event_time == "received":
                    timestamp_text = row.get("received_at") or row.get("message_time") or ""
                else:
                    timestamp_text = row.get("message_time") or row.get("received_at") or ""
                code = str(row.get("strategy_code") or "").strip()
                event = EfEvent(
                    row_number=row_number,
                    timestamp=parse_time(timestamp_text),
                    strategy_code=code,
                    previous_position=int(float(row["previous_position"])),
                    new_position=int(float(row["new_position"])),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if code in ALL_STRATEGIES:
                events.append(event)
    return sorted(events, key=lambda event: (event.timestamp, event.row_number))


def load_h_events(path: Path) -> list[HEvent]:
    events: list[HEvent] = []
    price_pattern = re.compile(r"成交價\s*(\d+(?:\.\d+)?)")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = parse_time(row["received_at"])
                previous_text = str(row.get("previous_position") or "").strip()
                previous = int(float(previous_text)) if previous_text else None
                new_position = int(float(row["new_position"]))
            except (KeyError, TypeError, ValueError):
                continue
            match = price_pattern.search(str(row.get("raw_message") or ""))
            exact_price = float(match.group(1)) if match else None
            events.append(HEvent(timestamp, previous, new_position, exact_price))
    return sorted(events, key=lambda event: event.timestamp)


def load_h_tradingview_events(path: Path) -> list[HEvent]:
    """Load the complete TradingView H3 trade export from its Entry rows."""
    events: list[HEvent] = []
    position = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            trade_type = str(row.get("類型") or "").strip()
            if trade_type not in {"Entry Long", "Entry Short"}:
                continue
            try:
                timestamp = datetime.strptime(
                    f"{row['日期'].strip()} {row['時間'].strip()}",
                    "%Y/%m/%d %H:%M:%S",
                )
                price = float(str(row["價格"]).replace(",", ""))
            except (KeyError, TypeError, ValueError):
                continue
            target = 1 if trade_type == "Entry Long" else -1
            events.append(HEvent(timestamp, position, target, price))
            position = target
    return sorted(events, key=lambda event: event.timestamp)


def load_h_source(path: Path) -> list[HEvent]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle), [])
    if "類型" in header and "日期" in header and "價格" in header:
        return load_h_tradingview_events(path)
    return load_h_events(path)


def load_h_reported_closed_pnls(
    path: Path, start: datetime, end: datetime
) -> tuple[list[float], int]:
    """Return TradingView's exit-date P&L figures and invalid exit-row count."""
    pnls: list[float] = []
    invalid = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if not str(row.get("類型") or "").strip().startswith("Exit"):
                continue
            try:
                timestamp = datetime.strptime(
                    f"{row['日期'].strip()} {row['時間'].strip()}",
                    "%Y/%m/%d %H:%M:%S",
                )
                pnl = float(str(row["獲利($)"]).replace(",", ""))
            except (KeyError, TypeError, ValueError):
                invalid += 1
                continue
            if start <= timestamp <= end:
                pnls.append(pnl)
    return pnls, invalid


class PriceLookup:
    def __init__(self, bars: list[PriceBar]) -> None:
        self.bars = bars
        self.times = [bar.bar_time for bar in bars]
        recorded = sorted(bars, key=lambda bar: (bar.record_time, bar.bar_time))
        self.record_times = [bar.record_time for bar in recorded]
        self.visible_latest: list[PriceBar] = []
        latest: PriceBar | None = None
        for bar in recorded:
            if latest is None or bar.bar_time > latest.bar_time:
                latest = bar
            self.visible_latest.append(latest)

    def open_at_or_after(self, timestamp: datetime) -> float | None:
        bar = self.bar_at_or_after(timestamp)
        return None if bar is None else bar.open

    def bar_at_or_after(self, timestamp: datetime) -> PriceBar | None:
        index = bisect.bisect_left(self.times, timestamp)
        return None if index >= len(self.bars) else self.bars[index]

    def next_minute_open(self, timestamp: datetime) -> float | None:
        minute = timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return self.open_at_or_after(minute)

    def signal_minute_open(self, timestamp: datetime) -> float | None:
        minute = timestamp.replace(second=0, microsecond=0)
        return self.open_at_or_after(minute)

    def visible_next_open(self, timestamp: datetime) -> float | None:
        """Open immediately after the latest 1m bar recorded by receipt time."""
        index = bisect.bisect_right(self.record_times, timestamp) - 1
        if index < 0:
            return None
        visible = self.visible_latest[index]
        next_index = bisect.bisect_right(self.times, visible.bar_time)
        return None if next_index >= len(self.bars) else self.bars[next_index].open


def state_before_ef(events: Iterable[EfEvent], start: datetime) -> dict[str, int]:
    positions: dict[str, int] = {}
    for event in events:
        if event.timestamp >= start:
            break
        positions[event.strategy_code] = event.new_position
    return positions


def transition_book(
    *,
    positions: dict[str, int],
    entries: dict[str, float],
    code: str,
    fallback_previous: int,
    target: int,
    price: float,
) -> tuple[float, int]:
    previous = positions.get(code, fallback_previous)
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


def backtest_separate_book(
    *,
    name: str,
    events: Iterable[EfEvent],
    filtered_targets: dict[int, int] | None,
    lookup: PriceLookup,
    start: datetime,
    end: datetime,
    start_price: float,
    end_price: float,
    execution_price: Callable[[datetime], float | None],
) -> Result:
    source_positions = state_before_ef(events, start)
    positions: dict[str, int] = {}
    if filtered_targets is None:
        positions.update(source_positions)
    else:
        for event in events:
            if event.timestamp >= start:
                break
            positions[event.strategy_code] = filtered_targets[event.row_number]
    entries = {code: start_price for code, position in positions.items() if position}
    realized = 0.0
    one_way = 0
    closed_pnls: list[float] = []
    for event in events:
        if event.timestamp < start:
            continue
        if event.timestamp > end:
            break
        price = execution_price(event.timestamp)
        if price is None:
            continue
        target = (
            event.new_position
            if filtered_targets is None
            else filtered_targets[event.row_number]
        )
        previous = positions.get(event.strategy_code, event.previous_position)
        closes_leg = previous != 0 and previous != target
        pnl, quantity = transition_book(
            positions=positions,
            entries=entries,
            code=event.strategy_code,
            fallback_previous=event.previous_position,
            target=target,
            price=price,
        )
        realized += pnl
        if closes_leg:
            closed_pnls.append(pnl)
        one_way += quantity
    unrealized = sum(
        (end_price - entries[code]) * position * 10
        for code, position in positions.items()
        if position and code in entries
    )
    return Result(name, realized, unrealized, one_way, sum(positions.values()), closed_pnls)


def session_key(timestamp: datetime) -> tuple[datetime.date, str] | None:
    clock = timestamp.time()
    if datetime.strptime("08:45", "%H:%M").time() <= clock < datetime.strptime(
        "13:45", "%H:%M"
    ).time():
        return timestamp.date(), "day"
    if clock >= datetime.strptime("15:00", "%H:%M").time():
        return timestamp.date(), "night"
    if clock < datetime.strptime("05:00", "%H:%M").time():
        return (timestamp - timedelta(days=1)).date(), "night"
    return None


def backtest_ef_break_policy(
    *,
    events: list[EfEvent],
    bars: list[PriceBar],
    lookup: PriceLookup,
    start: datetime,
    end: datetime,
    start_price: float,
    end_price: float,
    reopen: bool,
    break_scope: str = "all",
) -> Result:
    desired = state_before_ef(events, start)
    positions = dict(desired)
    entries = {code: start_price for code, position in positions.items() if position}
    actions: list[tuple[datetime, int, str, float, EfEvent | None]] = []

    sessions: dict[tuple[datetime.date, str], list[PriceBar]] = {}
    for bar in bars:
        if bar.bar_time < start or bar.bar_time > end:
            continue
        key = session_key(bar.bar_time)
        if key is not None:
            sessions.setdefault(key, []).append(bar)
    ordered_sessions = sorted(sessions.items(), key=lambda item: item[1][0].bar_time)
    for index, (key, group) in enumerate(ordered_sessions[:-1]):
        if break_scope != "all" and key[1] != break_scope:
            continue
        last_bar = group[-1]
        next_bar = ordered_sessions[index + 1][1][0]
        # The session's final close is only known when trading has ended. Use
        # the final one-minute bar's open as the executable scheduled exit proxy.
        actions.append((last_bar.bar_time, 0, "flatten", last_bar.open, None))
        actions.append((next_bar.bar_time, 1, "reopen", next_bar.open, None))

    for event in events:
        if event.timestamp < start or event.timestamp > end:
            continue
        target_minute = event.timestamp.replace(second=0, microsecond=0) + timedelta(
            minutes=1
        )
        bar = lookup.bar_at_or_after(target_minute)
        if bar is not None and bar.bar_time <= end:
            actions.append((bar.bar_time, 2, "signal", bar.open, event))

    realized = 0.0
    one_way = 0
    closed_pnls: list[float] = []
    for _, _, kind, price, event in sorted(actions, key=lambda item: (item[0], item[1])):
        if kind == "flatten":
            targets = {code: 0 for code in positions}
        elif kind == "reopen":
            if not reopen:
                continue
            targets = dict(desired)
        else:
            assert event is not None
            desired[event.strategy_code] = event.new_position
            targets = {event.strategy_code: event.new_position}

        for code, target in targets.items():
            previous = positions.get(code, 0)
            if previous == target:
                continue
            if previous and code in entries:
                pnl = (price - entries[code]) * previous * 10
                realized += pnl
                closed_pnls.append(pnl)
            one_way += abs(target - previous)
            positions[code] = target
            if target:
                entries[code] = price
            else:
                entries.pop(code, None)

    unrealized = sum(
        (end_price - entries[code]) * position * 10
        for code, position in positions.items()
        if position and code in entries
    )
    suffix = "reopen" if reopen else "wait_signal"
    policy = f"flat_{break_scope}_{suffix}"
    return Result(policy, realized, unrealized, one_way, sum(positions.values()), closed_pnls)


def run_target_strategy(
    *,
    name: str,
    timed_updates: list[tuple[datetime, str, object]],
    initial_state: object,
    update_state: Callable[[object, str, object], None],
    get_target: Callable[[object], int],
    get_price: Callable[[datetime, str, object], float | None],
    start: datetime,
    end: datetime,
    start_price: float,
    end_price: float,
) -> Result:
    state = initial_state
    for timestamp, kind, payload in timed_updates:
        if timestamp >= start:
            break
        update_state(state, kind, payload)
    target = get_target(state)
    entry = start_price if target else None
    realized = 0.0
    one_way = 0
    closed_pnls: list[float] = []
    for timestamp, kind, payload in timed_updates:
        if timestamp < start:
            continue
        if timestamp > end:
            break
        price = get_price(timestamp, kind, payload)
        if price is None:
            continue
        update_state(state, kind, payload)
        new_target = get_target(state)
        if new_target == target:
            continue
        if target and entry is not None:
            pnl = (price - entry) * target * 10
            realized += pnl
            closed_pnls.append(pnl)
        one_way += abs(new_target - target)
        target = new_target
        entry = price if target else None
    unrealized = 0.0 if not target or entry is None else (end_price - entry) * target * 10
    return Result(name, realized, unrealized, one_way, target, closed_pnls)


def load_rsi_targets(
    strategy_path: Path,
    price_path: Path,
    events: list[EfEvent],
    threshold: float,
) -> dict[int, int]:
    spec = importlib.util.spec_from_file_location("rsi60_strategy_for_backtest", strategy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    signal_events = [
        module.SignalEvent(
            row_number=event.row_number,
            timestamp=event.timestamp,
            strategy_code=event.strategy_code,
            previous_position=event.previous_position,
            new_position=event.new_position,
        )
        for event in events
    ]
    snapshots = module.load_rsi_snapshots(price_path)
    _, decisions = module.replay_events(signal_events, snapshots, threshold=threshold)
    return {decision.event.row_number: decision.filtered_position for decision in decisions}


def backtest_h(
    *,
    h_events: list[HEvent],
    lookup: PriceLookup,
    start: datetime,
    end: datetime,
    start_price: float,
    end_price: float,
    execution_price: Callable[[datetime], float | None],
    units: int = 1,
) -> Result:
    position = 0
    for event in h_events:
        if event.timestamp >= start:
            break
        position = event.new_position
    entry = start_price if position else None
    realized = 0.0
    one_way = 0
    closed_pnls: list[float] = []
    for event in h_events:
        if event.timestamp < start:
            continue
        if event.timestamp > end:
            break
        price = event.exact_price
        if price is None:
            price = execution_price(event.timestamp)
        if price is None or position == event.new_position:
            continue
        if position and entry is not None:
            pnl = (price - entry) * position * units * 10
            realized += pnl
            closed_pnls.append(pnl)
        one_way += abs(event.new_position - position) * units
        position = event.new_position
        entry = price if position else None
    unrealized = (
        0.0
        if not position or entry is None
        else (end_price - entry) * position * units * 10
    )
    return Result(
        f"h_execution_replay_{units}",
        realized,
        unrealized,
        one_way,
        position * units,
        closed_pnls,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-08-01 00:00:00")
    parser.add_argument("--end", default="")
    parser.add_argument("--rsi-threshold", type=float, default=50.0)
    parser.add_argument(
        "--h-source",
        type=Path,
        default=Path.home() / "Downloads" / "h3.csv",
        help="Complete TradingView H3 CSV; falls back to the legacy event file if absent.",
    )
    parser.add_argument("--h-units", type=int, default=1)
    parser.add_argument(
        "--one-way-cost-twd",
        type=float,
        default=24.0,
        help="Qunyi cost per one-way fill; NT$24 equals NT$48 per round trip.",
    )
    parser.add_argument(
        "--fill-mode",
        choices=("strict-next", "signal-minute", "visible-next"),
        default="strict-next",
    )
    parser.add_argument(
        "--event-time",
        choices=("message", "received"),
        default="received",
        help="Use the signal's embedded time or the time it became actionable locally.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    backend = root / "backend-futures-py"
    price_path = backend / "tv_doc" / "webhook_data_1min.csv"
    signal_path = backend / "tv_doc" / "six_strategy_signal_events.csv"
    legacy_h_path = (
        backend / "h3-ef-012-strategy" / "records" / "h3_position_events.csv"
    )
    h_path = args.h_source if args.h_source.exists() else legacy_h_path
    bars = load_prices(price_path)
    lookup = PriceLookup(bars)
    events = load_ef_events(signal_path, event_time=args.event_time)
    h_events = load_h_source(h_path)
    start = parse_time(args.start)
    if args.end:
        requested_end = parse_time(args.end)
        eligible_bars = [bar for bar in bars if bar.bar_time <= requested_end]
        if not eligible_bars:
            raise RuntimeError("No end price")
        end = eligible_bars[-1].bar_time
        end_price = eligible_bars[-1].close
    else:
        end = bars[-1].bar_time
        end_price = bars[-1].close
    start_price = lookup.open_at_or_after(start)
    if start_price is None:
        raise RuntimeError("No start price")
    execution_price = {
        "strict-next": lookup.next_minute_open,
        "signal-minute": lookup.signal_minute_open,
        "visible-next": lookup.visible_next_open,
    }[args.fill_mode]

    # Pure H uses the TradingView strategy's explicit prices when available.
    pure_h = backtest_h(
        h_events=h_events,
        lookup=lookup,
        start=start,
        end=end,
        start_price=start_price,
        end_price=end_price,
        execution_price=execution_price,
        units=args.h_units,
    )

    pure_ef = backtest_separate_book(
        name="ef_signal_replay_12_theoretical",
        events=events,
        filtered_targets=None,
        lookup=lookup,
        start=start,
        end=end,
        start_price=start_price,
        end_price=end_price,
        execution_price=execution_price,
    )

    rsi_targets = load_rsi_targets(
        backend / "ef-rsi60-filter-strategy" / "strategy.py",
        price_path,
        events,
        args.rsi_threshold,
    )
    rsi = backtest_separate_book(
        name=f"rsi{args.rsi_threshold:g}_filtered_12",
        events=events,
        filtered_targets=rsi_targets,
        lookup=lookup,
        start=start,
        end=end,
        start_price=start_price,
        end_price=end_price,
        execution_price=execution_price,
    )

    # Strong consensus: both E and F net positions must independently reach 2.
    strong_state: dict[str, int] = {}
    strong_updates = [(event.timestamp, "ef", event) for event in events]

    def update_ef(state: object, _kind: str, payload: object) -> None:
        assert isinstance(state, dict) and isinstance(payload, EfEvent)
        state[payload.strategy_code] = payload.new_position

    def strong_target(state: object) -> int:
        assert isinstance(state, dict)
        e_net = sum(int(state.get(code, 0)) for code in PORTFOLIO_E)
        f_net = sum(int(state.get(code, 0)) for code in PORTFOLIO_F)
        if e_net >= 2 and f_net >= 2:
            return 1
        if e_net <= -2 and f_net <= -2:
            return -1
        return 0

    def ef_price(timestamp: datetime, _kind: str, _payload: object) -> float | None:
        return execution_price(timestamp)

    strong = run_target_strategy(
        name="ef_strong_threshold2",
        timed_updates=strong_updates,
        initial_state=strong_state,
        update_state=update_ef,
        get_target=strong_target,
        get_price=ef_price,
        start=start,
        end=end,
        start_price=start_price,
        end_price=end_price,
    )

    # H3+EF 0/1/2 with U=1. Backfilled H events retain their exact CSV price;
    # EF-driven target changes execute on the strict next 1m bar open.
    combined_state: dict[str, object] = {"h": 0, "ef": {}}
    combined_updates = sorted(
        [(event.timestamp, "ef", event) for event in events]
        + [(event.timestamp, "h", event) for event in h_events],
        key=lambda item: (item[0], item[1]),
    )

    def update_combined(state: object, kind: str, payload: object) -> None:
        assert isinstance(state, dict)
        if kind == "h":
            assert isinstance(payload, HEvent)
            state["h"] = payload.new_position
        else:
            assert isinstance(payload, EfEvent)
            ef_positions = state["ef"]
            assert isinstance(ef_positions, dict)
            ef_positions[payload.strategy_code] = payload.new_position

    def combined_target(state: object) -> int:
        assert isinstance(state, dict)
        h_position = int(state["h"])
        ef_positions = state["ef"]
        assert isinstance(ef_positions, dict)
        ef_net = sum(int(ef_positions.get(code, 0)) for code in ALL_STRATEGIES)
        ef_direction = (ef_net > 0) - (ef_net < 0)
        if ef_direction == -h_position:
            return 0
        if ef_direction == h_position:
            return 2 * h_position
        return h_position

    def combined_price(timestamp: datetime, kind: str, payload: object) -> float | None:
        if kind == "h":
            assert isinstance(payload, HEvent)
            if payload.exact_price is not None:
                return payload.exact_price
        return execution_price(timestamp)

    combined = run_target_strategy(
        name="h3_ef_u1",
        timed_updates=combined_updates,
        initial_state=combined_state,
        update_state=update_combined,
        get_target=combined_target,
        get_price=combined_price,
        start=start,
        end=end,
        start_price=start_price,
        end_price=end_price,
    )

    results = [pure_h, pure_ef, combined, strong, rsi]
    break_results = [
        backtest_ef_break_policy(
            events=events,
            bars=bars,
            lookup=lookup,
            start=start,
            end=end,
            start_price=start_price,
            end_price=end_price,
            reopen=reopen,
            break_scope=scope,
        )
        for scope in ("all", "night", "day")
        for reopen in (True, False)
    ]
    print(
        f"period={start.strftime(TIME_FORMAT)}..{end.strftime(TIME_FORMAT)} "
        f"start_price={start_price:.0f} end_price={end_price:.0f} "
        f"ef_event_time={args.event_time} "
        f"ef_fill={args.fill_mode}_1m_open rsi_threshold={args.rsi_threshold:g} "
        f"h_source={h_path} h_units={args.h_units} "
        f"one_way_cost_twd={args.one_way_cost_twd:g}"
    )
    if h_events and h_events[-1].timestamp < end:
        print(
            "h_source_last_event="
            f"{h_events[-1].timestamp.strftime(TIME_FORMAT)} "
            "warning=no H source rows after this timestamp; later P&L is hold-to-mark only"
        )
    print(
        "strategy gross_profit_twd gross_loss_twd profit_factor closed_trades "
        "realized_twd unrealized_twd total_twd one_way estimated_net_twd end_position"
    )
    for result in results:
        print(
            result.name,
            round(result.gross_profit),
            round(result.gross_loss),
            "inf" if result.gross_loss == 0 else f"{result.profit_factor:.2f}",
            len(result.closed_pnls),
            round(result.realized),
            round(result.unrealized),
            round(result.total),
            result.one_way,
            round(result.total - result.one_way * args.one_way_cost_twd),
            result.ending_position,
        )
    with h_path.open(newline="", encoding="utf-8-sig") as handle:
        h_header = next(csv.reader(handle), [])
    if "獲利($)" in h_header:
        reported_pnls, invalid_h_exits = load_h_reported_closed_pnls(
            h_path, start, end
        )
        reported_profit = sum(pnl for pnl in reported_pnls if pnl > 0)
        reported_loss = -sum(pnl for pnl in reported_pnls if pnl < 0)
        reported_realized = reported_profit - reported_loss
        reported_cost = len(reported_pnls) * args.one_way_cost_twd * 2
        print(
            "h_tradingview_exit_basis",
            f"closed_trades={len(reported_pnls)}",
            f"gross_profit_twd={reported_profit:.0f}",
            f"gross_loss_twd={reported_loss:.0f}",
            "profit_factor="
            + (
                "inf"
                if reported_loss == 0
                else f"{reported_profit / reported_loss:.2f}"
            ),
            f"realized_twd={reported_realized:.0f}",
            f"estimated_cost_twd={reported_cost:.0f}",
            f"estimated_net_twd={reported_realized - reported_cost:.0f}",
            f"invalid_exit_rows={invalid_h_exits}",
        )
    all_five_gross_profit = sum(result.gross_profit for result in results)
    all_five_gross_loss = sum(result.gross_loss for result in results)
    print(
        "all_five",
        round(all_five_gross_profit),
        round(all_five_gross_loss),
        "inf" if all_five_gross_loss == 0 else f"{all_five_gross_profit / all_five_gross_loss:.2f}",
        sum(len(result.closed_pnls) for result in results),
        round(sum(result.realized for result in results)),
        round(sum(result.unrealized for result in results)),
        round(sum(result.total for result in results)),
        sum(result.one_way for result in results),
        round(
            sum(result.total for result in results)
            - sum(result.one_way for result in results) * args.one_way_cost_twd
        ),
        sum(result.ending_position for result in results),
    )
    print("pure_ef_break_policy total_twd one_way end_position")
    print("hold_through_break", round(pure_ef.total), pure_ef.one_way, pure_ef.ending_position)
    for result in break_results:
        print(result.name, round(result.total), result.one_way, result.ending_position)


if __name__ == "__main__":
    main()
