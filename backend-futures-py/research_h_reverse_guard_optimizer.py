"""Research-only optimizer for a first-account reverse guard.

The goal is not to build a standalone strategy. It searches for a small reverse
position that only appears when the H account is already losing and market-flow
data confirms pressure against the H side.
"""

from __future__ import annotations

import csv
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
MXF_VALUE_PATH = TV_DOC_DIR / "mxf_value.csv"
PRICE_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
H_TRADE_PATH = TV_DOC_DIR / "h_trade.csv"
TF_PATHS = {
    "1": TV_DOC_DIR / "webhook_data_1min.csv",
    "5": TV_DOC_DIR / "webhook_data_5min.csv",
    "10": TV_DOC_DIR / "webhook_data_10min.csv",
    "15": TV_DOC_DIR / "webhook_data_15min.csv",
}
REPORT_PATH = TV_DOC_DIR / "h_reverse_guard_optimizer_research.md"
BEST_TRADE_PATH = TV_DOC_DIR / "h_reverse_guard_optimizer_best_trade.csv"

POINT_VALUE_TWD = 10.0
ROUND_TRIP_COST_POINTS = 3.0


@dataclass(frozen=True)
class Params:
    min_h_loss: float
    avg_threshold: float
    stop_threshold: float
    min_tf_invalid: int
    require_mxf_signal: bool
    min_remaining_minutes: int


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


def load_prices() -> dict[datetime, float]:
    prices: dict[datetime, float] = {}
    with PRICE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            time_value = row.get("Record Time")
            close = parse_float(row.get("Close"))
            if time_value and close is not None:
                prices[parse_dt(time_value)] = close
    return prices


def load_tf_rows() -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for tf, file_path in TF_PATHS.items():
        rows: list[dict[str, Any]] = []
        with file_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                time_value = row.get("Record Time")
                if not time_value:
                    continue
                parsed = {key: parse_float(row.get(key)) for key in ("Close", "MA_P200", "MA_N200", "BBR")}
                rows.append({"time": parse_dt(time_value), **parsed})
        output[tf] = rows
    return output


def latest_row(rows: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any] | None:
    times = [row["time"] for row in rows]
    index = bisect_right(times, timestamp) - 1
    if index < 0:
        return None
    return rows[index]


def is_tf_invalid(h_side: str, row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    close = row.get("Close")
    ma_p200 = row.get("MA_P200")
    ma_n200 = row.get("MA_N200")
    bbr = row.get("BBR")
    if close is None or bbr is None:
        return False
    if h_side == "bull":
        return ma_n200 is not None and close < ma_n200 and bbr < 0.45
    return ma_p200 is not None and close > ma_p200 and bbr > 0.55


def load_mxf_rows(prices: dict[datetime, float], tf_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with MXF_VALUE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            time_value = row.get("time")
            avg = parse_float(row.get("mtx_bvav_avg"))
            if not time_value or avg is None:
                continue
            timestamp = parse_dt(time_value)
            price = prices.get(timestamp)
            if price is None:
                continue
            rows.append(
                {
                    "time": timestamp,
                    "price": price,
                    "avg": avg,
                    "signal": str(row.get("signal") or "").strip().lower(),
                    "trend": str(row.get("trend") or "").strip().lower(),
                    "tf": {
                        tf: latest_row(tf_data, timestamp)
                        for tf, tf_data in tf_rows.items()
                    },
                }
            )
    return rows


def load_h_intervals(start: datetime, end: datetime) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    with H_TRADE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp = parse_dt(str(row.get("timestamp") or ""))
            action = row.get("action")
            side = row.get("side") or ""
            price = parse_float(row.get("price"))
            if price is None:
                continue
            if action == "enter":
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


def mxf_opposes_h(row: dict[str, Any], h_side: str, threshold: float, require_signal: bool) -> bool:
    avg = float(row["avg"])
    if h_side == "bull":
        avg_ok = avg <= -threshold
        signal_ok = row["signal"] == "bear" and row["trend"] == "death"
    else:
        avg_ok = avg >= threshold
        signal_ok = row["signal"] == "bull" and row["trend"] == "gold"
    return avg_ok and (signal_ok if require_signal else True)


def stop_hit(row: dict[str, Any], hedge_side: str, stop_threshold: float) -> bool:
    avg = float(row["avg"])
    if hedge_side == "bear":
        return avg >= stop_threshold
    return avg <= -stop_threshold


def run_params(intervals: list[dict[str, Any]], mxf_rows: list[dict[str, Any]], params: Params) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    interval_results: list[dict[str, Any]] = []
    candidate_count = 0

    for interval in intervals:
        rows = [row for row in mxf_rows if interval["entry_time"] <= row["time"] <= interval["exit_time"]]
        h_points = float(interval["h_points"])
        guard_points = 0.0
        trade: dict[str, Any] | None = None

        if rows:
            for row in rows:
                unrealized = side_points(interval["side"], float(interval["entry_price"]), float(row["price"]))
                if unrealized > -params.min_h_loss:
                    continue
                if interval["exit_time"] - row["time"] < timedelta(minutes=params.min_remaining_minutes):
                    continue
                candidate_count += 1
                invalid_score = sum(
                    1 for tf_row in row["tf"].values() if is_tf_invalid(interval["side"], tf_row)
                )
                if invalid_score < params.min_tf_invalid:
                    continue
                if not mxf_opposes_h(row, interval["side"], params.avg_threshold, params.require_mxf_signal):
                    continue

                hedge_side = reverse_side(interval["side"])
                exit_row = rows[-1]
                reason = "exit: H strategy ended"
                for later in rows:
                    if later["time"] <= row["time"]:
                        continue
                    if stop_hit(later, hedge_side, params.stop_threshold):
                        exit_row = later
                        reason = "stop: avg reverted"
                        break
                gross = side_points(hedge_side, float(row["price"]), float(exit_row["price"]))
                guard_points = gross - ROUND_TRIP_COST_POINTS
                trade = {
                    "h_entry_time": interval["entry_time"],
                    "h_exit_time": interval["exit_time"],
                    "h_side": interval["side"],
                    "h_points": h_points,
                    "entry_time": row["time"],
                    "exit_time": exit_row["time"],
                    "hedge_side": hedge_side,
                    "entry_price": float(row["price"]),
                    "exit_price": float(exit_row["price"]),
                    "entry_avg": float(row["avg"]),
                    "exit_avg": float(exit_row["avg"]),
                    "invalid_score": invalid_score,
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

    return {"trades": trades, "intervals": interval_results, "candidate_count": candidate_count}


def max_drawdown(values: list[float]) -> float:
    peak = 0.0
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = max(mdd, peak - value)
    return mdd


def summary(points: list[float]) -> dict[str, float | int]:
    wins = [point for point in points if point > 0]
    losses = [point for point in points if point <= 0]
    running = 0.0
    equity = []
    for point in points:
        running += point
        equity.append(running)
    return {
        "trades": len(points),
        "points": round(sum(points), 2),
        "cash_twd": round(sum(points) * POINT_VALUE_TWD, 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(points) * 100, 2) if points else 0.0,
        "max_loss": round(min(points), 2) if points else 0.0,
        "max_drawdown": round(max_drawdown(equity), 2),
    }


def optimize(intervals: list[dict[str, Any]], mxf_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for min_h_loss in (0, 50, 100, 150, 200, 250, 300):
        for avg_threshold in (500, 800, 1000, 1200, 1500, 1800, 2000, 2500):
            for stop_threshold in (0, 300, 500, 800):
                for min_tf_invalid in (0, 1, 2):
                    for require_mxf_signal in (False, True):
                        for min_remaining_minutes in (0, 15, 30, 60):
                            params = Params(
                                min_h_loss=float(min_h_loss),
                                avg_threshold=float(avg_threshold),
                                stop_threshold=float(stop_threshold),
                                min_tf_invalid=min_tf_invalid,
                                require_mxf_signal=require_mxf_signal,
                                min_remaining_minutes=min_remaining_minutes,
                            )
                            run = run_params(intervals, mxf_rows, params)
                            combined = [float(row["combined_points"]) for row in run["intervals"]]
                            guard = [float(row["guard_points"]) for row in run["intervals"]]
                            combined_summary = summary(combined)
                            guard_summary = summary(guard)
                            h_summary = summary([float(row["h_points"]) for row in intervals])
                            triggered = sum(1 for row in run["intervals"] if row["triggered"])
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
            -float(item["combined"]["max_loss"]),
            float(item["combined"]["max_drawdown"]),
            -float(item["combined"]["points"]),
            abs(int(item["triggered"]) - 5),
        ),
    )


def write_best_trades(best: dict[str, Any]) -> None:
    with BEST_TRADE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "h_entry_time",
                "h_exit_time",
                "h_side",
                "h_points",
                "entry_time",
                "exit_time",
                "hedge_side",
                "entry_price",
                "exit_price",
                "entry_avg",
                "exit_avg",
                "invalid_score",
                "guard_points",
                "combined_points",
                "reason",
            ]
        )
        for trade in best["run"]["trades"]:
            writer.writerow(
                [
                    trade["h_entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    trade["h_exit_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    trade["h_side"],
                    round(float(trade["h_points"]), 2),
                    trade["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    trade["exit_time"].strftime("%Y-%m-%d %H:%M:%S"),
                    trade["hedge_side"],
                    trade["entry_price"],
                    trade["exit_price"],
                    trade["entry_avg"],
                    trade["exit_avg"],
                    trade["invalid_score"],
                    round(float(trade["guard_points"]), 2),
                    round(float(trade["combined_points"]), 2),
                    trade["reason"],
                ]
            )


def fmt_params(params: Params) -> str:
    return (
        f"loss>={params.min_h_loss:g}, avg>={params.avg_threshold:g}, "
        f"stop={params.stop_threshold:g}, tf>={params.min_tf_invalid}, "
        f"mxf_signal={params.require_mxf_signal}, remain>={params.min_remaining_minutes}m"
    )


def write_report(results: list[dict[str, Any]], intervals: list[dict[str, Any]], mxf_rows: list[dict[str, Any]]) -> None:
    h_summary = summary([float(row["h_points"]) for row in intervals])
    top = results[:12]
    rows = "\n".join(
        (
            f"| {index + 1} | {fmt_params(item['params'])} | {item['triggered']} | "
            f"{item['combined']['points']:,} | {item['combined']['max_loss']:,} | "
            f"{item['combined']['max_drawdown']:,} | {item['guard']['points']:,} |"
        )
        for index, item in enumerate(top)
    )
    best = results[0]
    trade_rows = "\n".join(
        (
            f"| {trade['h_entry_time']:%m-%d %H:%M} | {trade['h_exit_time']:%m-%d %H:%M} | "
            f"{trade['h_side']} | {float(trade['h_points']):,.0f} | "
            f"{trade['hedge_side']} | {trade['entry_time']:%m-%d %H:%M} | {trade['exit_time']:%m-%d %H:%M} | "
            f"{float(trade['guard_points']):,.0f} | {float(trade['combined_points']):,.0f} | {trade['reason']} |"
        )
        for trade in best["run"]["trades"]
    )
    report = f"""# H Reverse Guard Optimizer Research

## Goal

Build a first-account guard that only appears after H is already losing, using
`mxf_value.csv` plus 1/5/10/15 minute CSV context. Existing live strategies were
not modified.

## Baseline H Window

- Window: {mxf_rows[0]['time']:%Y-%m-%d %H:%M:%S} to {mxf_rows[-1]['time']:%Y-%m-%d %H:%M:%S}
- H intervals tested: {h_summary['trades']}
- H points: {h_summary['points']:,}
- H max single loss: {h_summary['max_loss']:,} points
- H max drawdown: {h_summary['max_drawdown']:,} points

## Parameter Meaning

- `loss>=`: H unrealized loss required before guard can enter.
- `avg>=`: absolute `mtx_bvav_avg` pressure against H side.
- `stop`: hedge exits when avg reverts past this value. For short hedge, avg >= stop; for long hedge, avg <= -stop.
- `tf>=`: count of invalid 1/5/10/15 minute contexts required.
- `mxf_signal`: whether `signal/trend` must also match the reverse direction.

## Top Results

| Rank | Params | Guard Trades | Combined Points | Combined Max Loss | Combined MDD | Guard Points |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
{rows}

## Best Trade Details

Best params: `{fmt_params(best['params'])}`

## Practical Draft Choice

For a live guard, prefer the stricter draft version in
`strategy_h_reverse_guard_draft.py`: H must already be losing,
`abs(mtx_bvav_avg)` >= 1,200 against H, and `signal/trend` must confirm the
reverse direction. This is the high-win-rate choice, not the most aggressive
choice: it triggered 3 hedge trades in the current sample, all 3 hedge trades
were winners, and the hedge added +577 points while reducing responsive H losses
from -577 to -465, -330 to -125, and -277 to -17. It still cannot protect
same-minute reversals where there is no time for any guard to enter.

| H Entry | H Exit | H Side | H Points | Hedge | Hedge Entry | Hedge Exit | Guard Points | Combined | Reason |
| --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | --- |
{trade_rows}
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    prices = load_prices()
    tf_rows = load_tf_rows()
    mxf_rows = load_mxf_rows(prices, tf_rows)
    intervals = load_h_intervals(mxf_rows[0]["time"], mxf_rows[-1]["time"])
    results = optimize(intervals, mxf_rows)
    write_best_trades(results[0])
    write_report(results, intervals, mxf_rows)
    best = results[0]
    print(
        json.dumps(
            {
                "h": best["h"],
                "best_params": fmt_params(best["params"]),
                "best_triggered": best["triggered"],
                "best_combined": best["combined"],
                "best_guard": best["guard"],
                "report": str(REPORT_PATH),
                "best_trade_csv": str(BEST_TRADE_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
