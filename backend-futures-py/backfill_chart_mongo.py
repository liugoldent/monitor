"""Backfill recent market rows and complete EF/H history to MongoDB.

Run with --dry-run first. Re-running is safe because every row uses an upsert.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path

from chart_mongo import ensure_query_indexes, mirror_batch

ROOT = Path(__file__).resolve().parent
SOURCES = {
    "webhook_data_1min.csv": ROOT / "tv_doc" / "webhook_data_1min.csv",
    "mxf_value.csv": ROOT / "tv_doc" / "mxf_value.csv",
    "six_strategy_signal_events.csv": ROOT / "tv_doc" / "six_strategy_signal_events.csv",
    "h_trade.csv": ROOT / "tv_doc" / "h_trade.csv",
    "ef_position_events.csv": ROOT / "telegram-relay-records" / "ef_position_events.csv",
    "h_position_events.csv": ROOT / "telegram-relay-records" / "h_position_events.csv",
}


def selected_dates(days: int) -> tuple[list[str], set[str]]:
    with SOURCES["webhook_data_1min.csv"].open(newline="", encoding="utf-8-sig") as handle:
        dates = set()
        for row in csv.DictReader(handle):
            try:
                day = date.fromisoformat((row.get("TradingView Time") or "")[:10])
            except ValueError:
                continue
            if day.weekday() < 5:
                dates.add(day)
    trading = sorted(dates)[-days:]
    selected = {day.isoformat() for day in trading}
    # Friday's night session continues into Saturday morning.
    for day in trading:
        if day.weekday() == 4:
            selected.add((day + timedelta(days=1)).isoformat())
    return [day.isoformat() for day in trading], selected


def row_date(source: str, row: dict) -> str:
    fields = {
        "webhook_data_1min.csv": ("TradingView Time",),
        "mxf_value.csv": ("time",),
        "six_strategy_signal_events.csv": ("received_at", "message_time"),
        "h_trade.csv": ("timestamp",),
        "ef_position_events.csv": ("received_at",),
        "h_position_events.csv": ("received_at",),
    }[source]
    return next((str(row[key])[:10] for key in fields if row.get(key)), "")


def backfill(source: str, path: Path, dry_run: bool, batch_size: int, dates: set[str]) -> int:
    if not path.exists():
        print(f"{source}: missing, skipped", flush=True)
        return 0
    count = 0
    batch = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=1):
            if source in {"webhook_data_1min.csv", "mxf_value.csv"} and row_date(source, row) not in dates:
                continue
            if source == "six_strategy_signal_events.csv":
                row["source_order"] = number
            batch.append(row)
            count += 1
            if len(batch) >= batch_size:
                if not dry_run:
                    mirror_batch(source, batch)
                batch.clear()
                if count % 10000 < batch_size:
                    print(f"{source}: {count} rows processed", flush=True)
    if batch and not dry_run:
        mirror_batch(source, batch)
    print(f"{source}: {count} rows {'counted' if dry_run else 'upserted'}", flush=True)
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--source", action="append", choices=tuple(SOURCES), help="Only backfill this source; repeat for more")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 1000:
        parser.error("--batch-size must be between 1 and 1000")
    if args.days < 1:
        parser.error("--days must be positive")
    trading, dates = selected_dates(args.days)
    print(f"Trading days: {', '.join(trading)}", flush=True)
    print(f"CSV dates included: {', '.join(sorted(dates))}", flush=True)
    if not args.dry_run:
        ensure_query_indexes()
    selected_sources = args.source or list(SOURCES)
    total = sum(backfill(source, SOURCES[source], args.dry_run, args.batch_size, dates) for source in selected_sources)
    print(f"Total: {total} rows", flush=True)


if __name__ == "__main__":
    main()
