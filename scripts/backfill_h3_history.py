from __future__ import annotations

import argparse
import csv
import shutil
from bisect import bisect_right
from datetime import datetime, timedelta
from pathlib import Path


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SOURCE_TIME_FORMAT = "%Y/%m/%d %H:%M:%S"
POSITION_FIELDS = [
    "received_at",
    "event_key",
    "source",
    "action",
    "previous_position",
    "new_position",
    "raw_message",
]
TRADE_FIELDS = ["timestamp", "action", "side", "price", "pnl", "quantity"]


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Backfill H3 position/trade history from a TradingView strategy CSV."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--from-date", default="2026-08-01")
    parser.add_argument(
        "--records-dir",
        type=Path,
        default=root / "backend-futures-py" / "h3-ef-012-strategy" / "records",
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=root / "backend-futures-py" / "tv_doc" / "webhook_data_1min.csv",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv_atomic(
    path: Path, fields: list[str], rows: list[dict[str, object]]
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def backup_once(path: Path) -> Path:
    backup_dir = path.parent.parent / "runtime" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.stem}.before_h3_csv_backfill{path.suffix}"
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup


def source_entries(path: Path, start: datetime) -> list[dict[str, object]]:
    parsed: list[dict[str, object]] = []
    for row in read_csv(path):
        if not str(row.get("類型") or "").startswith("Entry "):
            continue
        try:
            timestamp = datetime.strptime(
                f"{row['日期']} {row['時間']}", SOURCE_TIME_FORMAT
            )
            price = float(str(row["價格"]).replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
        position = 1 if row["類型"] == "Entry Long" else -1
        parsed.append(
            {
                "timestamp": timestamp,
                "position": position,
                "price": price,
                "trade_number": str(row.get("交易No") or "").strip(),
            }
        )
    parsed.sort(key=lambda value: value["timestamp"])
    before = [value for value in parsed if value["timestamp"] < start]
    selected = ([before[-1]] if before else []) + [
        value for value in parsed if value["timestamp"] >= start
    ]
    if not selected:
        raise RuntimeError("來源CSV沒有可補錄的H3進場資料")
    return selected


def action_text(previous: int | None, current: int) -> str:
    if previous is None:
        return "初始多單" if current > 0 else "初始空單"
    if previous < 0 < current:
        return "空單平倉並轉多"
    if previous > 0 > current:
        return "多單平倉並轉空"
    return "部位不變"


def historical_position_rows(
    entries: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, float]]:
    rows: list[dict[str, object]] = []
    prices: dict[str, float] = {}
    previous: int | None = None
    for entry in entries:
        timestamp = entry["timestamp"]
        position = int(entry["position"])
        event_key = f"backfill:h3csv:{entry['trade_number']}"
        side = "多" if position > 0 else "空"
        rows.append(
            {
                "received_at": timestamp.strftime(TIME_FORMAT),
                "event_key": event_key,
                "source": "TradingView H3 CSV補錄",
                "action": action_text(previous, position),
                "previous_position": "" if previous is None else previous,
                "new_position": position,
                "raw_message": f"H3 CSV補錄：{side}1口，成交價{entry['price']:g}",
            }
        )
        prices[event_key] = float(entry["price"])
        previous = position
    return rows, prices


def load_recorded_prices(path: Path) -> tuple[list[datetime], list[float]]:
    values: dict[datetime, float] = {}
    for row in read_csv(path):
        try:
            timestamp = datetime.strptime(row["Record Time"], TIME_FORMAT)
            values[timestamp] = float(str(row["Close"]).replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
    ordered = sorted(values.items())
    return [item[0] for item in ordered], [item[1] for item in ordered]


def price_at(
    times: list[datetime], prices: list[float], timestamp: datetime
) -> float | None:
    index = bisect_right(times, timestamp) - 1
    return None if index < 0 else prices[index]


def rebuild_trade_rows(
    position_rows: list[dict[str, object]],
    source_prices: dict[str, float],
    price_times: list[datetime],
    prices: list[float],
) -> list[dict[str, object]]:
    trades: list[dict[str, object]] = []
    current_position = 0
    entry_price: float | None = None
    for row in position_rows:
        timestamp = datetime.strptime(str(row["received_at"]), TIME_FORMAT)
        target = int(row["new_position"])
        if target == current_position:
            continue
        event_key = str(row["event_key"])
        event_price = source_prices.get(event_key)
        if event_price is None:
            event_price = price_at(price_times, prices, timestamp)
        normalized_price: float | str = "" if event_price is None else event_price
        if current_position:
            pnl: float | str = ""
            if entry_price is not None and event_price is not None:
                pnl = round(
                    (event_price - entry_price)
                    * (1 if current_position > 0 else -1)
                    * 10,
                    2,
                )
            trades.append(
                {
                    "timestamp": timestamp.strftime(TIME_FORMAT),
                    "action": "exiting",
                    "side": "bull" if current_position > 0 else "bear",
                    "price": normalized_price,
                    "pnl": pnl,
                    "quantity": 1,
                }
            )
        trades.append(
            {
                "timestamp": timestamp.strftime(TIME_FORMAT),
                "action": "enter",
                "side": "bull" if target > 0 else "bear",
                "price": normalized_price,
                "pnl": "",
                "quantity": 1,
            }
        )
        current_position = target
        entry_price = event_price
    return trades


def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.from_date, "%Y-%m-%d")
    position_path = args.records_dir / "h3_position_events.csv"
    trade_path = args.records_dir / "h3_trade.csv"
    entries = source_entries(args.source, start)
    historical_rows, source_prices = historical_position_rows(entries)
    last_source_time = entries[-1]["timestamp"]

    # A few seconds separate TradingView's timestamp and the Telegram receipt.
    # Keep only live events after a five-minute crossover window so the final
    # CSV contains one transition per H3 direction change.
    crossover = last_source_time + timedelta(minutes=5)
    live_rows: list[dict[str, object]] = []
    for row in read_csv(position_path):
        try:
            timestamp = datetime.strptime(row["received_at"], TIME_FORMAT)
        except (KeyError, ValueError):
            continue
        if timestamp > crossover and not str(row.get("event_key") or "").startswith(
            "backfill:h3csv:"
        ):
            live_rows.append(row)

    merged = historical_rows + live_rows
    merged.sort(key=lambda row: datetime.strptime(str(row["received_at"]), TIME_FORMAT))
    price_times, prices = load_recorded_prices(args.prices)
    trades = rebuild_trade_rows(merged, source_prices, price_times, prices)

    position_backup = backup_once(position_path)
    trade_backup = backup_once(trade_path)
    write_csv_atomic(position_path, POSITION_FIELDS, merged)
    write_csv_atomic(trade_path, TRADE_FIELDS, trades)

    print(f"source_entries={len(entries)} live_events={len(live_rows)}")
    print(f"position_rows={len(merged)} trade_rows={len(trades)}")
    print(f"position_backup={position_backup}")
    print(f"trade_backup={trade_backup}")


if __name__ == "__main__":
    main()
