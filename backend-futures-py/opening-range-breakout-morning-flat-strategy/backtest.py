from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

from strategy import (
    Config,
    PriceBar,
    Trade,
    daily_regimes,
    delayed_execution_index,
    find_breakout_signal,
    find_retest_entry,
    load_hourly_daily_bars,
    load_price_bars,
    merge_daily_history,
    opening_range,
    parse_time,
    session_bars,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
DEFAULT_PRICES = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
DEFAULT_DAILY_WARMUP = (
    BACKEND_DIR
    / "tv_doc"
    / "archive_research_2026-05-15"
    / "tradingview_mxf_60min.csv"
)


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    gross_points: float = 0.0
    net_points: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0
    net_loss: float = 0.0
    max_drawdown: float = 0.0
    exposure_bars: int = 0
    eligible_bars: int = 0
    expired_retests: int = 0

    @property
    def gross_profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf

    @property
    def net_profit_factor(self) -> float:
        return self.net_profit / self.net_loss if self.net_loss else math.inf

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return math.nan
        return sum(trade.gross_points > 0 for trade in self.trades) / len(self.trades)

    @property
    def exposure(self) -> float:
        return self.exposure_bars / self.eligible_bars if self.eligible_bars else 0.0

    @property
    def worst_trade(self) -> float:
        return min((trade.gross_points for trade in self.trades), default=0.0)


def _trade_exit(
    bars: list[PriceBar],
    *,
    entry_index: int,
    side: int,
    stop_price: float,
    target_price: float,
    force_flat_time: time,
) -> tuple[int, float, str, list[float]] | None:
    entry_day = bars[entry_index].bar_time.date()
    adverse_marks: list[float] = []
    for index in range(entry_index, len(bars)):
        bar = bars[index]
        if bar.bar_time.date() != entry_day:
            previous = bars[index - 1]
            return index - 1, previous.close, "missing_force_flat_bar", adverse_marks
        if bar.bar_time.time() >= force_flat_time:
            return index, bar.open, "11:00_flat", adverse_marks
        stop_hit = bar.low <= stop_price if side > 0 else bar.high >= stop_price
        target_hit = bar.high >= target_price if side > 0 else bar.low <= target_price
        if stop_hit:
            return index, stop_price, "stop", adverse_marks
        # The one-minute bar does not reveal whether its high occurred before the
        # retest limit was filled. Do not credit an entry-bar target without ticks.
        if target_hit and index > entry_index:
            adverse_marks.append(bar.low if side > 0 else bar.high)
            return index, target_price, "target", adverse_marks
        adverse_marks.append(bar.low if side > 0 else bar.high)
    return None


def run(
    *,
    prices: Path,
    daily_warmup: Path,
    start: datetime,
    end: datetime,
    config: Config,
    one_way_cost: float,
) -> Result:
    config.validate()
    all_bars = load_price_bars(prices)
    eligible = [bar for bar in all_bars if start <= bar.bar_time <= end]
    if not eligible:
        raise RuntimeError("回測區間沒有1分鐘價格資料")
    bar_times = [bar.bar_time for bar in all_bars]
    history = merge_daily_history(load_hourly_daily_bars(daily_warmup), all_bars)
    regimes = daily_regimes(history, config.daily_sma_length)
    sessions = session_bars(eligible)
    result = Result(
        eligible_bars=sum(
            row.bar_time.time() < config.force_flat_time
            for rows in sessions.values()
            for row in rows
        )
    )
    realized_net = 0.0
    equity_peak = 0.0
    round_trip_cost = one_way_cost * 2

    for trading_day, rows in sorted(sessions.items()):
        regime = regimes.get(trading_day)
        if regime is None:
            continue
        side, previous_close, daily_sma = regime
        if side == 0:
            continue
        current_range = opening_range(rows, config)
        if current_range is None:
            continue
        opening_high, opening_low = current_range
        signal = find_breakout_signal(
            rows,
            side=side,
            opening_high=opening_high,
            opening_low=opening_low,
            config=config,
        )
        if signal is None:
            continue
        order_start_index = delayed_execution_index(all_bars, bar_times, signal)
        if order_start_index is None:
            continue
        breakout_edge = opening_high if side > 0 else opening_low
        retest_limit = breakout_edge - side * config.retest_points
        expiry = min(
            datetime.combine(trading_day, config.force_flat_time),
            signal.bar_time + timedelta(minutes=config.retest_expiry_minutes),
        )
        retest_entry = find_retest_entry(
            all_bars,
            start_index=order_start_index,
            side=side,
            limit_price=retest_limit,
            penetration_points=config.limit_penetration_points,
            expiry=expiry,
        )
        if retest_entry is None:
            result.expired_retests += 1
            continue
        entry_index, entry_price = retest_entry
        entry = all_bars[entry_index]
        if entry.bar_time < start or entry.bar_time > end:
            continue
        stop_price = entry_price - side * config.stop_points
        target_price = entry_price + side * config.target_points
        exit_result = _trade_exit(
            all_bars,
            entry_index=entry_index,
            side=side,
            stop_price=stop_price,
            target_price=target_price,
            force_flat_time=config.force_flat_time,
        )
        if exit_result is None:
            continue
        exit_index, exit_price, reason, adverse_prices = exit_result
        exit_bar = all_bars[exit_index]
        if exit_bar.bar_time > end:
            continue
        gross_points = (exit_price - entry_price) * side
        net_points = gross_points - round_trip_cost

        for adverse_price in adverse_prices:
            marked_equity = (
                realized_net + (adverse_price - entry_price) * side - round_trip_cost
            )
            equity_peak = max(equity_peak, marked_equity)
            result.max_drawdown = min(
                result.max_drawdown, marked_equity - equity_peak
            )
            result.exposure_bars += 1

        realized_net += net_points
        equity_peak = max(equity_peak, realized_net)
        result.max_drawdown = min(result.max_drawdown, realized_net - equity_peak)
        trade = Trade(
            trading_day=trading_day,
            side=side,
            signal_time=signal.bar_time,
            entry_time=entry.bar_time,
            exit_time=exit_bar.bar_time,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_price=stop_price,
            target_price=target_price,
            opening_high=opening_high,
            opening_low=opening_low,
            daily_close=previous_close,
            daily_sma=daily_sma,
            gross_points=gross_points,
            reason=reason,
        )
        result.trades.append(trade)
        result.gross_points += gross_points
        result.net_points += net_points
        if gross_points > 0:
            result.gross_profit += gross_points
        elif gross_points < 0:
            result.gross_loss += -gross_points
        if net_points > 0:
            result.net_profit += net_points
        elif net_points < 0:
            result.net_loss += -net_points

    if abs(result.net_points - realized_net) > 1e-9:
        raise AssertionError("net P&L reconciliation failed")
    return result


def month_rows(result: Result, one_way_cost: float) -> list[tuple[str, int, float]]:
    values: dict[str, list[Trade]] = defaultdict(list)
    for trade in result.trades:
        values[trade.trading_day.strftime("%Y-%m")].append(trade)
    return [
        (
            month,
            len(rows),
            sum(row.gross_points - one_way_cost * 2 for row in rows),
        )
        for month, rows in sorted(values.items())
    ]


def write_trades(path: Path, result: Result, one_way_cost: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "trading_day",
        "side",
        "signal_time",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "stop_price",
        "target_price",
        "opening_high",
        "opening_low",
        "daily_close",
        "daily_sma",
        "gross_points",
        "net_points",
        "reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(
                {
                    "trading_day": trade.trading_day.isoformat(),
                    "side": "bull" if trade.side > 0 else "bear",
                    "signal_time": trade.signal_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "entry_time": trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit_time": trade.exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "stop_price": trade.stop_price,
                    "target_price": trade.target_price,
                    "opening_high": trade.opening_high,
                    "opening_low": trade.opening_low,
                    "daily_close": trade.daily_close,
                    "daily_sma": trade.daily_sma,
                    "gross_points": trade.gross_points,
                    "net_points": trade.gross_points - one_way_cost * 2,
                    "reason": trade.reason,
                }
            )


def _fmt(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="日線方向＋日盤30分開盤區間突破回測")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--daily-warmup", type=Path, default=DEFAULT_DAILY_WARMUP)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--opening-minutes", type=int, default=30)
    parser.add_argument("--daily-sma-length", type=int, default=20)
    parser.add_argument("--retest", type=float, default=50.0)
    parser.add_argument("--retest-expiry", type=int, default=30)
    parser.add_argument("--limit-penetration", type=float, default=1.0)
    parser.add_argument("--stop", type=float, default=100.0)
    parser.add_argument("--target", type=float, default=100.0)
    parser.add_argument("--force-flat", default="11:00")
    parser.add_argument("--one-way-cost", type=float, default=2.4)
    parser.add_argument("--point-value", type=float, default=10.0)
    parser.add_argument("--trades-out", type=Path)
    args = parser.parse_args()
    force_hour, force_minute = (int(value) for value in args.force_flat.split(":"))
    config = Config(
        opening_minutes=args.opening_minutes,
        daily_sma_length=args.daily_sma_length,
        retest_points=args.retest,
        retest_expiry_minutes=args.retest_expiry,
        limit_penetration_points=args.limit_penetration,
        stop_points=args.stop,
        target_points=args.target,
        force_flat_time=time(force_hour, force_minute),
        minimum_opening_bars=max(1, args.opening_minutes - 2),
    )
    result = run(
        prices=args.prices,
        daily_warmup=args.daily_warmup,
        start=parse_time(args.start),
        end=parse_time(args.end),
        config=config,
        one_way_cost=args.one_way_cost,
    )
    print(
        f"period={args.start}..{args.end} opening={config.opening_minutes}m "
        f"daily_sma={config.daily_sma_length} retest={config.retest_points:g} "
        f"retest_expiry={config.retest_expiry_minutes}m "
        f"limit_penetration={config.limit_penetration_points:g} "
        f"stop={config.stop_points:g} "
        f"target={config.target_points:g} force_flat={config.force_flat_time} "
        "signal=bar_close fill=next_minute_after_record_time_open intrabar=adverse_first"
    )
    print(
        "trades win_rate gross net gross_PF net_PF MDD_intrabar worst_trade "
        "exposure expired_retests"
    )
    print(
        len(result.trades),
        _fmt(result.win_rate),
        _fmt(result.gross_points),
        _fmt(result.net_points),
        _fmt(result.gross_profit_factor),
        _fmt(result.net_profit_factor),
        _fmt(result.max_drawdown),
        _fmt(result.worst_trade),
        _fmt(result.exposure),
        result.expired_retests,
    )
    print("month trades net_points")
    for month, count, net_points in month_rows(result, args.one_way_cost):
        print(month, count, _fmt(net_points))
    print(
        f"net_twd={result.net_points * args.point_value:.2f} "
        f"mdd_twd={result.max_drawdown * args.point_value:.2f}"
    )
    if args.trades_out:
        write_trades(args.trades_out, result, args.one_way_cost)
        print(f"trades_out={args.trades_out}")


if __name__ == "__main__":
    main()
