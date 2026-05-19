"""Research-only hedge test for H bull losses using mtx_bvav_avg.

Rule under test:
- Only when the first H account is long (bull).
- Only after that H long is already losing.
- Enter one short hedge when mtx_bvav_avg < -1000.
- Stop the hedge when mtx_bvav_avg > 0.
- Otherwise take profit / exit when the first H strategy exits the long
  position, which is treated as the point where it can turn short.

This file is not imported by live trading code.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
MXF_VALUE_PATH = TV_DOC_DIR / "mxf_value.csv"
PRICE_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
H_TRADE_PATH = TV_DOC_DIR / "h_trade.csv"
REPORT_PATH = TV_DOC_DIR / "h_bull_loss_mtx_avg_short_hedge_research.md"
TRADE_PATH = TV_DOC_DIR / "h_bull_loss_mtx_avg_short_hedge_trade.csv"

POINT_VALUE_TWD = 10.0
ROUND_TRIP_COST_POINTS = 3.0
ENTRY_THRESHOLD = -1000.0
STOP_THRESHOLD = 0.0


@dataclass
class HedgeTrade:
    h_entry_time: datetime
    h_exit_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    entry_avg: float
    exit_avg: float
    h_entry_price: float
    h_exit_price: float
    h_points: float
    hedge_points: float
    hedge_net_points: float
    combined_points: float
    reason: str


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


def load_prices() -> dict[datetime, float]:
    prices: dict[datetime, float] = {}
    with PRICE_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            time_value = row.get("Record Time") or row.get("time")
            close = parse_float(row.get("Close") or row.get("close"))
            if not time_value or close is None:
                continue
            prices[parse_dt(time_value)] = close
    return prices


def load_mxf_rows(prices: dict[datetime, float]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with MXF_VALUE_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            time_value = row.get("time")
            avg = parse_float(row.get("mtx_bvav_avg"))
            if not time_value or avg is None:
                continue
            timestamp = parse_dt(time_value)
            price = prices.get(timestamp)
            if price is None:
                continue
            rows.append({"time": timestamp, "price": price, "avg": avg})
    return rows


def load_h_intervals() -> list[dict[str, object]]:
    intervals: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    with H_TRADE_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            timestamp = parse_dt(str(row.get("timestamp") or ""))
            action = row.get("action")
            side = row.get("side") or ""
            price = parse_float(row.get("price"))
            if price is None:
                continue

            if action == "enter":
                current = {
                    "entry_time": timestamp,
                    "side": side,
                    "entry_price": price,
                }
                continue

            if action != "exiting" or current is None:
                continue

            pnl_cash = parse_float(row.get("pnl"))
            if pnl_cash is None:
                current = None
                continue

            intervals.append(
                {
                    **current,
                    "exit_time": timestamp,
                    "exit_price": price,
                    "h_points": pnl_cash / POINT_VALUE_TWD,
                }
            )
            current = None
    return intervals


def short_points(entry: float, exit_: float) -> float:
    return entry - exit_


def backtest_hedge(
    intervals: list[dict[str, object]],
    mxf_rows: list[dict[str, object]],
) -> tuple[list[HedgeTrade], list[dict[str, object]]]:
    hedges: list[HedgeTrade] = []
    candidates: list[dict[str, object]] = []

    for interval in intervals:
        if interval["side"] != "bull":
            continue

        h_entry_time = interval["entry_time"]
        h_exit_time = interval["exit_time"]
        h_entry_price = float(interval["entry_price"])
        h_exit_price = float(interval["exit_price"])
        h_points = float(interval["h_points"])
        rows = [
            row
            for row in mxf_rows
            if h_entry_time <= row["time"] <= h_exit_time
        ]
        if not rows:
            continue

        candidates.append(interval)
        entry_row = next(
            (
                row
                for row in rows
                if float(row["price"]) < h_entry_price and float(row["avg"]) < ENTRY_THRESHOLD
            ),
            None,
        )
        if entry_row is None:
            continue

        following_rows = [row for row in rows if row["time"] > entry_row["time"]]
        stop_row = next(
            (row for row in following_rows if float(row["avg"]) > STOP_THRESHOLD),
            None,
        )
        if stop_row is not None:
            exit_row = stop_row
            reason = "stop: mtx_bvav_avg > 0"
        else:
            exit_row = rows[-1]
            reason = "exit: H bull strategy ended"

        hedge_points = short_points(float(entry_row["price"]), float(exit_row["price"]))
        hedge_net = hedge_points - ROUND_TRIP_COST_POINTS
        hedges.append(
            HedgeTrade(
                h_entry_time=h_entry_time,
                h_exit_time=h_exit_time,
                entry_time=entry_row["time"],
                exit_time=exit_row["time"],
                entry_price=float(entry_row["price"]),
                exit_price=float(exit_row["price"]),
                entry_avg=float(entry_row["avg"]),
                exit_avg=float(exit_row["avg"]),
                h_entry_price=h_entry_price,
                h_exit_price=h_exit_price,
                h_points=h_points,
                hedge_points=hedge_points,
                hedge_net_points=hedge_net,
                combined_points=h_points + hedge_net,
                reason=reason,
            )
        )

    return hedges, candidates


def max_drawdown(values: list[float]) -> float:
    peak = 0.0
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = max(mdd, peak - value)
    return mdd


def summarize(values: list[float]) -> dict[str, float | int]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    equity = []
    running = 0.0
    for value in values:
        running += value
        equity.append(running)
    return {
        "trades": len(values),
        "points": round(sum(values), 2),
        "cash_twd": round(sum(values) * POINT_VALUE_TWD, 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values) * 100, 2) if values else 0.0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown_points": round(max_drawdown(equity), 2),
    }


def write_trades(hedges: list[HedgeTrade]) -> None:
    with TRADE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "h_entry_time",
                "h_exit_time",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "entry_avg",
                "exit_avg",
                "h_entry_price",
                "h_exit_price",
                "h_points",
                "hedge_points",
                "hedge_net_points",
                "combined_points",
                "reason",
            ]
        )
        for hedge in hedges:
            writer.writerow(
                [
                    hedge.h_entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                    hedge.h_exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                    hedge.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                    hedge.exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                    hedge.entry_price,
                    hedge.exit_price,
                    hedge.entry_avg,
                    hedge.exit_avg,
                    hedge.h_entry_price,
                    hedge.h_exit_price,
                    round(hedge.h_points, 2),
                    round(hedge.hedge_points, 2),
                    round(hedge.hedge_net_points, 2),
                    round(hedge.combined_points, 2),
                    hedge.reason,
                ]
            )


def write_report(
    mxf_rows: list[dict[str, object]],
    hedges: list[HedgeTrade],
    candidates: list[dict[str, object]],
) -> None:
    hedge_summary = summarize([hedge.hedge_net_points for hedge in hedges])
    h_candidate_summary = summarize([float(item["h_points"]) for item in candidates])
    combined_summary = summarize(
        [
            hedge.combined_points
            for hedge in hedges
        ]
    )
    no_hedge_candidates = len(candidates) - len(hedges)
    stop_count = sum(1 for hedge in hedges if hedge.reason.startswith("stop"))
    h_end_count = len(hedges) - stop_count
    rows = "\n".join(
        f"| {hedge.h_entry_time:%Y-%m-%d %H:%M} | {hedge.h_exit_time:%Y-%m-%d %H:%M} | "
        f"{hedge.entry_time:%m-%d %H:%M} | {hedge.exit_time:%m-%d %H:%M} | "
        f"{hedge.h_points:,.0f} | {hedge.hedge_net_points:,.0f} | {hedge.combined_points:,.0f} | {hedge.reason} |"
        for hedge in hedges
    )
    report = f"""# H Bull Loss MTX AVG Short Hedge Research

## Rule

- First account H side must be `bull`.
- H long must already be losing at the MXF timestamp.
- Enter one short hedge when `mtx_bvav_avg < {ENTRY_THRESHOLD:g}`.
- Stop the short hedge when `mtx_bvav_avg > {STOP_THRESHOLD:g}`.
- If the stop does not happen first, exit when the H bull position ends.
- Cost model: hedge net subtracts {ROUND_TRIP_COST_POINTS:g} points per round trip.

## Data

- MXF rows joined with 1 minute close: {len(mxf_rows):,}
- Window: {mxf_rows[0]['time']:%Y-%m-%d %H:%M:%S} to {mxf_rows[-1]['time']:%Y-%m-%d %H:%M:%S}
- Candidate H bull intervals: {len(candidates)}
- Triggered hedge trades: {len(hedges)}
- Candidate H bull intervals without hedge trigger: {no_hedge_candidates}
- Hedge exits by avg stop: {stop_count}
- Hedge exits by H bull end: {h_end_count}

## Result

| Item | H Bull Candidates | Hedge Net | H + Hedge On Triggered Trades |
| --- | ---: | ---: | ---: |
| Trades | {h_candidate_summary['trades']} | {hedge_summary['trades']} | {combined_summary['trades']} |
| Points | {h_candidate_summary['points']:,} | {hedge_summary['points']:,} | {combined_summary['points']:,} |
| Cash TWD | {h_candidate_summary['cash_twd']:,} | {hedge_summary['cash_twd']:,} | {combined_summary['cash_twd']:,} |
| Wins | {h_candidate_summary['wins']} | {hedge_summary['wins']} | {combined_summary['wins']} |
| Losses | {h_candidate_summary['losses']} | {hedge_summary['losses']} | {combined_summary['losses']} |
| Win rate | {h_candidate_summary['win_rate']}% | {hedge_summary['win_rate']}% | {combined_summary['win_rate']}% |
| Max drawdown points | {h_candidate_summary['max_drawdown_points']:,} | {hedge_summary['max_drawdown_points']:,} | {combined_summary['max_drawdown_points']:,} |

## Triggered Trades

| H Entry | H Exit | Hedge Entry | Hedge Exit | H Points | Hedge Net | Combined | Exit Reason |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
{rows}
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    prices = load_prices()
    mxf_rows = load_mxf_rows(prices)
    intervals = load_h_intervals()
    hedges, candidates = backtest_hedge(intervals, mxf_rows)
    write_trades(hedges)
    write_report(mxf_rows, hedges, candidates)
    print(
        json.dumps(
            {
                "mxf_rows": len(mxf_rows),
                "candidate_h_bull_intervals": len(candidates),
                "hedge_trades": len(hedges),
                "h_candidates": summarize([float(item["h_points"]) for item in candidates]),
                "hedge_net": summarize([hedge.hedge_net_points for hedge in hedges]),
                "combined_triggered": summarize([hedge.combined_points for hedge in hedges]),
                "report": str(REPORT_PATH),
                "trade_csv": str(TRADE_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
