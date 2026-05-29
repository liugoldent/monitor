"""Research H profit-retrace reverse guard.

The strategy targets this failure mode:
- H has a large unrealized profit.
- Price gives back too much of that open profit.
- Market-flow data starts pressing against the H side.

When all conditions are true, the second account opens one reverse guard
contract. The guard exits when H exits, or earlier if H recovers and prints a
new favorable close beyond the best H close seen at guard entry.

This file is research-only. It does not place orders.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
TV_DOC_DIR = BASE_DIR / "tv_doc"
RESEARCH_OUTPUT_DIR = TV_DOC_DIR / "research_outputs"
H_TRADE_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_PATH = TV_DOC_DIR / "mxf_value.csv"
PRICE_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
REPORT_PATH = RESEARCH_OUTPUT_DIR / "h_profit_retrace_guard_research.md"
TRADE_PATH = RESEARCH_OUTPUT_DIR / "h_profit_retrace_guard_trades.csv"
RESEARCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

POINT_VALUE_TWD = 10.0
ROUND_TRIP_COST_POINTS = 3.0


@dataclass(frozen=True)
class Params:
    profit_trigger_points: float
    giveback_points: float
    giveback_ratio: float
    mxf_avg_threshold: float
    require_mxf_signal: bool
    recovery_stop_buffer_points: float
    guard_stop_loss_points: float
    min_remaining_minutes: int


PRACTICAL_PARAMS = Params(
    profit_trigger_points=750.0,
    giveback_points=500.0,
    giveback_ratio=0.5,
    mxf_avg_threshold=500.0,
    require_mxf_signal=True,
    recovery_stop_buffer_points=0.0,
    guard_stop_loss_points=300.0,
    min_remaining_minutes=5,
)


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


def parse_float(value: object) -> float | None:
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def side_points(side: str, entry: float, exit_: float) -> float:
    return exit_ - entry if side == "bull" else entry - exit_


def reverse_side(side: str) -> str:
    return "bear" if side == "bull" else "bull"


def load_prices() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with PRICE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            time_value = row.get("Record Time")
            close = parse_float(row.get("Close"))
            if time_value and close is not None:
                rows.append({"time": parse_dt(time_value), "price": close})
    return rows


def load_mxf_by_time() -> dict[datetime, dict[str, Any]]:
    rows: dict[datetime, dict[str, Any]] = {}
    with MXF_VALUE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            time_value = row.get("time")
            avg = parse_float(row.get("mtx_bvav_avg"))
            if not time_value or avg is None:
                continue
            rows[parse_dt(time_value)] = {
                "avg": avg,
                "signal": str(row.get("signal") or "").strip().lower(),
                "trend": str(row.get("trend") or "").strip().lower(),
            }
    return rows


def load_h_intervals(start: datetime, end: datetime) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    with H_TRADE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp = parse_dt(str(row.get("timestamp") or ""))
            action = str(row.get("action") or "").strip().lower()
            side = str(row.get("side") or "").strip().lower()
            price = parse_float(row.get("price"))
            if price is None:
                continue

            if action == "enter" and side in {"bull", "bear"}:
                current = {"entry_time": timestamp, "side": side, "entry_price": price}
                continue

            if action != "exiting" or current is None:
                continue

            pnl_cash = parse_float(row.get("pnl"))
            if pnl_cash is None:
                current = None
                continue

            interval = {
                **current,
                "exit_time": timestamp,
                "exit_price": price,
                "h_points": pnl_cash / POINT_VALUE_TWD,
            }
            if interval["exit_time"] >= start and interval["entry_time"] <= end:
                intervals.append(interval)
            current = None
    return intervals


def mxf_opposes_h(mxf: dict[str, Any] | None, h_side: str, threshold: float, require_signal: bool) -> bool:
    if mxf is None:
        return False
    avg = float(mxf["avg"])
    if h_side == "bull":
        avg_ok = avg <= -threshold
        signal_ok = mxf["signal"] == "bear" and mxf["trend"] == "death"
    else:
        avg_ok = avg >= threshold
        signal_ok = mxf["signal"] == "bull" and mxf["trend"] == "gold"
    return avg_ok and (signal_ok if require_signal else True)


def rows_for_interval(price_rows: list[dict[str, Any]], interval: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in price_rows
        if interval["entry_time"] <= row["time"] <= interval["exit_time"]
    ]


def build_interval_rows(
    intervals: list[dict[str, Any]],
    price_rows: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    return [(interval, rows_for_interval(price_rows, interval)) for interval in intervals]


def run_params(
    interval_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    mxf_by_time: dict[datetime, dict[str, Any]],
    params: Params,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    interval_results: list[dict[str, Any]] = []

    for interval, rows in interval_rows:
        h_points = float(interval["h_points"])
        guard_points = 0.0
        trade: dict[str, Any] | None = None
        max_favorable = 0.0
        max_favorable_time: datetime | None = None
        max_favorable_price = float(interval["entry_price"])

        for row in rows:
            favorable = side_points(interval["side"], float(interval["entry_price"]), float(row["price"]))
            if favorable > max_favorable:
                max_favorable = favorable
                max_favorable_time = row["time"]
                max_favorable_price = float(row["price"])

            giveback = max_favorable - favorable
            if row["time"] > interval["exit_time"] - timedelta(minutes=params.min_remaining_minutes):
                continue
            if max_favorable < params.profit_trigger_points:
                continue
            if giveback < params.giveback_points:
                continue
            if max_favorable <= 0 or giveback / max_favorable < params.giveback_ratio:
                continue
            if not mxf_opposes_h(mxf_by_time.get(row["time"]), interval["side"], params.mxf_avg_threshold, params.require_mxf_signal):
                continue

            guard_side = reverse_side(interval["side"])
            entry_row = row
            entry_max_favorable = max_favorable
            exit_row = rows[-1]
            reason = "exit: H strategy ended"
            for later in rows:
                if later["time"] <= entry_row["time"]:
                    continue
                guard_unrealized = side_points(guard_side, float(entry_row["price"]), float(later["price"]))
                if guard_unrealized <= -params.guard_stop_loss_points:
                    exit_row = later
                    reason = "stop: guard stop loss"
                    break
                later_favorable = side_points(
                    interval["side"],
                    float(interval["entry_price"]),
                    float(later["price"]),
                )
                if later_favorable >= entry_max_favorable + params.recovery_stop_buffer_points:
                    exit_row = later
                    reason = "stop: H favorable close recovered"
                    break

            guard_points = side_points(guard_side, float(entry_row["price"]), float(exit_row["price"])) - ROUND_TRIP_COST_POINTS
            trade = {
                "h_entry_time": interval["entry_time"],
                "h_exit_time": interval["exit_time"],
                "h_side": interval["side"],
                "h_entry_price": interval["entry_price"],
                "h_exit_price": interval["exit_price"],
                "h_points": h_points,
                "max_favorable_points": max_favorable,
                "max_favorable_time": max_favorable_time,
                "max_favorable_price": max_favorable_price,
                "giveback_points": giveback,
                "entry_time": entry_row["time"],
                "exit_time": exit_row["time"],
                "guard_side": guard_side,
                "entry_price": float(entry_row["price"]),
                "exit_price": float(exit_row["price"]),
                "entry_mxf_avg": float(mxf_by_time[entry_row["time"]]["avg"]),
                "entry_mxf_signal": mxf_by_time[entry_row["time"]]["signal"],
                "entry_mxf_trend": mxf_by_time[entry_row["time"]]["trend"],
                "guard_points": guard_points,
                "combined_points": h_points + guard_points,
                "reason": reason,
            }
            trades.append(trade)
            break

        interval_results.append(
            {
                **interval,
                "guard_points": guard_points,
                "combined_points": h_points + guard_points,
                "triggered": trade is not None,
            }
        )

    return {"trades": trades, "intervals": interval_results}


def max_drawdown(values: list[float]) -> float:
    peak = 0.0
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = max(mdd, peak - value)
    return mdd


def summary(points: list[float]) -> dict[str, float | int]:
    wins = [point for point in points if point > 0]
    running = 0.0
    equity: list[float] = []
    for point in points:
        running += point
        equity.append(running)
    return {
        "trades": len(points),
        "points": round(sum(points), 2),
        "cash_twd": round(sum(points) * POINT_VALUE_TWD, 2),
        "wins": len(wins),
        "losses": len(points) - len(wins),
        "win_rate": round(len(wins) / len(points) * 100, 2) if points else 0.0,
        "max_loss": round(min(points), 2) if points else 0.0,
        "max_drawdown": round(max_drawdown(equity), 2),
    }


def optimize(
    interval_rows: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    mxf_by_time: dict[datetime, dict[str, Any]],
) -> list[dict[str, Any]]:
    h_summary = summary([float(interval["h_points"]) for interval, _ in interval_rows])
    results: list[dict[str, Any]] = []
    for profit_trigger in (750, 1000, 1200, 1500):
        for giveback_points in (400, 600, 800):
            for giveback_ratio in (0.25, 0.35, 0.5):
                for avg_threshold in (500, 1000, 1500):
                    for require_signal in (False, True):
                        for recovery_buffer in (0, 200):
                            for guard_stop in (200, 300, 400):
                                for min_remaining in (0, 5):
                                    params = Params(
                                        profit_trigger_points=float(profit_trigger),
                                        giveback_points=float(giveback_points),
                                        giveback_ratio=float(giveback_ratio),
                                        mxf_avg_threshold=float(avg_threshold),
                                        require_mxf_signal=require_signal,
                                        recovery_stop_buffer_points=float(recovery_buffer),
                                        guard_stop_loss_points=float(guard_stop),
                                        min_remaining_minutes=min_remaining,
                                    )
                                    run = run_params(interval_rows, mxf_by_time, params)
                                    combined = [float(row["combined_points"]) for row in run["intervals"]]
                                    guard = [float(row["guard_points"]) for row in run["intervals"]]
                                    triggered = sum(1 for row in run["intervals"] if row["triggered"])
                                    combined_summary = summary(combined)
                                    guard_summary = summary(guard)
                                    results.append(
                                        {
                                            "params": params,
                                            "triggered": triggered,
                                            "combined": combined_summary,
                                            "guard": guard_summary,
                                            "h": h_summary,
                                            "run": run,
                                        }
                                    )

    return sorted(
        results,
        key=lambda item: (
            -float(item["combined"]["points"]),
            float(item["combined"]["max_drawdown"]),
            -float(item["combined"]["max_loss"]),
            abs(int(item["triggered"]) - 4),
        ),
    )


def fmt_params(params: Params) -> str:
    signal = "yes" if params.require_mxf_signal else "no"
    return (
        f"profit>={params.profit_trigger_points:g}, "
        f"giveback>={params.giveback_points:g}/{params.giveback_ratio:.0%}, "
        f"avg>={params.mxf_avg_threshold:g}, signal={signal}, "
        f"recover_stop={params.recovery_stop_buffer_points:g}, "
        f"guard_stop={params.guard_stop_loss_points:g}, remain>={params.min_remaining_minutes}m"
    )


def write_trades(path: Path, trades: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "h_entry_time",
                "h_exit_time",
                "h_side",
                "h_entry_price",
                "h_exit_price",
                "h_points",
                "max_favorable_points",
                "max_favorable_time",
                "max_favorable_price",
                "giveback_points",
                "guard_side",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "entry_mxf_avg",
                "entry_mxf_signal",
                "entry_mxf_trend",
                "guard_points",
                "combined_points",
                "reason",
            ]
        )
        for trade in trades:
            writer.writerow(
                [
                    trade["h_entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    trade["h_exit_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    trade["h_side"],
                    trade["h_entry_price"],
                    trade["h_exit_price"],
                    round(float(trade["h_points"]), 2),
                    round(float(trade["max_favorable_points"]), 2),
                    trade["max_favorable_time"].strftime("%Y-%m-%d %H:%M:%S") if trade["max_favorable_time"] else "",
                    trade["max_favorable_price"],
                    round(float(trade["giveback_points"]), 2),
                    trade["guard_side"],
                    trade["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    trade["exit_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    trade["entry_price"],
                    trade["exit_price"],
                    trade["entry_mxf_avg"],
                    trade["entry_mxf_signal"],
                    trade["entry_mxf_trend"],
                    round(float(trade["guard_points"]), 2),
                    round(float(trade["combined_points"]), 2),
                    trade["reason"],
                ]
            )


def md_table_for_top(results: list[dict[str, Any]]) -> str:
    rows = []
    for index, item in enumerate(results[:12], start=1):
        rows.append(
            f"| {index} | {fmt_params(item['params'])} | {item['triggered']} | "
            f"{item['combined']['points']:,} | {item['combined']['max_loss']:,} | "
            f"{item['combined']['max_drawdown']:,} | {item['guard']['points']:,} |"
        )
    return "\n".join(rows)


def md_table_for_trades(trades: list[dict[str, Any]]) -> str:
    if not trades:
        return "| none | | | | | | | |\n"
    rows = []
    for trade in trades:
        rows.append(
            f"| {trade['h_entry_time']:%m-%d %H:%M} | {trade['h_exit_time']:%m-%d %H:%M} | "
            f"{trade['h_side']} | {float(trade['h_points']):,.0f} | "
            f"{float(trade['max_favorable_points']):,.0f} | {float(trade['giveback_points']):,.0f} | "
            f"{trade['guard_side']} | {trade['entry_time']:%m-%d %H:%M} | {trade['exit_time']:%m-%d %H:%M} | "
            f"{float(trade['guard_points']):,.0f} | {float(trade['combined_points']):,.0f} | {trade['reason']} |"
        )
    return "\n".join(rows)


def write_report(
    results: list[dict[str, Any]],
    practical: dict[str, Any],
    intervals: list[dict[str, Any]],
    price_rows: list[dict[str, Any]],
) -> None:
    h_summary = summary([float(row["h_points"]) for row in intervals])
    best = results[0]
    practical_combined = summary([float(row["combined_points"]) for row in practical["intervals"]])
    practical_guard = summary([float(row["guard_points"]) for row in practical["intervals"]])
    report = f"""# H Profit Retrace Guard Research

## Goal

Avoid the case where H once has a large unrealized profit, then gives too much
back before the official H exit. This is a reverse guard, not a replacement for
H: it waits for H profit first, then enters only after a large retrace with
market-flow pressure against H.

## Baseline Window

- 1m price window: {price_rows[0]['time']:%Y-%m-%d %H:%M:%S} to {price_rows[-1]['time']:%Y-%m-%d %H:%M:%S}
- H intervals tested: {h_summary['trades']}
- H points: {h_summary['points']:,}
- H cash: {h_summary['cash_twd']:,}
- H max single loss: {h_summary['max_loss']:,} points
- H max drawdown: {h_summary['max_drawdown']:,} points

## Rule

For each active H trade:

1. Track H unrealized close-to-close profit.
2. If the best open profit reaches `profit>=`.
3. If current profit gives back at least `giveback>=` points and the specified ratio of best open profit.
4. If `mtx_bvav_avg` and optionally `signal/trend` oppose H.
5. Open one reverse guard contract.
6. Exit the guard when H exits, or when H recovers to a new favorable close beyond the best H close seen at guard entry.

Round-trip cost: {ROUND_TRIP_COST_POINTS:g} points per guard trade.

## Top Optimizer Results

| Rank | Params | Guard Trades | Combined Points | Combined Max Loss | Combined MDD | Guard Points |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
{md_table_for_top(results)}

## Best Optimizer Params

`{fmt_params(best['params'])}`

## Practical Draft Choice

`{fmt_params(PRACTICAL_PARAMS)}`

- Guard trades: {len(practical['trades'])}
- Guard points: {practical_guard['points']:,}
- Combined points: {practical_combined['points']:,}
- Combined max single loss: {practical_combined['max_loss']:,} points
- Combined MDD: {practical_combined['max_drawdown']:,} points

| H Entry | H Exit | H Side | H Points | Max Open Profit | Giveback At Entry | Guard | Guard Entry | Guard Exit | Guard Points | Combined | Reason |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | --- |
{md_table_for_trades(practical['trades'])}
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    price_rows = load_prices()
    if not price_rows:
        raise SystemExit("No price rows found.")
    mxf_by_time = load_mxf_by_time()
    intervals = load_h_intervals(price_rows[0]["time"], price_rows[-1]["time"])
    interval_rows = build_interval_rows(intervals, price_rows)
    results = optimize(interval_rows, mxf_by_time)
    practical = run_params(interval_rows, mxf_by_time, PRACTICAL_PARAMS)
    write_trades(TRADE_PATH, practical["trades"])
    write_report(results, practical, intervals, price_rows)

    output = {
        "baseline": summary([float(row["h_points"]) for row in intervals]),
        "best_params": fmt_params(results[0]["params"]),
        "best_triggered": results[0]["triggered"],
        "best_combined": results[0]["combined"],
        "practical_params": fmt_params(PRACTICAL_PARAMS),
        "practical_triggered": len(practical["trades"]),
        "practical_combined": summary([float(row["combined_points"]) for row in practical["intervals"]]),
        "practical_guard": summary([float(row["guard_points"]) for row in practical["intervals"]]),
        "report": str(REPORT_PATH),
        "trades": str(TRADE_PATH),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
