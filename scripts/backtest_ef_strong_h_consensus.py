from __future__ import annotations

import argparse
import bisect
import csv
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

import backtest_five_strategies_next_open as base


POINT_VALUE = 10.0
MORNING_FLAT = time(4, 59)
DAY_OPEN = time(8, 45)


@dataclass(frozen=True)
class HEvent:
    timestamp: datetime
    position: int
    price: float


@dataclass(frozen=True)
class Action:
    timestamp: datetime
    priority: int
    kind: str
    price: float
    payload: object | None = None


@dataclass(frozen=True)
class Trade:
    entry_time: datetime
    exit_time: datetime
    direction: int
    entry_price: float
    exit_price: float
    pnl: float


@dataclass
class Result:
    name: str
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    realized: float = 0.0
    unrealized: float = 0.0
    turnover: int = 0
    ending_position: int = 0
    max_drawdown: float = 0.0
    exposure_bars: int = 0
    marked_bars: int = 0
    missing_fills: int = 0
    closed_pnls: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.realized + self.unrealized

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf


def load_h_trade(path: Path) -> list[HEvent]:
    # h_trade contains an exit and an entry at the same timestamp for reversals.
    # Collapse them to the final state so the combined strategy changes once.
    grouped: dict[datetime, tuple[int, float]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = base.parse_time(row["timestamp"])
                action = str(row["action"]).strip().lower()
                side = str(row["side"]).strip().lower()
                price = float(row["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if action == "enter":
                position = 1 if side == "bull" else -1 if side == "bear" else 0
            elif action == "exiting":
                position = 0
            else:
                continue
            grouped[timestamp] = (position, price)
    return [HEvent(ts, *grouped[ts]) for ts in sorted(grouped)]


def load_continuous_h(tv_export: Path, live_events_path: Path) -> tuple[list[HEvent], int]:
    historical = base.load_h_source(tv_export)
    if not historical:
        raise RuntimeError(f"No H events in {tv_export}")
    cutoff = historical[-1].timestamp
    combined: dict[datetime, HEvent] = {}
    skipped_without_price = 0
    for event in historical:
        if event.exact_price is None:
            skipped_without_price += 1
            continue
        combined[event.timestamp] = HEvent(
            event.timestamp, event.new_position, event.exact_price
        )
    for event in base.load_h_events(live_events_path):
        if event.timestamp <= cutoff:
            continue
        if event.exact_price is None:
            skipped_without_price += 1
            continue
        combined[event.timestamp] = HEvent(
            event.timestamp, event.new_position, event.exact_price
        )
    return [combined[key] for key in sorted(combined)], skipped_without_price


def load_h_export(path: Path) -> tuple[list[HEvent], int]:
    grouped: dict[datetime, HEvent] = {}
    skipped_without_price = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            trade_type = str(row.get("類型") or "").strip()
            if not (trade_type.startswith("Entry") or trade_type.startswith("Exit")):
                continue
            try:
                timestamp = datetime.strptime(
                    f"{row['日期'].strip()} {row['時間'].strip()}",
                    "%Y/%m/%d %H:%M:%S",
                )
                price = float(str(row["價格"]).replace(",", ""))
            except (KeyError, TypeError, ValueError):
                skipped_without_price += 1
                continue
            if trade_type == "Entry Long":
                position = 1
            elif trade_type == "Entry Short":
                position = -1
            elif trade_type.startswith("Exit"):
                position = 0
            else:
                continue
            # Reversals have Exit and Entry rows at the same timestamp. File
            # order puts Entry last, so retain the final actionable state.
            grouped[timestamp] = HEvent(timestamp, position, price)
    return [grouped[key] for key in sorted(grouped)], skipped_without_price


def load_strict_ef_events(path: Path) -> tuple[list[base.EfEvent], int, int]:
    events: list[base.EfEvent] = []
    untimed = 0
    duplicates = 0
    seen: set[tuple[object, ...]] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=1):
            received_at = str(row.get("received_at") or "").strip()
            if not received_at:
                untimed += 1
                continue
            try:
                code = str(row.get("strategy_code") or "").strip()
                event = base.EfEvent(
                    row_number=row_number,
                    timestamp=base.parse_time(received_at),
                    strategy_code=code,
                    previous_position=int(float(row["previous_position"])),
                    new_position=int(float(row["new_position"])),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if code not in base.ALL_STRATEGIES:
                continue
            key = (
                event.timestamp,
                code,
                event.previous_position,
                event.new_position,
                str(row.get("account") or ""),
            )
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            events.append(event)
    return sorted(events, key=lambda event: (event.timestamp, event.row_number)), untimed, duplicates


def strong_ef_target(positions: dict[str, int], threshold: int) -> int:
    e_net = sum(positions.get(code, 0) for code in base.PORTFOLIO_E)
    f_net = sum(positions.get(code, 0) for code in base.PORTFOLIO_F)
    if e_net >= threshold and f_net >= threshold:
        return 1
    if e_net <= -threshold and f_net <= -threshold:
        return -1
    return 0


def combined_target(policy: str, ef_target: int, h_position: int) -> int:
    if policy == "ef_only":
        return ef_target
    if policy == "h_veto":
        return 0 if ef_target and h_position == -ef_target else ef_target
    if policy == "h_confirm":
        return ef_target if ef_target and h_position == ef_target else 0
    raise ValueError(policy)


def in_morning_block(timestamp: datetime) -> bool:
    return MORNING_FLAT <= timestamp.time() < DAY_OPEN


def previous_morning_boundary(bars: list[base.PriceBar], start: datetime) -> datetime:
    candidates = [
        bar.bar_time
        for bar in bars
        if bar.bar_time < start and bar.bar_time.time() == MORNING_FLAT
    ]
    return candidates[-1] if candidates else bars[0].bar_time


def build_actions(
    bars: list[base.PriceBar],
    ef_events: list[base.EfEvent],
    h_events: list[HEvent],
    warmup: datetime,
    end: datetime,
) -> tuple[list[Action], int]:
    times = [bar.bar_time for bar in bars]
    actions: list[Action] = []
    missing = 0
    for bar in bars:
        if warmup <= bar.bar_time <= end and bar.bar_time.time() == MORNING_FLAT:
            actions.append(Action(bar.bar_time, 0, "flatten", bar.open))
    for event in h_events:
        if warmup <= event.timestamp <= end:
            actions.append(Action(event.timestamp, 1, "h", event.price, event))
    for event in ef_events:
        if event.timestamp < warmup or event.timestamp > end:
            continue
        target_time = event.timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        index = bisect.bisect_left(times, target_time)
        if index >= len(bars) or bars[index].bar_time > end:
            missing += 1
            continue
        fill = bars[index]
        actions.append(Action(fill.bar_time, 2, "ef", fill.open, event))
    actions.sort(
        key=lambda action: (
            action.timestamp,
            action.priority,
            0
            if not isinstance(action.payload, base.EfEvent)
            else action.payload.row_number,
        )
    )
    return actions, missing


def run(
    *,
    name: str,
    policy: str,
    bars: list[base.PriceBar],
    ef_events: list[base.EfEvent],
    h_events: list[HEvent],
    start: datetime,
    end: datetime,
    threshold: int,
) -> Result:
    eligible = [bar for bar in bars if start <= bar.bar_time <= end]
    if not eligible:
        raise RuntimeError("No price bars in reporting window")
    warmup = previous_morning_boundary(bars, start)
    ef_positions = base.state_before_ef(ef_events, warmup)
    h_position = 0
    for event in h_events:
        if event.timestamp >= warmup:
            break
        h_position = event.position
    position = 0
    actions, missing = build_actions(bars, ef_events, h_events, warmup, end)

    # Warm up from the preceding 04:59 boundary to reconstruct the live position
    # without automatically restoring the pre-flatten target at 08:45.
    action_index = 0
    while action_index < len(actions) and actions[action_index].timestamp < start:
        action = actions[action_index]
        if action.kind == "flatten":
            position = 0
        elif action.kind == "h":
            assert isinstance(action.payload, HEvent)
            h_position = action.payload.position
            ef_target = strong_ef_target(ef_positions, threshold)
            position = 0 if in_morning_block(action.timestamp) else combined_target(
                policy, ef_target, h_position
            )
        else:
            assert isinstance(action.payload, base.EfEvent)
            ef_positions[action.payload.strategy_code] = action.payload.new_position
            ef_target = strong_ef_target(ef_positions, threshold)
            position = 0 if in_morning_block(action.timestamp) else combined_target(
                policy, ef_target, h_position
            )
        action_index += 1

    entry_price: float | None = eligible[0].open if position else None
    entry_time: datetime | None = start if position else None
    result = Result(name=name, ending_position=position, missing_fills=missing)
    realized = 0.0
    peak = 0.0

    for bar in eligible:
        while action_index < len(actions) and actions[action_index].timestamp <= bar.bar_time:
            action = actions[action_index]
            if action.kind == "flatten":
                target = 0
            elif action.kind == "h":
                assert isinstance(action.payload, HEvent)
                h_position = action.payload.position
                ef_target = strong_ef_target(ef_positions, threshold)
                target = 0 if in_morning_block(action.timestamp) else combined_target(
                    policy, ef_target, h_position
                )
            else:
                assert isinstance(action.payload, base.EfEvent)
                ef_positions[action.payload.strategy_code] = action.payload.new_position
                ef_target = strong_ef_target(ef_positions, threshold)
                target = 0 if in_morning_block(action.timestamp) else combined_target(
                    policy, ef_target, h_position
                )
            if target != position:
                if position:
                    assert entry_price is not None and entry_time is not None
                    pnl = (action.price - entry_price) * position * POINT_VALUE
                    result.closed_pnls.append(pnl)
                    result.trades.append(
                        Trade(
                            entry_time,
                            action.timestamp,
                            position,
                            entry_price,
                            action.price,
                            pnl,
                        )
                    )
                    realized += pnl
                    if pnl > 0:
                        result.gross_profit += pnl
                    elif pnl < 0:
                        result.gross_loss += -pnl
                result.turnover += abs(target - position)
                position = target
                entry_price = action.price if target else None
                entry_time = action.timestamp if target else None
            action_index += 1

        unrealized = (
            (bar.close - entry_price) * position * POINT_VALUE
            if position and entry_price is not None
            else 0.0
        )
        if position:
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
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest EF strong consensus with H confirmation")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--threshold", type=int, default=2)
    parser.add_argument("--one-way-cost-twd", type=float, default=24.0)
    parser.add_argument("--h-source", type=Path, default=None)
    parser.add_argument("--show-trades", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    backend = root / "backend-futures-py"
    bars = base.load_prices(backend / "tv_doc" / "webhook_data_1min.csv")
    ef_events, untimed, duplicates = load_strict_ef_events(
        backend / "tv_doc" / "six_strategy_signal_events.csv"
    )
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
        f"period={start}..{end} ef_event_time=received_at ef_fill=strict_next_1m_open "
        f"h_source={h_path_text} h_fill=recorded_exact_price point_value={POINT_VALUE:g} "
        f"one_way_cost_twd={args.one_way_cost_twd:g} untimed_skipped={untimed} "
        f"duplicate_events={duplicates} h_without_price_skipped={h_skipped} "
        f"h_last={h_events[-1].timestamp}"
    )
    print(
        "strategy gross_profit_twd gross_loss_twd PF closed_legs realized_twd "
        "unrealized_twd total_twd turnover estimated_net_twd max_drawdown_twd "
        "ending_position exposure missing_fills"
    )
    for policy in ("ef_only", "h_veto", "h_confirm"):
        result = run(
            name=policy,
            policy=policy,
            bars=bars,
            ef_events=ef_events,
            h_events=h_events,
            start=start,
            end=end,
            threshold=args.threshold,
        )
        exposure = result.exposure_bars / result.marked_bars if result.marked_bars else 0.0
        net = result.total - result.turnover * args.one_way_cost_twd
        pf = "inf" if math.isinf(result.profit_factor) else f"{result.profit_factor:.2f}"
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
            round(net),
            round(result.max_drawdown),
            result.ending_position,
            f"{exposure:.3f}",
            result.missing_fills,
        )
        if args.show_trades and policy == "h_confirm":
            for trade in result.trades:
                print(
                    "trade",
                    trade.entry_time,
                    trade.exit_time,
                    "long" if trade.direction > 0 else "short",
                    f"{trade.entry_price:g}",
                    f"{trade.exit_price:g}",
                    f"{trade.pnl:.0f}",
                )


if __name__ == "__main__":
    main()
