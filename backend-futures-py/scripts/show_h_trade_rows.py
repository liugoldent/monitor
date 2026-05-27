from __future__ import annotations

import argparse
import csv
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
TRADE_LOG_PATH = BASE_DIR / "tv_doc" / "h_trade.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show h_trade.csv rows with both CSV line numbers and trade_row indexes."
    )
    parser.add_argument("--path", type=Path, default=TRADE_LOG_PATH, help="Path to h_trade.csv.")
    parser.add_argument("--around", type=int, help="Show rows around this trade_row index.")
    parser.add_argument("--window", type=int, default=5, help="Rows before/after --around. Default: 5.")
    parser.add_argument("--date", help="Only show rows whose timestamp starts with this value, e.g. 2026-05-22.")
    parser.add_argument("--tail", type=int, help="Show the last N trade rows.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with args.path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    data_rows = rows[1:]
    selected: list[tuple[int, list[str]]] = list(enumerate(data_rows, start=1))

    if args.date:
        selected = [(idx, row) for idx, row in selected if row and row[0].startswith(args.date)]

    if args.around is not None:
        start = args.around - args.window
        end = args.around + args.window
        selected = [(idx, row) for idx, row in selected if start <= idx <= end]

    if args.tail is not None:
        selected = selected[-args.tail :]

    print("csv_line,trade_row,timestamp,action,side,price,pnl,quantity")
    for trade_row, row in selected:
        print(",".join([str(trade_row + 1), str(trade_row), *row]))


if __name__ == "__main__":
    main()
