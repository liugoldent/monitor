from __future__ import annotations

import argparse
import bisect
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from strategy import ALL_STRATEGIES, PORTFOLIO_E, PORTFOLIO_F


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
DEFAULT_SIGNALS = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
DEFAULT_PRICES = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    strategy_code: str
    previous_position: int
    new_position: int


def sign(value: int) -> int:
    return (value > 0) - (value < 0)


def load_events(path: Path) -> list[Event]:
    events: list[Event] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp_text = row.get("message_time") or row.get("received_at") or ""
            try:
                timestamp = datetime.strptime(timestamp_text, TIME_FORMAT)
                previous = int(float(row["previous_position"]))
                new = int(float(row["new_position"]))
            except (KeyError, TypeError, ValueError):
                continue
            code = str(row.get("strategy_code") or "").strip()
            if code in ALL_STRATEGIES:
                events.append(Event(timestamp, code, previous, new))
    return sorted(events, key=lambda event: event.timestamp)


def load_prices(path: Path) -> tuple[list[datetime], list[float]]:
    values: list[tuple[datetime, float]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                values.append(
                    (
                        datetime.strptime(row["Record Time"], TIME_FORMAT),
                        float(row["Close"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    values.sort()
    return [value[0] for value in values], [value[1] for value in values]


def previous_price(
    timestamp: datetime,
    price_times: list[datetime],
    prices: list[float],
) -> float | None:
    index = bisect.bisect_right(price_times, timestamp) - 1
    if index < 0 or timestamp - price_times[index] > timedelta(minutes=5):
        return None
    return prices[index]


def target(positions: dict[str, int], threshold: int) -> int:
    e_net = sum(positions.get(code, 0) for code in PORTFOLIO_E)
    f_net = sum(positions.get(code, 0) for code in PORTFOLIO_F)
    if e_net >= threshold and f_net >= threshold:
        return 1
    if e_net <= -threshold and f_net <= -threshold:
        return -1
    return 0


def run_month(
    events: list[Event],
    price_times: list[datetime],
    prices: list[float],
    month: str,
    threshold: int,
) -> dict[str, float | int]:
    start = datetime.strptime(month + "-01", "%Y-%m-%d")
    if start.month == 12:
        end = datetime(start.year + 1, 1, 1)
    else:
        end = datetime(start.year, start.month + 1, 1)
    data_end = min(end, price_times[-1] + timedelta(seconds=1))
    monthly_events = [event for event in events if start <= event.timestamp < data_end]

    positions: dict[str, int] = {}
    for event in events:
        if event.timestamp >= start:
            break
        positions[event.strategy_code] = event.new_position
    for event in monthly_events:
        positions.setdefault(event.strategy_code, event.previous_position)

    current_target = target(positions, threshold)
    entry_price = previous_price(start, price_times, prices)
    pnls: list[float] = []
    missing_segments = 0
    for event in monthly_events:
        event_price = previous_price(event.timestamp, price_times, prices)
        positions[event.strategy_code] = event.new_position
        new_target = target(positions, threshold)
        if new_target == current_target:
            continue
        if current_target:
            if entry_price is None or event_price is None:
                missing_segments += 1
            else:
                pnls.append((event_price - entry_price) * current_target)
        current_target = new_target
        entry_price = event_price

    last_index = bisect.bisect_left(price_times, data_end) - 1
    if current_target and entry_price is not None and last_index >= 0:
        pnls.append((prices[last_index] - entry_price) * current_target)

    gross_profit = sum(max(pnl, 0) for pnl in pnls)
    gross_loss = -sum(min(pnl, 0) for pnl in pnls)
    equity = peak = max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return {
        "segments": len(pnls),
        "missing_segments": missing_segments,
        "points": round(equity, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else float("inf"),
        "max_drawdown": round(max_drawdown, 2),
        "peak": round(peak, 2),
        "giveback": round(peak - equity, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest pure EF strong consensus by month")
    parser.add_argument("--months", nargs="+", default=["2026-07", "2026-08"])
    parser.add_argument("--thresholds", nargs="+", type=int, default=[1, 2])
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    args = parser.parse_args()
    events = load_events(args.signals)
    price_times, prices = load_prices(args.prices)
    print("month threshold segments missing points PF max_drawdown peak giveback")
    for month in args.months:
        for threshold in args.thresholds:
            result = run_month(events, price_times, prices, month, threshold)
            print(
                month,
                threshold,
                result["segments"],
                result["missing_segments"],
                result["points"],
                result["profit_factor"],
                result["max_drawdown"],
                result["peak"],
                result["giveback"],
            )


if __name__ == "__main__":
    main()
