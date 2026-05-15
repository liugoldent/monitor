"""Search conservative H-loss guard variants.

The optimizer is read-only. It never updates live strategy state and only writes
an analysis report under tv_doc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest_h_second_account import (
    BASE_DIR,
    DATE_FMT,
    HPosition,
    WebhookStore,
    invalid_score,
    opposite_side,
    parse_dt,
    read_h_positions,
    row_close,
    side_points,
    support_score,
)


TV_DOC_DIR = BASE_DIR / "tv_doc"


@dataclass(frozen=True)
class GuardParams:
    soft_loss: float
    hard_loss: float
    invalid_score: int
    stop_loss: float
    take_profit: float | None
    add_profit: float | None
    add_confirm_score: int
    after_add_protect: float | None
    trail_arm: float | None
    trail_floor: float | None
    exit_on_h_change: bool = True


def strategy_mdd(points: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for point in points:
        equity += point
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return abs(worst)


def simulate_loss_guard(
    positions: list[HPosition],
    store: WebhookStore,
    start: datetime,
    params: GuardParams,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []

    for h_pos in positions:
        minute_rows = store.one_min_between(max(h_pos.entry_time, start), h_pos.exit_time)
        if not minute_rows:
            continue

        second_side = ""
        entry_price: float | None = None
        add_entry_price: float | None = None
        max_first_points = 0.0
        min_total_points = 0.0
        max_total_points = 0.0
        events: list[dict[str, Any]] = []

        for timestamp, row in minute_rows:
            close = row_close(row)
            if close is None:
                continue

            if not second_side:
                h_unrealized = side_points(h_pos.side, h_pos.entry_price, close)
                score = invalid_score(store, h_pos.side, timestamp)
                if h_unrealized <= -params.hard_loss or (
                    h_unrealized <= -params.soft_loss and score >= params.invalid_score
                ):
                    second_side = opposite_side(h_pos.side)
                    entry_price = close
                    events.append(
                        {
                            "time": timestamp.strftime(DATE_FMT),
                            "signal": "entry",
                            "side": second_side,
                            "price": close,
                            "points": round(h_unrealized, 1),
                            "extra": score,
                        }
                    )
                continue

            assert entry_price is not None
            first_points = side_points(second_side, entry_price, close)
            max_first_points = max(max_first_points, first_points)

            if (
                params.add_profit is not None
                and add_entry_price is None
                and first_points >= params.add_profit
                and support_score(store, second_side, timestamp) >= params.add_confirm_score
            ):
                add_entry_price = close
                events.append(
                    {
                        "time": timestamp.strftime(DATE_FMT),
                        "signal": "add",
                        "side": second_side,
                        "price": close,
                        "points": round(first_points, 1),
                        "extra": support_score(store, second_side, timestamp),
                    }
                )

            total_points = first_points
            if add_entry_price is not None:
                total_points += side_points(second_side, add_entry_price, close)

            min_total_points = min(min_total_points, total_points)
            max_total_points = max(max_total_points, total_points)

            reason = ""
            if (
                add_entry_price is not None
                and params.after_add_protect is not None
                and first_points <= params.after_add_protect
            ):
                reason = "after-add protect"
            elif add_entry_price is None and first_points <= -params.stop_loss:
                reason = "stop loss"
            elif params.take_profit is not None and first_points >= params.take_profit:
                reason = "take profit"
            elif (
                params.trail_arm is not None
                and params.trail_floor is not None
                and max_first_points >= params.trail_arm
                and first_points <= params.trail_floor
            ):
                reason = "giveback"

            if reason:
                events.append(
                    {
                        "time": timestamp.strftime(DATE_FMT),
                        "signal": "exit",
                        "side": second_side,
                        "price": close,
                        "points": round(total_points, 1),
                        "extra": reason,
                    }
                )
                trades.append(
                    {
                        "h_entry_time": h_pos.entry_time.strftime(DATE_FMT),
                        "h_side": h_pos.side,
                        "h_points": round(h_pos.points, 1),
                        "points": round(total_points, 1),
                        "reason": reason,
                        "mae": round(min_total_points, 1),
                        "mfe": round(max_total_points, 1),
                        "events": events,
                    }
                )
                second_side = ""
                break

        if second_side and entry_price is not None:
            timestamp, row = minute_rows[-1]
            close = row_close(row)
            if close is None:
                continue
            total_points = side_points(second_side, entry_price, close)
            if add_entry_price is not None:
                total_points += side_points(second_side, add_entry_price, close)
            min_total_points = min(min_total_points, total_points)
            max_total_points = max(max_total_points, total_points)
            reason = "current mark" if h_pos.is_open else "H exit"
            events.append(
                {
                    "time": timestamp.strftime(DATE_FMT),
                    "signal": "exit",
                    "side": second_side,
                    "price": close,
                    "points": round(total_points, 1),
                    "extra": reason,
                }
            )
            trades.append(
                {
                    "h_entry_time": h_pos.entry_time.strftime(DATE_FMT),
                    "h_side": h_pos.side,
                    "h_points": round(h_pos.points, 1),
                    "points": round(total_points, 1),
                    "reason": reason,
                    "mae": round(min_total_points, 1),
                    "mfe": round(max_total_points, 1),
                    "events": events,
                }
            )

    return trades


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    points = [float(trade["points"]) for trade in trades]
    wins = [point for point in points if point > 0]
    losses = [point for point in points if point <= 0]
    return {
        "trades": len(points),
        "total_points": round(sum(points), 1),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(points), 3) if points else 0,
        "avg_win": round(sum(wins) / len(wins), 1) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 1) if losses else 0,
        "best": round(max(points), 1) if points else 0,
        "worst": round(min(points), 1) if points else 0,
        "mdd": round(strategy_mdd(points), 1),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses else 999,
        "worst_intratrade_mae": round(min((float(trade["mae"]) for trade in trades), default=0), 1),
    }


def candidate_params() -> list[GuardParams]:
    params: list[GuardParams] = []
    for soft_loss in (150.0, 180.0, 220.0, 260.0):
        for hard_loss in (420.0, 520.0, 9999.0):
            for invalid in (3, 4, 5):
                for stop_loss in (220.0, 300.0, 420.0):
                    for take_profit in (None, 600.0, 800.0):
                        params.append(
                            GuardParams(
                                soft_loss=soft_loss,
                                hard_loss=hard_loss,
                                invalid_score=invalid,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                add_profit=220.0,
                                add_confirm_score=2,
                                after_add_protect=None,
                                trail_arm=None,
                                trail_floor=None,
                            )
                        )
                        params.append(
                            GuardParams(
                                soft_loss=soft_loss,
                                hard_loss=hard_loss,
                                invalid_score=invalid,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                                add_profit=260.0,
                                add_confirm_score=2,
                                after_add_protect=80.0,
                                trail_arm=None,
                                trail_floor=None,
                            )
                        )
    return params


def score(summary: dict[str, Any]) -> float:
    return (
        float(summary["total_points"])
        - 0.8 * float(summary["mdd"])
        + 40.0 * float(summary["profit_factor"])
        + 80.0 * (float(summary["avg_win"]) / max(abs(float(summary["avg_loss"])), 1.0))
    )


def write_report(path: Path, start: datetime, results: list[dict[str, Any]]) -> None:
    lines = [
        "# Optimized H Loss Guard Candidates",
        "",
        f"回測起點：{start.strftime(DATE_FMT)}",
        "",
        "篩選條件：",
        "",
        "```text",
        "交易數 >= 3",
        "總點數 > 0",
        "平均獲利 > 平均虧損絕對值",
        "策略 MDD <= 350 點",
        "單筆最差 >= -220 點",
        "```",
        "",
    ]

    for idx, item in enumerate(results[:10], start=1):
        lines.extend(
            [
                f"## Candidate {idx}",
                "",
                "```json",
                json.dumps(
                    {
                        "summary": item["summary"],
                        "params": item["params"],
                        "trades": item["trades"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
            ]
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    start = parse_dt("2026-04-30 00:00:00")
    store = WebhookStore()
    positions = read_h_positions(store, start)

    results: list[dict[str, Any]] = []
    for params in candidate_params():
        trades = simulate_loss_guard(positions, store, start, params)
        summary = summarize(trades)
        if (
            summary["trades"] >= 3
            and summary["total_points"] > 0
            and summary["avg_win"] > abs(summary["avg_loss"])
            and summary["mdd"] <= 350
            and summary["worst"] >= -220
        ):
            results.append(
                {
                    "score": score(summary),
                    "summary": summary,
                    "params": params.__dict__,
                    "trades": trades,
                }
            )

    results.sort(key=lambda item: item["score"], reverse=True)
    report_path = TV_DOC_DIR / "h_loss_guard_optimized_candidates.md"
    write_report(report_path, start, results)
    print(
        json.dumps(
            {
                "candidates": len(results),
                "report": str(report_path),
                "best": results[0] if results else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
