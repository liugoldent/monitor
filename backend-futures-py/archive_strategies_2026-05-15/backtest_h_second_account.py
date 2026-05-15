"""Backtest second-account H strategies against local TradingView webhook CSVs.

This script is intentionally read-only for live strategy state. It uses
`tv_doc/h_trade.csv` as the H strategy reference and `tv_doc/webhook_data_*min.csv`
for indicator snapshots.
"""

from __future__ import annotations

import argparse
import csv
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
TV_DOC_DIR = BASE_DIR / "tv_doc"
DATE_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class HPosition:
    entry_time: datetime
    side: str
    entry_price: float
    exit_time: datetime
    exit_price: float
    points: float
    is_open: bool = False


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value.strip(), DATE_FMT)


def to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def side_points(side: str, entry: float, close: float) -> float:
    return close - entry if side == "bull" else entry - close


def opposite_side(side: str) -> str:
    return "bear" if side == "bull" else "bull"


def read_webhook_rows(tf: str) -> list[tuple[datetime, dict[str, str]]]:
    path = TV_DOC_DIR / f"webhook_data_{tf}min.csv"
    rows: list[tuple[datetime, dict[str, str]]] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append((parse_dt(row["TradingView Time"]), row))
            except (KeyError, ValueError):
                continue
    rows.sort(key=lambda item: item[0])
    return rows


class WebhookStore:
    def __init__(self) -> None:
        self.rows = {tf: read_webhook_rows(tf) for tf in ("1", "3", "5", "10", "15")}
        self.times = {tf: [item[0] for item in rows] for tf, rows in self.rows.items()}

    def latest(self, tf: str, timestamp: datetime) -> dict[str, str] | None:
        idx = bisect_right(self.times[tf], timestamp) - 1
        if idx < 0:
            return None
        return self.rows[tf][idx][1]

    def one_min_between(self, start: datetime, end: datetime) -> list[tuple[datetime, dict[str, str]]]:
        return [(t, row) for t, row in self.rows["1"] if start <= t <= end]

    def latest_close(self) -> tuple[datetime, float]:
        timestamp, row = self.rows["1"][-1]
        close = to_float(row.get("Close"))
        if close is None:
            raise ValueError("latest 1m row has no Close")
        return timestamp, close


def row_close(row: dict[str, str] | None) -> float | None:
    return to_float(row.get("Close")) if row else None


def tf_invalid(h_side: str, row: dict[str, str] | None) -> bool:
    if not row:
        return False
    close = to_float(row.get("Close"))
    ma_p200 = to_float(row.get("MA_P200"))
    ma_n200 = to_float(row.get("MA_N200"))
    bbr = to_float(row.get("BBR"))
    if close is None or bbr is None:
        return False
    if h_side == "bull":
        return ma_n200 is not None and close < ma_n200 and bbr < 0.45
    return ma_p200 is not None and close > ma_p200 and bbr > 0.55


def side_supported(side: str, row: dict[str, str] | None) -> bool:
    if not row:
        return False
    close = to_float(row.get("Close"))
    ma_p200 = to_float(row.get("MA_P200"))
    ma_n200 = to_float(row.get("MA_N200"))
    bbr = to_float(row.get("BBR"))
    if close is None or bbr is None:
        return False
    if side == "bull":
        return ma_n200 is not None and close > ma_n200 and bbr > 0.50
    return ma_p200 is not None and close < ma_p200 and bbr < 0.50


def invalid_score(store: WebhookStore, side: str, timestamp: datetime) -> int:
    return sum(tf_invalid(side, store.latest(tf, timestamp)) for tf in ("1", "3", "5", "10", "15"))


def support_score(store: WebhookStore, side: str, timestamp: datetime) -> int:
    return sum(side_supported(side, store.latest(tf, timestamp)) for tf in ("1", "3", "5"))


def read_h_positions(store: WebhookStore, start: datetime, include_open: bool = True) -> list[HPosition]:
    path = TV_DOC_DIR / "h_trade.csv"
    positions: list[HPosition] = []
    current: dict[str, Any] | None = None

    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            action = str(row.get("action", "")).strip().lower()
            side = str(row.get("side", "")).strip().lower()
            price = to_float(row.get("price"))
            try:
                timestamp = parse_dt(str(row.get("timestamp", "")))
            except ValueError:
                continue

            if action == "enter" and side in {"bull", "bear"} and price is not None:
                current = {"entry_time": timestamp, "side": side, "entry_price": price}
                continue

            if action == "exiting" and current and price is not None:
                position = HPosition(
                    entry_time=current["entry_time"],
                    side=current["side"],
                    entry_price=current["entry_price"],
                    exit_time=timestamp,
                    exit_price=price,
                    points=side_points(current["side"], current["entry_price"], price),
                )
                if position.exit_time >= start:
                    positions.append(position)
                current = None

    if include_open and current:
        latest_time, latest_close = store.latest_close()
        position = HPosition(
            entry_time=current["entry_time"],
            side=current["side"],
            entry_price=current["entry_price"],
            exit_time=latest_time,
            exit_price=latest_close,
            points=side_points(current["side"], current["entry_price"], latest_close),
            is_open=True,
        )
        if position.exit_time >= start:
            positions.append(position)

    return positions


def backtest_loss_guard(positions: list[HPosition], store: WebhookStore, start: datetime) -> list[dict[str, Any]]:
    soft_loss = -180.0
    hard_loss = -480.0
    invalid_threshold = 3
    stop_loss = -220.0
    take_profit = 450.0
    trail_arm = 220.0
    trail_floor = 120.0
    add_profit = 180.0
    add_score = 2
    after_add_protect = 80.0

    trades: list[dict[str, Any]] = []
    for h_pos in positions:
        minute_rows = store.one_min_between(max(h_pos.entry_time, start), h_pos.exit_time)
        if not minute_rows:
            continue

        second_side = ""
        entry_price: float | None = None
        add_entry_price: float | None = None
        max_favorable = 0.0
        events: list[dict[str, Any]] = []

        for timestamp, row in minute_rows:
            close = row_close(row)
            if close is None:
                continue

            if not second_side:
                h_unrealized = side_points(h_pos.side, h_pos.entry_price, close)
                score = invalid_score(store, h_pos.side, timestamp)
                if h_unrealized <= hard_loss or (h_unrealized <= soft_loss and score >= invalid_threshold):
                    second_side = opposite_side(h_pos.side)
                    entry_price = close
                    events.append({
                        "time": timestamp,
                        "signal": "entry",
                        "side": second_side,
                        "price": close,
                        "points": h_unrealized,
                        "extra": score,
                    })
                continue

            assert entry_price is not None
            first_points = side_points(second_side, entry_price, close)
            max_favorable = max(max_favorable, first_points)
            if add_entry_price is None and first_points >= add_profit and support_score(store, second_side, timestamp) >= add_score:
                add_entry_price = close
                events.append({
                    "time": timestamp,
                    "signal": "add",
                    "side": second_side,
                    "price": close,
                    "points": first_points,
                    "extra": support_score(store, second_side, timestamp),
                })

            total_points = first_points
            if add_entry_price is not None:
                total_points += side_points(second_side, add_entry_price, close)

            reason = ""
            if add_entry_price is not None and first_points <= after_add_protect:
                reason = "after-add protect"
            elif add_entry_price is None and first_points <= stop_loss:
                reason = "stop loss"
            elif first_points >= take_profit:
                reason = "take profit"
            elif max_favorable >= trail_arm and first_points <= trail_floor:
                reason = "giveback"

            if reason:
                events.append({
                    "time": timestamp,
                    "signal": "exit",
                    "side": second_side,
                    "price": close,
                    "points": total_points,
                    "extra": reason,
                })
                trades.append({"h": h_pos, "points": total_points, "reason": reason, "events": events})
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
            reason = "current mark" if h_pos.is_open else "H exit"
            events.append({
                "time": timestamp,
                "signal": "exit",
                "side": second_side,
                "price": close,
                "points": total_points,
                "extra": reason,
            })
            trades.append({"h": h_pos, "points": total_points, "reason": reason, "events": events})

    return trades


def backtest_scale_follow(positions: list[HPosition], store: WebhookStore, start: datetime) -> list[dict[str, Any]]:
    h_profit_entry = 180.0
    entry_score = 2
    first_stop = -180.0
    add_profit = 180.0
    add_score = 2
    after_add_protect = 80.0
    take_profit = 450.0
    trail_arm = 300.0
    trail_giveback = 160.0

    trades: list[dict[str, Any]] = []
    for h_pos in positions:
        minute_rows = store.one_min_between(max(h_pos.entry_time, start), h_pos.exit_time)
        if not minute_rows:
            continue

        in_position = False
        entry_price: float | None = None
        add_entry_price: float | None = None
        max_favorable = 0.0
        events: list[dict[str, Any]] = []

        for timestamp, row in minute_rows:
            close = row_close(row)
            if close is None:
                continue

            if not in_position:
                h_unrealized = side_points(h_pos.side, h_pos.entry_price, close)
                score = support_score(store, h_pos.side, timestamp)
                if h_unrealized >= h_profit_entry and score >= entry_score:
                    in_position = True
                    entry_price = close
                    events.append({
                        "time": timestamp,
                        "signal": "entry",
                        "side": h_pos.side,
                        "price": close,
                        "points": h_unrealized,
                        "extra": score,
                    })
                continue

            assert entry_price is not None
            first_points = side_points(h_pos.side, entry_price, close)
            max_favorable = max(max_favorable, first_points)
            if add_entry_price is None and first_points >= add_profit and support_score(store, h_pos.side, timestamp) >= add_score:
                add_entry_price = close
                events.append({
                    "time": timestamp,
                    "signal": "add",
                    "side": h_pos.side,
                    "price": close,
                    "points": first_points,
                    "extra": support_score(store, h_pos.side, timestamp),
                })

            total_points = first_points
            if add_entry_price is not None:
                total_points += side_points(h_pos.side, add_entry_price, close)

            reason = ""
            if add_entry_price is not None and first_points <= after_add_protect:
                reason = "after-add protect"
            elif add_entry_price is None and (first_points <= first_stop or invalid_score(store, h_pos.side, timestamp) >= 2):
                reason = "stop or invalid"
            elif first_points >= take_profit:
                reason = "take profit"
            elif max_favorable >= trail_arm and first_points <= max_favorable - trail_giveback:
                reason = "trailing giveback"

            if reason:
                events.append({
                    "time": timestamp,
                    "signal": "exit",
                    "side": h_pos.side,
                    "price": close,
                    "points": total_points,
                    "extra": reason,
                })
                trades.append({"h": h_pos, "points": total_points, "reason": reason, "events": events})
                in_position = False
                break

        if in_position and entry_price is not None:
            timestamp, row = minute_rows[-1]
            close = row_close(row)
            if close is None:
                continue
            total_points = side_points(h_pos.side, entry_price, close)
            if add_entry_price is not None:
                total_points += side_points(h_pos.side, add_entry_price, close)
            reason = "current mark" if h_pos.is_open else "H exit"
            events.append({
                "time": timestamp,
                "signal": "exit",
                "side": h_pos.side,
                "price": close,
                "points": total_points,
                "extra": reason,
            })
            trades.append({"h": h_pos, "points": total_points, "reason": reason, "events": events})

    return trades


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    points = [float(trade["points"]) for trade in trades]
    return {
        "trades": len(trades),
        "total_points": round(sum(points), 1),
        "wins": sum(point > 0 for point in points),
        "losses": sum(point <= 0 for point in points),
        "best": round(max(points), 1) if points else 0,
        "worst": round(min(points), 1) if points else 0,
    }


def fmt_dt(timestamp: datetime) -> str:
    return timestamp.strftime(DATE_FMT)


def trade_to_dict(trade: dict[str, Any]) -> dict[str, Any]:
    h_pos: HPosition = trade["h"]
    return {
        "h_entry_time": fmt_dt(h_pos.entry_time),
        "h_side": h_pos.side,
        "h_entry_price": h_pos.entry_price,
        "h_exit_time": fmt_dt(h_pos.exit_time),
        "h_exit_price": h_pos.exit_price,
        "h_points": round(h_pos.points, 1),
        "h_is_open": h_pos.is_open,
        "second_points": round(float(trade["points"]), 1),
        "exit_reason": trade["reason"],
        "events": [
            {
                "time": fmt_dt(event["time"]),
                "signal": event["signal"],
                "side": event["side"],
                "price": event["price"],
                "points": round(float(event["points"]), 1),
                "extra": event["extra"],
            }
            for event in trade["events"]
        ],
    }


def write_report(path: Path, start: datetime, store: WebhookStore, positions: list[HPosition], loss_guard: list[dict[str, Any]], scale_follow: list[dict[str, Any]]) -> None:
    one_min_first = store.rows["1"][0][0]
    one_min_last = store.rows["1"][-1][0]
    covered_positions = sum(1 for pos in positions if store.one_min_between(max(pos.entry_time, start), pos.exit_time))
    loss_summary = summarize(loss_guard)
    scale_summary = summarize(scale_follow)

    lines = [
        "# H Second Account Backtest",
        "",
        f"回測區間：{fmt_dt(start)} -> {fmt_dt(one_min_last)}",
        f"1 分指標資料：{fmt_dt(one_min_first)} -> {fmt_dt(one_min_last)}",
        "",
        "## 樣本",
        "",
        "```text",
        f"H 倉位數：{len(positions)}",
        f"有 1 分指標資料覆蓋：{covered_positions}",
        "```",
        "",
        "## H Loss Guard",
        "",
        "第二帳號等 H 看錯、虧損擴大，才反向進場；有獲利後才保守加碼。",
        "",
        "```text",
        f"觸發交易：{loss_summary['trades']}",
        f"合計：{loss_summary['total_points']} 點",
        f"勝率：{loss_summary['wins']} / {loss_summary['trades']}",
        f"最佳：{loss_summary['best']} 點",
        f"最差：{loss_summary['worst']} 點",
        "```",
        "",
        "## H Scale Follow",
        "",
        "第二帳號在 H 已經獲利後順向跟進，並在第二帳號獲利後加碼。",
        "",
        "```text",
        f"觸發交易：{scale_summary['trades']}",
        f"合計：{scale_summary['total_points']} 點",
        f"勝率：{scale_summary['wins']} / {scale_summary['trades']}",
        f"最佳：{scale_summary['best']} 點",
        f"最差：{scale_summary['worst']} 點",
        "```",
        "",
        "## 明細 JSON",
        "",
        "```json",
        json.dumps(
            {
                "loss_guard": [trade_to_dict(trade) for trade in loss_guard],
                "scale_follow": [trade_to_dict(trade) for trade in scale_follow],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-04-30 00:00:00")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report", default=str(TV_DOC_DIR / "h_second_account_backtest_2026-04-30_to_today.md"))
    args = parser.parse_args()

    start = parse_dt(args.start)
    store = WebhookStore()
    positions = read_h_positions(store, start)
    loss_guard = backtest_loss_guard(positions, store, start)
    scale_follow = backtest_scale_follow(positions, store, start)

    result = {
        "start": fmt_dt(start),
        "latest_1m": fmt_dt(store.rows["1"][-1][0]),
        "h_positions": len(positions),
        "covered_h_positions": sum(1 for pos in positions if store.one_min_between(max(pos.entry_time, start), pos.exit_time)),
        "loss_guard": summarize(loss_guard),
        "scale_follow": summarize(scale_follow),
    }
    write_report(Path(args.report), start, store, positions, loss_guard, scale_follow)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result)


if __name__ == "__main__":
    main()
