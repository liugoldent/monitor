"""Backtest the H strategy package.

Package:
1. First account follows H and sizes by single-contract drawdown.
2. Second account runs a reverse H-loss guard.

This script is read-only for live state. It writes only a markdown report.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest_h_second_account import (
    DATE_FMT,
    TV_DOC_DIR,
    WebhookStore,
    parse_dt,
    read_h_positions,
    side_points,
)
from optimize_h_loss_guard import GuardParams, simulate_loss_guard, strategy_mdd, summarize as summarize_guard


POINT_VALUE = 10
DEFAULT_START = "2026-04-30 00:00:00"


@dataclass(frozen=True)
class FirstAccountParams:
    add_drawdown_points: float = 1750.0
    base_quantity: int = 1
    add_quantity: int = 2


@dataclass
class FirstAccountTrade:
    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    single_points: float
    quantity: int
    account_points: float
    single_mdd_before: float
    single_mdd_after: float


def read_h_positions_from_trade_log(start: datetime) -> list[dict[str, Any]]:
    """Read H positions from h_trade.csv, including positions opened before start if they exit after start."""
    path = TV_DOC_DIR / "h_trade.csv"
    positions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            action = str(row.get("action", "")).strip().lower()
            side = str(row.get("side", "")).strip().lower()
            try:
                timestamp = parse_dt(str(row.get("timestamp", "")))
                price = float(str(row.get("price", "")).replace(",", ""))
            except ValueError:
                continue

            if action == "enter" and side in {"bull", "bear"}:
                current = {
                    "entry_time": timestamp,
                    "side": side,
                    "entry_price": price,
                }
                continue

            if action == "exiting" and current:
                points = side_points(current["side"], current["entry_price"], price)
                item = {
                    "entry_time": current["entry_time"],
                    "exit_time": timestamp,
                    "side": current["side"],
                    "entry_price": current["entry_price"],
                    "exit_price": price,
                    "single_points": points,
                }
                if timestamp >= start:
                    positions.append(item)
                current = None

    return positions


def backtest_first_account(start: datetime, params: FirstAccountParams) -> list[FirstAccountTrade]:
    positions = read_h_positions_from_trade_log(start)
    equity = 0.0
    peak = 0.0
    add_active = False
    trades: list[FirstAccountTrade] = []

    for position in positions:
        single_mdd_before = peak - equity
        if single_mdd_before <= 0:
            add_active = False
            quantity = params.base_quantity
        elif add_active or single_mdd_before >= params.add_drawdown_points:
            add_active = True
            quantity = params.add_quantity
        else:
            quantity = params.base_quantity

        single_points = float(position["single_points"])
        account_points = single_points * quantity
        equity += single_points
        peak = max(peak, equity)
        single_mdd_after = peak - equity

        trades.append(
            FirstAccountTrade(
                entry_time=position["entry_time"].strftime(DATE_FMT),
                exit_time=position["exit_time"].strftime(DATE_FMT),
                side=position["side"],
                entry_price=position["entry_price"],
                exit_price=position["exit_price"],
                single_points=round(single_points, 1),
                quantity=quantity,
                account_points=round(account_points, 1),
                single_mdd_before=round(single_mdd_before, 1),
                single_mdd_after=round(single_mdd_after, 1),
            )
        )

    return trades


def summarize_first_account(trades: list[FirstAccountTrade]) -> dict[str, Any]:
    account_points = [trade.account_points for trade in trades]
    single_points = [trade.single_points for trade in trades]
    wins = [point for point in account_points if point > 0]
    losses = [point for point in account_points if point <= 0]
    return {
        "trades": len(trades),
        "account_points": round(sum(account_points), 1),
        "account_cash_twd": round(sum(account_points) * POINT_VALUE, 0),
        "single_points_for_mdd": round(sum(single_points), 1),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3) if trades else 0,
        "best": round(max(account_points), 1) if account_points else 0,
        "worst": round(min(account_points), 1) if account_points else 0,
        "account_mdd_points": round(strategy_mdd(account_points), 1),
        "account_mdd_cash_twd": round(strategy_mdd(account_points) * POINT_VALUE, 0),
        "single_mdd_points": round(max((trade.single_mdd_after for trade in trades), default=0), 1),
        "two_lot_trades": sum(1 for trade in trades if trade.quantity >= 2),
    }


def combined_equity_summary(first_trades: list[FirstAccountTrade], guard_trades: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[tuple[str, float]] = []
    for trade in first_trades:
        events.append((trade.exit_time, trade.account_points))
    for trade in guard_trades:
        exit_time = trade["events"][-1]["time"]
        events.append((exit_time, float(trade["points"])))
    events.sort(key=lambda item: item[0])
    points = [point for _, point in events]
    return {
        "total_points": round(sum(points), 1),
        "cash_twd": round(sum(points) * POINT_VALUE, 0),
        "mdd_points": round(strategy_mdd(points), 1),
        "mdd_cash_twd": round(strategy_mdd(points) * POINT_VALUE, 0),
        "events": [{"time": time, "points": round(point, 1)} for time, point in events],
    }


def write_report(
    path: Path,
    start: datetime,
    first_params: FirstAccountParams,
    guard_params: GuardParams,
    first_trades: list[FirstAccountTrade],
    guard_trades: list[dict[str, Any]],
) -> None:
    first_summary = summarize_first_account(first_trades)
    guard_summary = summarize_guard(guard_trades)
    combined = combined_equity_summary(first_trades, guard_trades)

    lines = [
        "# H Strategy Package Backtest",
        "",
        f"回測起點：{start.strftime(DATE_FMT)}",
        "",
        "## 策略",
        "",
        "第一帳號加碼策略：",
        "",
        "```text",
        f"H 訊號正常跟單，基礎 {first_params.base_quantity} 口",
        f"單口 MDD >= {first_params.add_drawdown_points:.0f} 點後，下一筆開始 {first_params.add_quantity} 口",
        "MDD 歸零後回到 1 口",
        "MDD 永遠用單口點數計算，帳戶損益才乘上口數",
        "```",
        "",
        "第二帳號反向護欄策略：",
        "",
        "```text",
        f"H 浮虧 >= {guard_params.soft_loss:.0f} 點，且 1/3/5/10/15 分至少 {guard_params.invalid_score} 個週期確認 H 方向失效，反向進 1 口",
        f"或 H 浮虧 >= {guard_params.hard_loss:.0f} 點，直接反向進 1 口",
        f"第一口獲利 >= {guard_params.add_profit:.0f} 點，且 1/3/5 分至少 {guard_params.add_confirm_score} 個週期支持反向方向，加第 2 口",
        f"加碼前第一口 -{guard_params.stop_loss:.0f} 點停損",
        "H 換方向時出場",
        "```",
        "",
        "## 回測結果",
        "",
        "第一帳號：",
        "",
        "```json",
        json.dumps(first_summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "第二帳號護欄：",
        "",
        "```json",
        json.dumps(guard_summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "兩帳號合計：",
        "",
        "```json",
        json.dumps({k: v for k, v in combined.items() if k != "events"}, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 明細",
        "",
        "```json",
        json.dumps(
            {
                "first_account_trades": [asdict(trade) for trade in first_trades],
                "guard_trades": guard_trades,
                "combined_events": combined["events"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## 判斷",
        "",
        "這組規則在目前 4/30 之後的資料為正期望，且第二帳號護欄呈現賺大賠小。",
        "但護欄觸發樣本仍少，不能視為長期保證；正式上線前應持續用同一腳本更新樣本。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--report", default=str(TV_DOC_DIR / "h_strategy_package_backtest.md"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    start = parse_dt(args.start)
    store = WebhookStore()
    first_params = FirstAccountParams()
    guard_params = GuardParams(
        soft_loss=150.0,
        hard_loss=420.0,
        invalid_score=3,
        stop_loss=220.0,
        take_profit=None,
        add_profit=220.0,
        add_confirm_score=2,
        after_add_protect=None,
        trail_arm=None,
        trail_floor=None,
    )

    first_trades = backtest_first_account(start, first_params)
    h_positions_for_guard = read_h_positions(store, start)
    guard_trades = simulate_loss_guard(h_positions_for_guard, store, start, guard_params)
    write_report(Path(args.report), start, first_params, guard_params, first_trades, guard_trades)

    result = {
        "report": args.report,
        "first_account": summarize_first_account(first_trades),
        "guard": summarize_guard(guard_trades),
        "combined": {k: v for k, v in combined_equity_summary(first_trades, guard_trades).items() if k != "events"},
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result)


if __name__ == "__main__":
    main()
