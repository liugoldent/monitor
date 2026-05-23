"""Research-only backtest for MA960 + MXF flow continuation signals."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
TV_DOC_DIR = BASE_DIR / "tv_doc"
PRICE_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
MXF_PATH = TV_DOC_DIR / "mxf_value.csv"
OUTPUT_DIR = Path(__file__).resolve().parent
REPORT_PATH = OUTPUT_DIR / "ma960_flow_strategy_research.md"
TRADE_PATH = OUTPUT_DIR / "ma960_flow_strategy_trade.csv"

ROUND_TRIP_COST_POINTS = 3.0


@dataclass(frozen=True)
class Params:
    max_dist_above_ma960: float
    min_ma960_slope_15: float
    take_profit: float
    stop_loss: float
    max_hold_minutes: int
    require_trend_gold: bool


PRACTICAL_PARAMS = Params(
    max_dist_above_ma960=60.0,
    min_ma960_slope_15=0.0,
    take_profit=120.0,
    stop_loss=60.0,
    max_hold_minutes=30,
    require_trend_gold=False,
)


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")


def parse_float(value: object) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def load_price_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[datetime] = set()
    with PRICE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            time_value = row.get("TradingView Time") or row.get("Record Time")
            close = parse_float(row.get("Close"))
            ma960 = parse_float(row.get("MA_960"))
            if not time_value or close is None or ma960 is None:
                continue
            timestamp = parse_dt(time_value)
            if timestamp in seen:
                continue
            seen.add(timestamp)
            rows.append({"time": timestamp, "close": close, "ma960": ma960})
    rows.sort(key=lambda item: item["time"])
    for index, row in enumerate(rows):
        row["ma960_slope_15"] = row["ma960"] - rows[index - 15]["ma960"] if index >= 15 else None
    return rows


def load_mxf_rows() -> dict[datetime, dict[str, Any]]:
    output: dict[datetime, dict[str, Any]] = {}
    with MXF_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            time_value = row.get("time")
            if not time_value:
                continue
            tx = parse_float(row.get("tx_bvav"))
            dealer = parse_float(row.get("mtx_bvav"))
            retail = parse_float(row.get("mtx_tbta"))
            avg = parse_float(row.get("mtx_bvav_avg"))
            if tx is None or dealer is None or retail is None:
                continue
            output[parse_dt(time_value)] = {
                "tx_bvav": tx,
                "mtx_bvav": dealer,
                "mtx_tbta": retail,
                "mtx_bvav_avg": avg,
                "signal": str(row.get("signal") or "").strip().lower(),
                "trend": str(row.get("trend") or "").strip().lower(),
            }
    return output


def join_rows() -> list[dict[str, Any]]:
    price_rows = load_price_rows()
    mxf_by_time = load_mxf_rows()
    rows: list[dict[str, Any]] = []
    for row in price_rows:
        mxf = mxf_by_time.get(row["time"])
        if not mxf:
            continue
        rows.append({**row, **mxf, "dist_to_ma960": row["close"] - row["ma960"]})
    return rows


def signal_type(row: dict[str, Any], params: Params) -> str | None:
    if row["tx_bvav"] <= 0 or row["mtx_bvav"] <= 0:
        return None
    if not (0 <= row["dist_to_ma960"] <= params.max_dist_above_ma960):
        return None
    slope = row.get("ma960_slope_15")
    if slope is None or slope <= params.min_ma960_slope_15:
        return None
    if params.require_trend_gold and row.get("trend") != "gold":
        return None
    if row["mtx_tbta"] < 0:
        return "super_long"
    if row["mtx_tbta"] > 0:
        return "shakeout_long"
    return None


def backtest(rows: list[dict[str, Any]], params: Params) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        setup = signal_type(row, params)
        if setup is None:
            index += 1
            continue

        entry_index = index
        entry_price = float(row["close"])
        entry_time = row["time"]
        exit_index = entry_index
        exit_reason = "max hold"
        deadline = entry_time + timedelta(minutes=params.max_hold_minutes)

        for candidate_index in range(entry_index + 1, len(rows)):
            candidate = rows[candidate_index]
            pnl = float(candidate["close"]) - entry_price
            exit_index = candidate_index
            if pnl >= params.take_profit:
                exit_reason = "take profit"
                break
            if pnl <= -params.stop_loss:
                exit_reason = "stop loss"
                break
            if candidate["close"] < candidate["ma960"]:
                exit_reason = "close below ma960"
                break
            if candidate["time"] >= deadline:
                exit_reason = "max hold"
                break

        exit_row = rows[exit_index]
        gross = float(exit_row["close"]) - entry_price
        points = gross - ROUND_TRIP_COST_POINTS
        trades.append(
            {
                "setup": setup,
                "entry_time": entry_time,
                "exit_time": exit_row["time"],
                "entry_price": entry_price,
                "exit_price": float(exit_row["close"]),
                "points": points,
                "gross_points": gross,
                "reason": exit_reason,
                "tx_bvav": row["tx_bvav"],
                "mtx_bvav": row["mtx_bvav"],
                "mtx_tbta": row["mtx_tbta"],
                "mtx_bvav_avg": row["mtx_bvav_avg"],
                "ma960": row["ma960"],
                "dist_to_ma960": row["dist_to_ma960"],
                "ma960_slope_15": row["ma960_slope_15"],
            }
        )
        index = max(exit_index + 1, entry_index + 1)
    return trades


def max_drawdown(points: list[float]) -> float:
    running = 0.0
    peak = 0.0
    drawdown = 0.0
    for point in points:
        running += point
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    return drawdown


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    points = [float(trade["points"]) for trade in trades]
    wins = [point for point in points if point > 0]
    losses = [point for point in points if point <= 0]
    return {
        "trades": len(points),
        "points": round(sum(points), 1),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(points) * 100, 1) if points else 0.0,
        "avg": round(sum(points) / len(points), 1) if points else 0.0,
        "median": round(median(points), 1) if points else 0.0,
        "max_loss": round(min(points), 1) if points else 0.0,
        "max_drawdown": round(max_drawdown(points), 1),
    }


def optimize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for max_dist in (40, 60, 80, 100, 120):
        for min_slope in (0, 3, 5, 10):
            for take_profit in (80, 120, 160):
                for stop_loss in (60, 80, 120):
                    for max_hold in (30, 60, 120):
                        for require_trend_gold in (False, True):
                            params = Params(
                                max_dist_above_ma960=float(max_dist),
                                min_ma960_slope_15=float(min_slope),
                                take_profit=float(take_profit),
                                stop_loss=float(stop_loss),
                                max_hold_minutes=max_hold,
                                require_trend_gold=require_trend_gold,
                            )
                            trades = backtest(rows, params)
                            if len(trades) < 4:
                                continue
                            summary = summarize(trades)
                            results.append({"params": params, "summary": summary, "trades": trades})
    return sorted(
        results,
        key=lambda item: (
            -float(item["summary"]["points"]),
            float(item["summary"]["max_drawdown"]),
            -float(item["summary"]["win_rate"]),
        ),
    )


def fmt_params(params: Params) -> str:
    return (
        f"dist<= {params.max_dist_above_ma960:g}, slope15> {params.min_ma960_slope_15:g}, "
        f"tp={params.take_profit:g}, sl={params.stop_loss:g}, hold={params.max_hold_minutes}m, "
        f"gold={params.require_trend_gold}"
    )


def write_trade_csv(trades: list[dict[str, Any]]) -> None:
    with TRADE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "setup",
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
            "points",
            "gross_points",
            "reason",
            "tx_bvav",
            "mtx_bvav",
            "mtx_tbta",
            "mtx_bvav_avg",
            "ma960",
            "dist_to_ma960",
            "ma960_slope_15",
        ])
        writer.writeheader()
        writer.writerows(trades)


def write_report(rows: list[dict[str, Any]], results: list[dict[str, Any]], practical: dict[str, Any]) -> None:
    top_rows = "\n".join(
        f"| {index + 1} | {fmt_params(item['params'])} | {item['summary']['trades']} | "
        f"{item['summary']['points']} | {item['summary']['win_rate']}% | "
        f"{item['summary']['max_loss']} | {item['summary']['max_drawdown']} |"
        for index, item in enumerate(results[:12])
    )
    setup_rows = []
    for setup in ("super_long", "shakeout_long"):
        setup_trades = [trade for trade in practical["trades"] if trade["setup"] == setup]
        setup_summary = summarize(setup_trades)
        setup_rows.append(
            f"| {setup} | {setup_summary['trades']} | {setup_summary['points']} | "
            f"{setup_summary['win_rate']}% | {setup_summary['avg']} | "
            f"{setup_summary['max_loss']} | {setup_summary['max_drawdown']} |"
        )
    trade_rows = "\n".join(
        f"| {trade['setup']} | {trade['entry_time']:%m-%d %H:%M} | {trade['exit_time']:%m-%d %H:%M} | "
        f"{trade['entry_price']:.0f} | {trade['exit_price']:.0f} | {trade['points']:.0f} | "
        f"{trade['dist_to_ma960']:.0f} | {trade['ma960_slope_15']:.1f} | {trade['reason']} |"
        for trade in practical["trades"]
    )
    practical_summary = practical["summary"]
    report = f"""# MA960 Flow Strategy Research

## Idea

Use the relationship between price and `MA_960` together with MXF flow:

- `tx_bvav > 0` and `mtx_bvav > 0`: big money is long.
- `mtx_tbta < 0`: retail is against big money, classified as `super_long`.
- `mtx_tbta > 0`: retail and big money are both long, classified as `shakeout_long`.
- Entry is only considered when price is above but close to MA960, and MA960 is rising.

This is research only. It is not wired into `webhook_server.py`.

## Data

- Window: {rows[0]['time']:%Y-%m-%d %H:%M:%S} to {rows[-1]['time']:%Y-%m-%d %H:%M:%S}
- Joined 1m + MXF rows: {len(rows):,}

## Top Optimizer Results

| Rank | Params | Trades | Points | Win Rate | Max Loss | MDD |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
{top_rows}

## Practical Draft

Chosen params: `{fmt_params(PRACTICAL_PARAMS)}`

Summary: trades={practical_summary['trades']}, points={practical_summary['points']}, win_rate={practical_summary['win_rate']}%, max_loss={practical_summary['max_loss']}, mdd={practical_summary['max_drawdown']}.

| Setup | Trades | Points | Win Rate | Avg | Max Loss | MDD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(setup_rows)}

## Practical Trades

| Setup | Entry | Exit | Entry | Exit | Points | Dist960 | Slope15 | Reason |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
{trade_rows}
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    rows = join_rows()
    results = optimize(rows)
    practical_trades = backtest(rows, PRACTICAL_PARAMS)
    practical = {"params": PRACTICAL_PARAMS, "summary": summarize(practical_trades), "trades": practical_trades}
    write_trade_csv(practical_trades)
    write_report(rows, results, practical)
    print({
        "rows": len(rows),
        "best": {"params": fmt_params(results[0]["params"]), "summary": results[0]["summary"]},
        "practical": {"params": fmt_params(PRACTICAL_PARAMS), "summary": practical["summary"]},
        "report": str(REPORT_PATH),
        "trade_csv": str(TRADE_PATH),
    })


if __name__ == "__main__":
    main()
