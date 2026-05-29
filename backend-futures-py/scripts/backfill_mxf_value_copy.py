from __future__ import annotations

import argparse
import csv
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from pymongo import MongoClient


BASE_DIR = Path(__file__).resolve().parents[1]
TV_DOC_DIR = BASE_DIR / "tv_doc"
BACKUP_DIR = TV_DOC_DIR / "backups"
DEFAULT_INPUT = TV_DOC_DIR / "mxf_value.csv"
DEFAULT_OUTPUT = BACKUP_DIR / "mxf_value.backfilled.csv"
DB_NAME = "mxf_futures"
AVG_WINDOW = 23
CSV_HEADER = ["time", "tx_bvav", "mtx_bvav", "mtx_tbta", "mtx_bvav_avg", "signal", "trend"]


def load_env_file(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        return float(text)
    except ValueError:
        return None


def format_int(value: object) -> str:
    number = to_float(value)
    if number is None:
        return ""
    return str(int(round(number)))


def get_signal(tx_bvav: float | None, mtx_bvav: float | None) -> str:
    if tx_bvav is None or mtx_bvav is None:
        return "none"
    if tx_bvav > 0 and mtx_bvav > 0:
        return "bull"
    if tx_bvav < 0 and mtx_bvav < 0:
        return "bear"
    return "none"


def get_trend(mtx_bvav: float | None, mtx_bvav_avg: float | None) -> str:
    if mtx_bvav is None or mtx_bvav_avg is None:
        return "none"
    if mtx_bvav > mtx_bvav_avg:
        return "gold"
    if mtx_bvav < mtx_bvav_avg:
        return "death"
    return "none"


def is_date_collection(name: str) -> bool:
    if len(name) != 10 or name[4] != "-" or name[7] != "-":
        return False
    return parse_time(f"{name} 00:00:00") is not None


def normalize_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = parse_time(raw.get("time"))
    if timestamp is None:
        return None
    return {
        "time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "tx_bvav": to_float(raw.get("tx_bvav")),
        "mtx_bvav": to_float(raw.get("mtx_bvav")),
        "mtx_tbta": to_float(raw.get("mtx_tbta")),
    }


def read_existing_rows(path: Path) -> OrderedDict[str, dict[str, Any]]:
    rows: OrderedDict[str, dict[str, Any]] = OrderedDict()
    if not path.exists():
        return rows

    with path.open("r", newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            record = normalize_record(raw)
            if record is None:
                continue
            rows[record["time"]] = record
    return rows


def read_mongo_rows(mongo_uri: str, start_date: str | None, end_date: str | None) -> OrderedDict[str, dict[str, Any]]:
    client = MongoClient(mongo_uri)
    db = client[DB_NAME]
    rows: OrderedDict[str, dict[str, Any]] = OrderedDict()
    collection_names = sorted(name for name in db.list_collection_names() if is_date_collection(name))
    if start_date:
        collection_names = [name for name in collection_names if name >= start_date]
    if end_date:
        collection_names = [name for name in collection_names if name <= end_date]

    for name in collection_names:
        cursor = db[name].find({}, {"_id": 0}).sort([("time", 1), ("_id", 1)])
        for raw in cursor:
            record = normalize_record(raw)
            if record is None:
                continue
            rows[record["time"]] = record
    return rows


def build_output_rows(records_by_time: dict[str, dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    history: list[float] = []
    for timestamp in sorted(records_by_time):
        record = records_by_time[timestamp]
        tx_bvav = record.get("tx_bvav")
        mtx_bvav = record.get("mtx_bvav")
        mtx_tbta = record.get("mtx_tbta")
        avg = None
        if mtx_bvav is not None:
            history.append(float(mtx_bvav))
            if len(history) >= AVG_WINDOW:
                avg = sum(history[-AVG_WINDOW:]) / AVG_WINDOW
        signal = get_signal(tx_bvav, mtx_bvav)
        trend = get_trend(mtx_bvav, avg)
        rows.append([
            timestamp,
            format_int(tx_bvav),
            format_int(mtx_bvav),
            format_int(mtx_tbta),
            format_int(avg),
            signal,
            trend,
        ])
    return rows


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill mxf_value.csv into a copy from MongoDB.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    load_env_file()
    mongo_rows = read_mongo_rows(require_env("MONGO_URI"), args.start_date, args.end_date)
    existing_rows = read_existing_rows(args.input)

    merged_rows: OrderedDict[str, dict[str, Any]] = OrderedDict()
    merged_rows.update(mongo_rows)
    merged_rows.update(existing_rows)

    output_rows = build_output_rows(merged_rows)
    write_csv(args.output, output_rows)

    print(f"Mongo rows: {len(mongo_rows)}")
    print(f"Existing rows: {len(existing_rows)}")
    print(f"Output rows: {len(output_rows)}")
    print(f"Added rows in copy: {max(0, len(output_rows) - len(existing_rows))}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
