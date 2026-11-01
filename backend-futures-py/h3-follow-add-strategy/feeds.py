"""Incremental local receipt feeds; stale data cannot trigger a live order."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

MAX_AGE = timedelta(seconds=180)


class CsvTail:
    def __init__(self, path, *, latest_only=False):
        self.path = Path(path)
        self.offset = 0
        self.identity = None
        self.header = None
        self.latest = None
        self.reset = False
        self.latest_only = latest_only

    def read(self):
        stat = self.path.stat()
        self.reset = self.identity != stat.st_ino or stat.st_size < self.offset
        if self.reset:
            self.offset, self.header, self.latest = 0, None, None
            self.identity = stat.st_ino
        rows = []
        with self.path.open('rb') as handle:
            handle.seek(self.offset)
            while True:
                raw = handle.readline()
                if not raw or not raw.endswith(b'\n'):
                    break
                self.offset = handle.tell()
                try:
                    fields = next(csv.reader([raw.decode('utf-8-sig')]))
                except (ValueError, UnicodeError, csv.Error):
                    continue
                if self.header is None:
                    self.header = fields
                elif len(fields) == len(self.header):
                    row = dict(zip(self.header, fields))
                    if not self.latest_only:
                        rows.append(row)
                    if not self.latest_only or (row.get('Symbol') == 'MXF1!' and row.get('Timeframe') == '1'):
                        self.latest = row
        return rows


def timestamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is not None:
        from zoneinfo import ZoneInfo
        stamp = stamp.astimezone(ZoneInfo('Asia/Taipei')).replace(tzinfo=None)
    return stamp


def fresh(stamp, now):
    return timedelta(0) <= now - stamp <= MAX_AGE


def current_price(row, now):
    if row is None:
        return None
    try:
        received, bar = timestamp(row['Record Time']), timestamp(row['TradingView Time'])
        from strategy import number
        price = number(row['Close'], positive=True)
        if bar <= received <= now and fresh(received, now) and fresh(bar, now):
            return price
    except (ValueError, TypeError, KeyError):
        pass
    return None
