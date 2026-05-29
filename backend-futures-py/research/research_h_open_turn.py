from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
TV_DOC_DIR = BASE_DIR / "tv_doc"
RESEARCH_OUTPUT_DIR = TV_DOC_DIR / "research_outputs"
H_TRADE_PATH = TV_DOC_DIR / "h_trade.csv"
MXF_VALUE_PATH = TV_DOC_DIR / "mxf_value.csv"
PRICE_PATH = TV_DOC_DIR / "webhook_data_1min.csv"
TRADE_PATH = RESEARCH_OUTPUT_DIR / "h_open_turn_trades.csv"
RESEARCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_STOP_POINTS = 250.0
TRAIL_START_POINTS = 1000.0
TRAIL_GIVEBACK_POINTS = 600.0


def parse_dt(value: object) -> datetime:
    return datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")


def minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def parse_float(value: object) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def points(side: str, entry: float, close: float) -> float:
    return close - entry if side == "bull" else entry - close


def load_mxf() -> dict[datetime, dict[str, str]]:
    rows = {}
    with MXF_VALUE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                rows[minute(parse_dt(row["time"]))] = row
            except Exception:
                continue
    return rows


def load_prices() -> list[dict]:
    rows = []
    with PRICE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                row["time"] = minute(parse_dt(row["Record Time"]))
            except Exception:
                continue
            for key in ("Open", "High", "Low", "Close", "MA_960"):
                row[key] = parse_float(row.get(key))
            rows.append(row)
    return rows


def load_h_trades() -> list[dict]:
    rows = []
    with H_TRADE_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                row["time"] = parse_dt(row["timestamp"])
            except Exception:
                continue
            row["price"] = parse_float(row.get("price"))
            rows.append(row)
    return rows


def opening_minute_for_h_time(h_time: datetime) -> datetime:
    value = minute(h_time)
    if h_time.strftime("%H:%M") == "08:47":
        value -= timedelta(minutes=1)
    return value


def get_opening_entries(h_trades: list[dict], mxf: dict[datetime, dict], price_by_time: dict[datetime, dict]) -> list[dict]:
    entries = []
    for index, row in enumerate(h_trades):
        if row.get("action") != "enter":
            continue
        h_time = row["time"]
        if h_time.strftime("%H:%M") not in {"08:46", "08:47"}:
            continue
        previous_exit = next(
            (item for item in reversed(h_trades[:index]) if item.get("action") == "exiting"),
            None,
        )
        next_exit = next(
            (item for item in h_trades[index + 1:] if item.get("action") == "exiting"),
            None,
        )
        if not previous_exit or not next_exit:
            continue
        open_minute = opening_minute_for_h_time(h_time)
        mxf_row = mxf.get(open_minute)
        price_row = price_by_time.get(open_minute)
        if not mxf_row or not price_row:
            continue

        old_side = str(previous_exit.get("side") or "")
        new_side = str(row.get("side") or "")
        close = price_row.get("Close")
        ma960 = price_row.get("MA_960")
        mtx_bvav = parse_float(mxf_row.get("mtx_bvav"))
        if close is None or ma960 is None or mtx_bvav is None:
            continue

        ok = (
            old_side == "bear"
            and new_side == "bull"
            and close < ma960
            and mtx_bvav > 0
        )
        if ok:
            entries.append({
                "time": minute(h_time),
                "open_minute": open_minute,
                "old_side": old_side,
                "side": new_side,
                "entry_price": row["price"],
                "h_exit_time": minute(next_exit["time"]),
                "open_close": close,
                "ma960": ma960,
                "mtx_bvav": mtx_bvav,
            })
    return entries


def run_trade(entry: dict, end_time: datetime, price_rows: list[dict]) -> dict:
    rows = [row for row in price_rows if entry["time"] <= row["time"] <= end_time]
    max_unrealized = 0.0
    exit_row = rows[-1]
    reason = "end: H exited or changed"

    for row in rows:
        if entry["side"] == "bear":
            adverse = row["High"] - entry["entry_price"]
        else:
            adverse = entry["entry_price"] - row["Low"]
        unrealized = points(entry["side"], entry["entry_price"], row["Close"])
        if adverse >= INITIAL_STOP_POINTS:
            exit_row = row
            reason = f"initial stop {INITIAL_STOP_POINTS:g} points"
            break
        max_unrealized = max(max_unrealized, unrealized)
        if max_unrealized >= TRAIL_START_POINTS and max_unrealized - unrealized >= TRAIL_GIVEBACK_POINTS:
            exit_row = row
            reason = f"trail after {TRAIL_START_POINTS:g}, giveback {TRAIL_GIVEBACK_POINTS:g}"
            break

    exit_points = points(entry["side"], entry["entry_price"], exit_row["Close"])
    return {
        **entry,
        "exit_time": exit_row["time"],
        "exit_price": exit_row["Close"],
        "points": exit_points,
        "max_unrealized_points": max_unrealized,
        "reason": reason,
    }


def main() -> None:
    mxf = load_mxf()
    price_rows = load_prices()
    price_by_time = {row["time"]: row for row in price_rows}
    h_trades = load_h_trades()
    entries = get_opening_entries(h_trades, mxf, price_by_time)
    trades = []
    for index, entry in enumerate(entries):
        trades.append(run_trade(entry, entry["h_exit_time"], price_rows))

    with TRADE_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "time",
            "open_minute",
            "old_side",
            "side",
            "entry_price",
            "h_exit_time",
            "exit_time",
            "exit_price",
            "points",
            "max_unrealized_points",
            "open_close",
            "ma960",
            "mtx_bvav",
            "reason",
        ])
        writer.writeheader()
        for trade in trades:
            row = dict(trade)
            for key in ("time", "open_minute", "h_exit_time", "exit_time"):
                row[key] = row[key].strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow(row)

    total = sum(float(trade["points"]) for trade in trades)
    wins = sum(1 for trade in trades if float(trade["points"]) > 0)
    print(f"trades={len(trades)} wins={wins} total_points={round(total, 1)} output={TRADE_PATH}")
    for trade in trades:
        print(
            f"{trade['time']:%Y-%m-%d %H:%M} {trade['old_side']}->{trade['side']} "
            f"exit={trade['exit_time']:%Y-%m-%d %H:%M} points={trade['points']:.1f} "
            f"max={trade['max_unrealized_points']:.1f} {trade['reason']}"
        )


if __name__ == "__main__":
    main()
