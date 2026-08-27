from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from strategy import (
    Action,
    Config,
    MeanReversionEngine,
    RiskContextTracker,
    load_price_bars,
    parse_time,
    text_time,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent
DEFAULT_PRICES = BACKEND_DIR / "tv_doc" / "webhook_data_1min.csv"
DEFAULT_H_TRADES = BACKEND_DIR / "tv_doc" / "h_trade.csv"
DEFAULT_EF_SIGNALS = BACKEND_DIR / "tv_doc" / "six_strategy_signal_events.csv"


@dataclass
class Result:
    actions: list[Action] = field(default_factory=list)
    trade_pnls: list[float] = field(default_factory=list)
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    realized: float = 0.0
    turnover: int = 0
    max_drawdown: float = 0.0
    exposure_bars: int = 0
    marked_bars: int = 0
    accepted_signals: int = 0
    rejected_signals: int = 0

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf


def run(
    *,
    prices: Path,
    h_trades: Path,
    ef_signals: Path,
    start: datetime,
    end: datetime,
    config: Config,
) -> Result:
    all_bars = load_price_bars(prices)
    eligible = [bar for bar in all_bars if start <= bar.bar_time <= end]
    if not eligible:
        raise RuntimeError("回測區間沒有1分鐘價格資料")
    warmup = [bar for bar in all_bars if bar.bar_time < start]
    engine = MeanReversionEngine(config)
    engine.warm(warmup)
    context_tracker = RiskContextTracker(
        h_trades, ef_signals, ef_threshold=config.ef_threshold
    )
    result = Result()
    realized = 0.0
    peak = 0.0
    for bar in eligible:
        context = context_tracker.at(bar.record_time)
        actions, decisions = engine.process_bar(bar, context)
        for decision in decisions:
            if decision.kind == "signal":
                if decision.accepted:
                    result.accepted_signals += 1
                else:
                    result.rejected_signals += 1
        for action in actions:
            result.actions.append(action)
            result.turnover += 1
            if action.action == "exit" and action.pnl_points is not None:
                pnl = action.pnl_points
                result.trade_pnls.append(pnl)
                realized += pnl
                if pnl > 0:
                    result.gross_profit += pnl
                elif pnl < 0:
                    result.gross_loss += -pnl
        unrealized = 0.0
        if engine.state.position and engine.state.entry_price is not None:
            unrealized = (bar.close - engine.state.entry_price) * engine.state.position
            result.exposure_bars += 1
        equity = realized + unrealized
        peak = max(peak, equity)
        result.max_drawdown = min(result.max_drawdown, equity - peak)
        result.marked_bars += 1
    # A research interval must not hide an open leg at the end boundary.
    if engine.state.position and engine.state.entry_price is not None:
        last = eligible[-1]
        pnl = (last.close - engine.state.entry_price) * engine.state.position
        action = Action(
            last.bar_time,
            "exit",
            engine.state.position,
            last.close,
            "backtest_end",
            pnl,
        )
        result.actions.append(action)
        result.turnover += 1
        result.trade_pnls.append(pnl)
        realized += pnl
        if pnl > 0:
            result.gross_profit += pnl
        elif pnl < 0:
            result.gross_loss += -pnl
    result.realized = realized
    if abs(result.gross_profit - result.gross_loss - result.realized) > 1e-9:
        raise AssertionError("gross profit/loss reconciliation failed")
    return result


def write_actions(path: Path, actions: list[Action]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "action",
        "side",
        "price",
        "pnl_points",
        "reason",
        "target_price",
        "stop_price",
        "signal_time",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for action in actions:
            writer.writerow(
                {
                    "timestamp": text_time(action.timestamp),
                    "action": action.action,
                    "side": "bull" if action.side > 0 else "bear",
                    "price": action.price,
                    "pnl_points": "" if action.pnl_points is None else action.pnl_points,
                    "reason": action.reason,
                    "target_price": "" if action.target_price is None else action.target_price,
                    "stop_price": "" if action.stop_price is None else action.stop_price,
                    "signal_time": text_time(action.signal_time),
                }
            )


def fmt(value: float) -> str:
    return "inf" if math.isinf(value) else f"{value:.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="60根1分K回歸通道均值回歸回測")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--h-trades", type=Path, default=DEFAULT_H_TRADES)
    parser.add_argument("--ef-signals", type=Path, default=DEFAULT_EF_SIGNALS)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--length", type=int, default=60)
    parser.add_argument("--width", type=float, default=2.0)
    parser.add_argument("--stop", type=float, default=100.0)
    parser.add_argument("--max-slope", type=float, default=2.5)
    parser.add_argument("--one-way-cost", type=float, default=2.0)
    parser.add_argument("--point-value", type=float, default=10.0)
    parser.add_argument("--trades-out", type=Path)
    args = parser.parse_args()
    start = parse_time(args.start)
    end = parse_time(args.end)
    config = Config(
        regression_length=args.length,
        channel_width=args.width,
        stop_points=args.stop,
        max_abs_slope=args.max_slope,
    )
    result = run(
        prices=args.prices,
        h_trades=args.h_trades,
        ef_signals=args.ef_signals,
        start=start,
        end=end,
        config=config,
    )
    estimated_net = result.realized - result.turnover * args.one_way_cost
    win_rate = (
        sum(pnl > 0 for pnl in result.trade_pnls) / len(result.trade_pnls)
        if result.trade_pnls
        else math.nan
    )
    exposure = result.exposure_bars / result.marked_bars if result.marked_bars else 0.0
    print(
        f"period={start}..{end} signal=bar_close fill=next_minute_after_record_time_open "
        f"length={config.regression_length} width={config.channel_width:g} "
        f"stop={config.stop_points:g} max_slope={config.max_abs_slope:g} "
        "intrabar=adverse_first context=point_in_time_h_ef"
    )
    print(
        "gross_profit gross_loss PF closed_legs win_rate realized turnover "
        "estimated_net max_drawdown_mtm exposure accepted_signals rejected_signals"
    )
    print(
        fmt(result.gross_profit),
        fmt(result.gross_loss),
        fmt(result.profit_factor),
        len(result.trade_pnls),
        fmt(win_rate),
        fmt(result.realized),
        result.turnover,
        fmt(estimated_net),
        fmt(result.max_drawdown),
        fmt(exposure),
        result.accepted_signals,
        result.rejected_signals,
    )
    print(
        f"estimated_net_twd={estimated_net * args.point_value:.2f} "
        f"max_drawdown_twd={result.max_drawdown * args.point_value:.2f}"
    )
    if args.trades_out:
        write_actions(args.trades_out, result.actions)
        print(f"trades_out={args.trades_out}")


if __name__ == "__main__":
    main()
