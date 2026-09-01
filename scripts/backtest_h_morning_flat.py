from __future__ import annotations

import argparse
import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

import backtest_five_strategies_next_open as base
from backtest_ef_strong_h_consensus import HEvent, load_continuous_h, load_h_export


POINT_VALUE = 10.0
MORNING_FLAT = time(4, 59)
DAY_OPEN = time(8, 45)


@dataclass(frozen=True)
class Action:
    timestamp: datetime
    priority: int
    kind: str
    price: float
    h_event: HEvent | None = None


@dataclass
class Result:
    name: str
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    realized: float = 0.0
    unrealized: float = 0.0
    turnover: int = 0
    closed_pnls: list[float] = field(default_factory=list)
    ending_position: int = 0
    max_drawdown: float = 0.0
    exposure_bars: int = 0
    marked_bars: int = 0
    scheduled_flats: int = 0
    scheduled_reopens: int = 0

    @property
    def total(self) -> float:
        return self.realized + self.unrealized

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf


def previous_flat_bar(bars: list[base.PriceBar], start: datetime) -> base.PriceBar:
    candidates = [
        bar
        for bar in bars
        if bar.bar_time < start and bar.bar_time.time() == MORNING_FLAT
    ]
    return candidates[-1] if candidates else bars[0]


def build_actions(
    *,
    policy: str,
    bars: list[base.PriceBar],
    h_events: list[HEvent],
    warmup: datetime,
    end: datetime,
) -> list[Action]:
    actions: list[Action] = []
    if policy != "hold":
        day_open_bars = [bar for bar in bars if bar.bar_time.time() == DAY_OPEN]
        day_open_times = [bar.bar_time for bar in day_open_bars]
        for bar in bars:
            if not warmup <= bar.bar_time <= end or bar.bar_time.time() != MORNING_FLAT:
                continue
            actions.append(Action(bar.bar_time, 0, "flatten", bar.open))
            if policy == "reopen":
                reopen_index = bisect.bisect_right(day_open_times, bar.bar_time)
                reopen_bar = (
                    day_open_bars[reopen_index]
                    if reopen_index < len(day_open_bars)
                    else None
                )
                if reopen_bar is not None and reopen_bar.bar_time <= end:
                    # The stored H direction is already known before the open, so
                    # the 08:45 Open is a non-lookahead execution proxy.
                    actions.append(
                        Action(reopen_bar.bar_time, 2, "reopen", reopen_bar.open)
                    )
    for event in h_events:
        if warmup <= event.timestamp <= end:
            actions.append(Action(event.timestamp, 1, "h", event.price, event))
    return sorted(actions, key=lambda action: (action.timestamp, action.priority))


def action_target(
    *,
    policy: str,
    action: Action,
    h_position: int,
    waiting: bool,
) -> tuple[int, int, bool]:
    if action.kind == "flatten":
        return 0, h_position, policy == "wait"
    if action.kind == "reopen":
        return h_position, h_position, False
    assert action.h_event is not None
    h_position = action.h_event.position
    if policy == "hold":
        return h_position, h_position, False
    if MORNING_FLAT <= action.timestamp.time() < DAY_OPEN:
        return 0, h_position, waiting
    if policy == "wait" and waiting:
        return h_position, h_position, False
    return h_position, h_position, waiting


def run(
    *,
    name: str,
    policy: str,
    bars: list[base.PriceBar],
    h_events: list[HEvent],
    start: datetime,
    end: datetime,
) -> Result:
    eligible = [bar for bar in bars if start <= bar.bar_time <= end]
    if not eligible:
        raise RuntimeError("No price bars in reporting window")
    warmup_bar = previous_flat_bar(bars, start)
    warmup = warmup_bar.bar_time
    h_position = 0
    for event in h_events:
        if event.timestamp >= warmup:
            break
        h_position = event.position
    position = h_position if policy == "hold" else 0
    waiting = policy == "wait"
    actions = build_actions(
        policy=policy,
        bars=bars,
        h_events=h_events,
        warmup=warmup,
        end=end,
    )
    action_index = 0
    while action_index < len(actions) and actions[action_index].timestamp < start:
        position, h_position, waiting = action_target(
            policy=policy,
            action=actions[action_index],
            h_position=h_position,
            waiting=waiting,
        )
        action_index += 1

    entry_price: float | None = eligible[0].open if position else None
    result = Result(name=name, ending_position=position)
    realized = 0.0
    peak = 0.0
    current_position = position

    timeline: list[tuple[datetime, int, str, object]] = []
    for action in actions[action_index:]:
        if start <= action.timestamp <= end:
            timeline.append((action.timestamp, action.priority, "action", action))
    for bar in eligible:
        timeline.append((bar.bar_time + timedelta(minutes=1), 9, "mark", bar))

    for _, _, kind, payload in sorted(timeline, key=lambda item: (item[0], item[1])):
        if kind == "action":
            assert isinstance(payload, Action)
            target, h_position, waiting = action_target(
                policy=policy,
                action=payload,
                h_position=h_position,
                waiting=waiting,
            )
            if payload.kind == "flatten":
                result.scheduled_flats += 1
            elif payload.kind == "reopen":
                result.scheduled_reopens += 1
            if target != current_position:
                if current_position:
                    assert entry_price is not None
                    pnl = (payload.price - entry_price) * current_position * POINT_VALUE
                    result.closed_pnls.append(pnl)
                    realized += pnl
                    if pnl > 0:
                        result.gross_profit += pnl
                    elif pnl < 0:
                        result.gross_loss += -pnl
                result.turnover += abs(target - current_position)
                current_position = target
                entry_price = payload.price if target else None
        else:
            assert isinstance(payload, base.PriceBar)
            unrealized = (
                (payload.close - entry_price) * current_position * POINT_VALUE
                if current_position and entry_price is not None
                else 0.0
            )
            if current_position:
                result.exposure_bars += 1
            equity = realized + unrealized
            peak = max(peak, equity)
            result.max_drawdown = min(result.max_drawdown, equity - peak)
            result.marked_bars += 1
            result.unrealized = unrealized

    result.realized = realized
    result.ending_position = current_position
    if abs(result.gross_profit - result.gross_loss - result.realized) > 1e-9:
        raise AssertionError("gross profit/loss reconciliation failed")
    if abs(result.realized + result.unrealized - result.total) > 1e-9:
        raise AssertionError("total reconciliation failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare H morning-break policies")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--one-way-cost-twd", type=float, default=24.0)
    parser.add_argument("--h-source", type=Path, default=None)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    backend = root / "backend-futures-py"
    bars = base.load_prices(backend / "tv_doc" / "webhook_data_1min.csv")
    if args.h_source is not None:
        h_path_text = str(args.h_source)
        h_events, h_skipped = load_h_export(args.h_source)
    else:
        tv_export = Path.home() / "Downloads" / "h3.csv"
        live_events_path = backend / "h3-ef-012-strategy" / "records" / "h3_position_events.csv"
        h_path_text = f"{tv_export}+{live_events_path}"
        h_events, h_skipped = load_continuous_h(tv_export, live_events_path)
    start = base.parse_time(args.start)
    end = base.parse_time(args.end)

    print(
        f"period={start}..{end} h_source={h_path_text} h_signal_fill=recorded_exact_price "
        f"flat_fill=04:59_open reopen_fill=08:45_open point_value={POINT_VALUE:g} "
        f"one_way_cost_twd={args.one_way_cost_twd:g} h_without_price_skipped={h_skipped} "
        f"h_last={h_events[-1].timestamp}"
    )
    print(
        "policy gross_profit_twd gross_loss_twd PF closed_legs realized_twd "
        "unrealized_twd total_twd turnover estimated_net_twd max_drawdown_twd "
        "ending_position exposure flats reopens"
    )
    for name, policy in (
        ("hold_through_break", "hold"),
        ("04:59_flat_08:45_restore", "reopen"),
        ("04:59_flat_wait_next_h", "wait"),
    ):
        result = run(
            name=name,
            policy=policy,
            bars=bars,
            h_events=h_events,
            start=start,
            end=end,
        )
        pf = "inf" if math.isinf(result.profit_factor) else f"{result.profit_factor:.2f}"
        exposure = result.exposure_bars / result.marked_bars if result.marked_bars else 0.0
        estimated_net = result.total - result.turnover * args.one_way_cost_twd
        print(
            result.name,
            round(result.gross_profit),
            round(result.gross_loss),
            pf,
            len(result.closed_pnls),
            round(result.realized),
            round(result.unrealized),
            round(result.total),
            result.turnover,
            round(estimated_net),
            round(result.max_drawdown),
            result.ending_position,
            f"{exposure:.3f}",
            result.scheduled_flats,
            result.scheduled_reopens,
        )


if __name__ == "__main__":
    main()
