from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from strategy import (
    FilterDecision,
    SignalEvent,
    latest_recorded_price,
    load_recorded_prices,
    load_rsi_snapshots,
    load_signal_rows,
    replay_events,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
DEFAULT_SIGNALS = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"
DEFAULT_PRICES = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"


@dataclass
class OpenLeg:
    position: int
    price: float | None


def transition(
    book: dict[str, OpenLeg],
    strategy_code: str,
    target: int,
    price: float | None,
    pnls: list[float],
) -> int:
    previous = book.get(strategy_code, OpenLeg(0, None))
    if previous.position == target:
        return 0
    missing = 0
    if previous.position:
        if previous.price is None or price is None:
            missing += 1
        else:
            direction = 1 if previous.position > 0 else -1
            pnls.append((price - previous.price) * direction)
    if target:
        book[strategy_code] = OpenLeg(target, price)
    else:
        book.pop(strategy_code, None)
    return missing


def statistics(pnls: list[float]) -> dict[str, float | int]:
    if not pnls:
        return {
            "trades": 0,
            "total": 0.0,
            "average": math.nan,
            "win_rate": math.nan,
            "profit_factor": math.nan,
            "max_drawdown": 0.0,
        }
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = -sum(value for value in pnls if value < 0)
    equity = peak = max_drawdown = 0.0
    for value in pnls:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return {
        "trades": len(pnls),
        "total": equity,
        "average": equity / len(pnls),
        "win_rate": sum(value > 0 for value in pnls) / len(pnls),
        "profit_factor": gross_profit / gross_loss if gross_loss else math.inf,
        "max_drawdown": max_drawdown,
    }


def run_backtest(
    events: list[SignalEvent],
    decisions: list[FilterDecision],
    price_times: list[datetime],
    prices: list[float],
) -> dict[str, object]:
    decision_by_row = {decision.event.row_number: decision for decision in decisions}
    baseline_book: dict[str, OpenLeg] = {}
    filtered_book: dict[str, OpenLeg] = {}
    baseline_pnls: list[float] = []
    filtered_pnls: list[float] = []
    baseline_missing = filtered_missing = blocked_entries = 0
    for event in sorted(events, key=lambda value: (value.timestamp, value.row_number)):
        price = latest_recorded_price(price_times, prices, event.timestamp)
        baseline_missing += transition(
            baseline_book,
            event.strategy_code,
            event.new_position,
            price,
            baseline_pnls,
        )
        decision = decision_by_row[event.row_number]
        filtered_missing += transition(
            filtered_book,
            event.strategy_code,
            decision.filtered_position,
            price,
            filtered_pnls,
        )
        if decision.allowed is False:
            blocked_entries += 1
    return {
        "baseline": statistics(baseline_pnls),
        "filtered": statistics(filtered_pnls),
        "baseline_missing": baseline_missing,
        "filtered_missing": filtered_missing,
        "blocked_entries": blocked_entries,
        "open_baseline": len(baseline_book),
        "open_filtered": len(filtered_book),
    }


def format_metric(value: object, *, percentage: bool = False) -> str:
    if isinstance(value, float) and math.isnan(value):
        return "-"
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    if percentage:
        return f"{float(value):.1%}"
    return f"{float(value):.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the six-strategy RSI60 entry filter")
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--threshold", type=float, default=50.0)
    parser.add_argument("--from-date", type=str, default="")
    args = parser.parse_args()

    _, events = load_signal_rows(args.signals)
    if args.from_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d")
        events = [event for event in events if event.timestamp >= start]
    snapshots = load_rsi_snapshots(args.prices)
    _, decisions = replay_events(events, snapshots, threshold=args.threshold)
    price_times, prices = load_recorded_prices(args.prices)
    result = run_backtest(events, decisions, price_times, prices)

    print("strategy trades total_points avg_points win_rate PF max_drawdown")
    for name in ("baseline", "filtered"):
        metrics = result[name]
        print(
            name,
            metrics["trades"],
            format_metric(metrics["total"]),
            format_metric(metrics["average"]),
            format_metric(metrics["win_rate"], percentage=True),
            format_metric(metrics["profit_factor"]),
            format_metric(metrics["max_drawdown"]),
        )
    print(
        f"blocked_entries={result['blocked_entries']} "
        f"missing_baseline={result['baseline_missing']} "
        f"missing_filtered={result['filtered_missing']} "
        f"open_baseline={result['open_baseline']} "
        f"open_filtered={result['open_filtered']}"
    )


if __name__ == "__main__":
    main()
