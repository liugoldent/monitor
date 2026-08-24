from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path


TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
POINT_VALUE = 10
HISTORY_FIELDS = [
    "exit_timestamp",
    "side",
    "exit_price",
    "trade_pnl_points",
    "equity_points",
    "peak_equity_points",
    "current_mdd_points",
    "maximum_mdd_points",
]


def _number(value: object) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _round(value: float) -> float:
    return round(value, 4)


def calculate_realized_h3_mdd(
    trade_rows: list[dict[str, object]],
    *,
    point_value: int = POINT_VALUE,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Calculate H3 drawdown only when a priced H3 trade is closed.

    The existing h3_trade.csv stores single-contract exit PnL in TWD, with ten
    TWD per point. Quantity is intentionally ignored. Open-trade mark-to-market
    PnL and one-minute closes never participate in this MDD series.
    """
    equity_points = 0.0
    peak_equity_points = 0.0
    maximum_mdd_points = 0.0
    skipped_unknown_pnl_count = 0
    history: list[dict[str, object]] = []

    for row in trade_rows:
        if str(row.get("action") or "").strip() != "exiting":
            continue
        pnl_twd = _number(row.get("pnl"))
        if pnl_twd is None:
            skipped_unknown_pnl_count += 1
            continue

        trade_pnl_points = pnl_twd / point_value
        equity_points += trade_pnl_points
        peak_equity_points = max(peak_equity_points, equity_points)
        current_mdd_points = max(0.0, peak_equity_points - equity_points)
        maximum_mdd_points = max(maximum_mdd_points, current_mdd_points)
        exit_price = _number(row.get("price"))
        history.append(
            {
                "exit_timestamp": str(row.get("timestamp") or "").strip(),
                "side": str(row.get("side") or "").strip(),
                "exit_price": "" if exit_price is None else _round(exit_price),
                "trade_pnl_points": _round(trade_pnl_points),
                "equity_points": _round(equity_points),
                "peak_equity_points": _round(peak_equity_points),
                "current_mdd_points": _round(current_mdd_points),
                "maximum_mdd_points": _round(maximum_mdd_points),
            }
        )

    if not history:
        return (
            {
                "ready": False,
                "updated_at": datetime.now().strftime(TIMESTAMP_FORMAT),
                "reason": "H3尚未有已平倉且可計算損益的交易",
                "calculation_basis": "只累計H3平倉後的單口已實現點數",
                "point_value": point_value,
                "closed_trades_count": 0,
                "skipped_unknown_pnl_count": skipped_unknown_pnl_count,
            },
            history,
        )

    latest = history[-1]
    snapshot = {
        "ready": True,
        "updated_at": datetime.now().strftime(TIMESTAMP_FORMAT),
        "last_exit_timestamp": latest["exit_timestamp"],
        "last_exit_side": latest["side"],
        "last_exit_price": latest["exit_price"],
        "last_trade_pnl_points": latest["trade_pnl_points"],
        "equity_points": latest["equity_points"],
        "peak_equity_points": latest["peak_equity_points"],
        "current_mdd_points": latest["current_mdd_points"],
        "maximum_mdd_points": latest["maximum_mdd_points"],
        "current_mdd_twd": _round(
            float(latest["current_mdd_points"]) * point_value
        ),
        "maximum_mdd_twd": _round(
            float(latest["maximum_mdd_points"]) * point_value
        ),
        "point_value": point_value,
        "closed_trades_count": len(history),
        "skipped_unknown_pnl_count": skipped_unknown_pnl_count,
        "calculation_basis": "只累計H3平倉後的單口已實現點數，不含浮動損益",
        "quantity_ignored": True,
        "order_trigger_enabled": False,
    }
    return snapshot, history


def load_trade_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _write_history_atomic(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def update_h3_mdd_records(
    *,
    trade_path: Path,
    snapshot_path: Path,
    history_path: Path,
) -> dict[str, object]:
    snapshot, history = calculate_realized_h3_mdd(load_trade_rows(trade_path))
    _write_json_atomic(snapshot_path, snapshot)
    _write_history_atomic(history_path, history)
    return snapshot
