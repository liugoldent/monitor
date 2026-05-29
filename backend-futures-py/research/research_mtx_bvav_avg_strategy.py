"""Research-only backtest for mtx_bvav_avg sign strategy.

This script does not participate in live trading. It evaluates whether a simple
MXF value sign strategy could offset first-account H losses.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
TV_DOC_DIR = BASE_DIR / "tv_doc"
RESEARCH_OUTPUT_DIR = TV_DOC_DIR / "research_outputs"
MXF_VALUE_PATH = TV_DOC_DIR / "mxf_value.csv"
PRICE_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
H_TRADE_PATH = TV_DOC_DIR / "h_trade.csv"
REPORT_PATH = RESEARCH_OUTPUT_DIR / "mtx_bvav_avg_sign_strategy_research.md"
TRADE_PATH = RESEARCH_OUTPUT_DIR / "mtx_bvav_avg_sign_strategy_trade.csv"
RESEARCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

POINT_VALUE_TWD = 10.0
ROUND_TRIP_COST_POINTS = 3.0


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    side: str
    entry_price: float
    exit_price: float
    points: float
    net_points: float
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
            if not time_value or avg is None or avg == 0:
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
                    "side": "bull" if avg > 0 else "bear",
                }
            )
    return rows


def side_points(side: str, entry: float, exit_: float) -> float:
    return exit_ - entry if side == "bull" else entry - exit_


def backtest_sign_strategy(rows: list[dict[str, object]]) -> list[Trade]:
    trades: list[Trade] = []
    position_side = ""
    entry_time: datetime | None = None
    entry_price: float | None = None

    for row in rows:
        desired_side = str(row["side"])
        timestamp = row["time"]
        price = float(row["price"])

        if not position_side:
            position_side = desired_side
            entry_time = timestamp
            entry_price = price
            continue

        if desired_side == position_side:
            continue

        assert entry_time is not None
        assert entry_price is not None
        points = side_points(position_side, entry_price, price)
        trades.append(
            Trade(
                entry_time=entry_time,
                exit_time=timestamp,
                side=position_side,
                entry_price=entry_price,
                exit_price=price,
                points=points,
                net_points=points - ROUND_TRIP_COST_POINTS,
                reason="mtx_bvav_avg sign flip",
            )
        )
        position_side = desired_side
        entry_time = timestamp
        entry_price = price

    if position_side and entry_time is not None and entry_price is not None and rows:
        last = rows[-1]
        exit_time = last["time"]
        exit_price = float(last["price"])
        points = side_points(position_side, entry_price, exit_price)
        trades.append(
            Trade(
                entry_time=entry_time,
                exit_time=exit_time,
                side=position_side,
                entry_price=entry_price,
                exit_price=exit_price,
                points=points,
                net_points=points - ROUND_TRIP_COST_POINTS,
                reason="mark to last price",
            )
        )

    return trades


def max_drawdown(values: list[float]) -> float:
    peak = 0.0
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = max(mdd, peak - value)
    return mdd


def summarize_trades(trades: list[Trade], use_net: bool = False) -> dict[str, float | int]:
    pnls = [trade.net_points if use_net else trade.points for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    equity = []
    running = 0.0
    for pnl in pnls:
        running += pnl
        equity.append(running)
    return {
        "trades": len(pnls),
        "points": round(sum(pnls), 2),
        "cash_twd": round(sum(pnls) * POINT_VALUE_TWD, 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown_points": round(max_drawdown(equity), 2),
    }


def load_h_exits(start: datetime, end: datetime) -> list[dict[str, object]]:
    exits: list[dict[str, object]] = []
    with H_TRADE_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("action") != "exiting":
                continue
            timestamp = parse_dt(str(row.get("timestamp") or ""))
            if timestamp < start or timestamp > end:
                continue
            pnl_cash = parse_float(row.get("pnl"))
            if pnl_cash is None:
                continue
            exits.append(
                {
                    "timestamp": timestamp,
                    "side": row.get("side") or "",
                    "price": parse_float(row.get("price")) or 0.0,
                    "pnl_cash": pnl_cash,
                    "pnl_points": pnl_cash / POINT_VALUE_TWD,
                }
            )
    return exits


def load_h_intervals(start: datetime, end: datetime) -> list[dict[str, object]]:
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

            interval = {
                **current,
                "exit_time": timestamp,
                "exit_price": price,
                "h_points": pnl_cash / POINT_VALUE_TWD,
            }
            if timestamp >= start and current["entry_time"] <= end:
                intervals.append(interval)
            current = None
    return intervals


def build_minute_returns(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    returns: list[dict[str, object]] = []
    for previous, current in zip(rows, rows[1:]):
        side = str(previous["side"])
        prev_price = float(previous["price"])
        curr_price = float(current["price"])
        returns.append(
            {
                "time": current["time"],
                "side": side,
                "points": side_points(side, prev_price, curr_price),
            }
        )
    return returns


def summarize_interval_overlay(
    intervals: list[dict[str, object]],
    minute_returns: list[dict[str, object]],
) -> list[dict[str, object]]:
    overlay: list[dict[str, object]] = []
    for interval in intervals:
        entry_time = interval["entry_time"]
        exit_time = interval["exit_time"]
        sign_points = sum(
            float(item["points"])
            for item in minute_returns
            if entry_time < item["time"] <= exit_time
        )
        overlay.append(
            {
                **interval,
                "sign_points": round(sign_points, 2),
                "combined_points": round(float(interval["h_points"]) + sign_points, 2),
            }
        )
    return overlay


def summarize_h_exits(exits: list[dict[str, object]]) -> dict[str, float | int]:
    points = [float(row["pnl_points"]) for row in exits]
    losses = [p for p in points if p < 0]
    wins = [p for p in points if p > 0]
    equity = []
    running = 0.0
    for pnl in points:
        running += pnl
        equity.append(running)
    return {
        "exits": len(points),
        "points": round(sum(points), 2),
        "cash_twd": round(sum(points) * POINT_VALUE_TWD, 2),
        "wins": len(wins),
        "losses": len(losses),
        "loss_points": round(sum(losses), 2),
        "loss_cash_twd": round(sum(losses) * POINT_VALUE_TWD, 2),
        "max_drawdown_points": round(max_drawdown(equity), 2),
    }


def write_trades(trades: list[Trade]) -> None:
    with TRADE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "entry_time",
                "exit_time",
                "side",
                "entry_price",
                "exit_price",
                "points",
                "net_points",
                "reason",
            ]
        )
        for trade in trades:
            writer.writerow(
                [
                    trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                    trade.exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                    trade.side,
                    trade.entry_price,
                    trade.exit_price,
                    round(trade.points, 2),
                    round(trade.net_points, 2),
                    trade.reason,
                ]
            )


def write_report(
    rows: list[dict[str, object]],
    trades: list[Trade],
    h_exits: list[dict[str, object]],
    overlay: list[dict[str, object]],
) -> None:
    gross = summarize_trades(trades, use_net=False)
    net = summarize_trades(trades, use_net=True)
    h_summary = summarize_h_exits(h_exits)
    combined_net_points = float(h_summary["points"]) + float(net["points"])
    h_loss_points = abs(float(h_summary["loss_points"]))
    offset_ratio = float(net["points"]) / h_loss_points if h_loss_points else 0.0
    losing_overlay = [row for row in overlay if float(row["h_points"]) < 0]
    losing_h_points = sum(float(row["h_points"]) for row in losing_overlay)
    losing_sign_points = sum(float(row["sign_points"]) for row in losing_overlay)
    losing_combined_points = sum(float(row["combined_points"]) for row in losing_overlay)

    largest_losses = sorted(h_exits, key=lambda row: float(row["pnl_points"]))[:8]
    worst_strategy = sorted(trades, key=lambda trade: trade.net_points)[:8]
    losing_overlay_rows = "\n".join(
        f"| {row['entry_time']:%Y-%m-%d %H:%M} | {row['exit_time']:%Y-%m-%d %H:%M} | {row['side']} | {float(row['h_points']):,.0f} | {float(row['sign_points']):,.0f} | {float(row['combined_points']):,.0f} |"
        for row in losing_overlay
    )
    largest_loss_rows = "\n".join(
        f"| {row['timestamp']:%Y-%m-%d %H:%M:%S} | {row['side']} | {row['price']:,.0f} | {float(row['pnl_points']):,.0f} |"
        for row in largest_losses
    )
    worst_strategy_rows = "\n".join(
        f"| {trade.entry_time:%Y-%m-%d %H:%M:%S} | {trade.exit_time:%Y-%m-%d %H:%M:%S} | {trade.side} | {trade.entry_price:,.0f} | {trade.exit_price:,.0f} | {trade.net_points:,.0f} |"
        for trade in worst_strategy
    )

    report = f"""# MTX BVAV AVG Sign Strategy Research

## Scope

- Research only; no live strategy files were changed.
- Signal: `mtx_bvav_avg > 0` = long, `mtx_bvav_avg < 0` = short.
- Execution proxy: `webhook_data_1min.csv` close at the same timestamp.
- Test window: {rows[0]['time']:%Y-%m-%d %H:%M:%S} to {rows[-1]['time']:%Y-%m-%d %H:%M:%S}
- Cost model: gross and net are shown; net subtracts {ROUND_TRIP_COST_POINTS:g} points per completed trade.

## Result

| Item | Value |
| --- | ---: |
| MXF rows with price | {len(rows):,} |
| Sign strategy trades | {net['trades']} |
| Gross points | {gross['points']:,} |
| Gross cash | {gross['cash_twd']:,} |
| Net points | {net['points']:,} |
| Net cash | {net['cash_twd']:,} |
| Win rate | {net['win_rate']}% |
| Avg win / avg loss | {net['avg_win']} / {net['avg_loss']} |
| Strategy max drawdown points | {net['max_drawdown_points']:,} |

## First Account Same Window

| Item | Value |
| --- | ---: |
| H exits | {h_summary['exits']} |
| H total points | {h_summary['points']:,} |
| H total cash | {h_summary['cash_twd']:,} |
| H losing exits | {h_summary['losses']} |
| H loss points only | {h_summary['loss_points']:,} |
| H loss cash only | {h_summary['loss_cash_twd']:,} |
| H max drawdown points | {h_summary['max_drawdown_points']:,} |

## Offset Check

| Item | Value |
| --- | ---: |
| Combined H + sign net points | {combined_net_points:,.2f} |
| Sign net / H loss-only absolute value | {offset_ratio * 100:.2f}% |

Conclusion: the raw sign strategy can offset some first-account losses only if it is allowed to run as an independent continuous strategy. It is not a clean hedge: the standalone strategy has a large drawdown and frequent flips, so the sign alone is too noisy for production without filters.

## Same-Interval Loss Offset

This check answers the stricter question: during the exact H trades that lost money, did the sign strategy make money in the same time window?

| Item | Value |
| --- | ---: |
| Losing H intervals | {len(losing_overlay)} |
| H losing-interval points | {losing_h_points:,.2f} |
| Sign strategy points during those intervals | {losing_sign_points:,.2f} |
| Combined losing-interval points | {losing_combined_points:,.2f} |
| Same-interval offset ratio | {(losing_sign_points / abs(losing_h_points) * 100) if losing_h_points else 0:.2f}% |

| H Entry | H Exit | H Side | H Points | Sign Points | Combined |
| --- | --- | --- | ---: | ---: | ---: |
{losing_overlay_rows}

## Largest First-Account Losses

| Time | Side | Price | Points |
| --- | --- | ---: | ---: |
{largest_loss_rows}

## Worst Sign-Strategy Trades

| Entry | Exit | Side | Entry | Exit | Net Points |
| --- | --- | --- | ---: | ---: | ---: |
{worst_strategy_rows}
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def main() -> None:
    prices = load_prices()
    rows = load_mxf_rows(prices)
    if not rows:
        raise RuntimeError("No MXF rows joined with price data")

    trades = backtest_sign_strategy(rows)
    h_exits = load_h_exits(rows[0]["time"], rows[-1]["time"])
    h_intervals = load_h_intervals(rows[0]["time"], rows[-1]["time"])
    overlay = summarize_interval_overlay(h_intervals, build_minute_returns(rows))
    write_trades(trades)
    write_report(rows, trades, h_exits, overlay)

    print(json.dumps({
        "rows": len(rows),
        "trades": len(trades),
        "gross": summarize_trades(trades, use_net=False),
        "net": summarize_trades(trades, use_net=True),
        "h": summarize_h_exits(h_exits),
        "same_interval_loss_offset": {
            "losing_intervals": len([row for row in overlay if float(row["h_points"]) < 0]),
            "h_loss_points": round(sum(float(row["h_points"]) for row in overlay if float(row["h_points"]) < 0), 2),
            "sign_points": round(sum(float(row["sign_points"]) for row in overlay if float(row["h_points"]) < 0), 2),
            "combined_points": round(sum(float(row["combined_points"]) for row in overlay if float(row["h_points"]) < 0), 2),
        },
        "report": str(REPORT_PATH),
        "trade_csv": str(TRADE_PATH),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
