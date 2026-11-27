"""Single-contract shadow curves for API_KEY3. This module never uses a broker."""
from __future__ import annotations

from bisect import bisect_right
import csv
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import os

from runtime import LedgerError
from strategy import number

TAIPEI = timezone(timedelta(hours=8))


def stamp(value):
    value = datetime.fromisoformat(value.replace('/', '-'))
    return (value if value.tzinfo else value.replace(tzinfo=TAIPEI)).timestamp()


@dataclass(frozen=True)
class Curve:
    equity: str = '0'
    peak: str = '0'
    losses: int = 0
    large_loss: bool = False
    longest_streak: int = 0
    drawdown_750_at: float | None = None
    direction: int = 0
    entry: str | None = None
    at: float = 0
    closed: int = 0

    @property
    def drawdown(self):
        return number(self.peak) - number(self.equity)

    def close(self, price, at):
        if not self.direction or self.entry is None:
            raise ValueError('虛擬持倉缺少進場價')
        pnl = (price - number(self.entry)) * self.direction
        equity = number(self.equity) + pnl
        peak = max(number(self.peak), equity)
        drawdown = peak - equity
        losses = self.losses + 1 if pnl < 0 else 0
        return replace(self, equity=str(equity), peak=str(peak), losses=losses,
                       large_loss=bool(drawdown and (self.large_loss or pnl <= -1000)),
                       longest_streak=max(self.longest_streak, losses) if drawdown else 0,
                       drawdown_750_at=(self.drawdown_750_at if self.drawdown_750_at is not None
                                        else at if drawdown >= 750 else None) if drawdown else None,
                       direction=0, entry=None, at=at, closed=self.closed + 1)

    def enter(self, direction, price, at):
        if self.direction or direction not in (-1, 1) or at < self.at:
            raise ValueError('歷史進場順序或方向無效')
        return replace(self, direction=direction, entry=str(price), at=at)

    def signal(self, direction, price, at):
        if at < self.at:
            raise ValueError('訊號時間早於前一筆虛擬成交')
        if direction == self.direction:
            return replace(self, at=at)
        curve = self.close(price, at) if self.direction else replace(self, at=at)
        return curve.enter(direction, price, at) if direction else curve


@contextmanager
def history_rows(path):
    try:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            yield csv.DictReader(handle, strict=True)
    except (OSError, csv.Error, ValueError, KeyError, TypeError) as exc:
        raise LedgerError(f'{path.name} 歷史無法回溯：{exc}') from exc


def h2_history(path, cutoff):
    """TradingView export repeats P&L on entries; pair by trade number and price."""
    curve, open_id = Curve(), None
    with history_rows(path) as rows:
        for row in rows:
            kind = row['類型']
            if not kind and row['交易No'] == '0':
                continue
            action, side = kind.split(' ')
            direction = {'Long': 1, 'Short': -1}[side]
            at = stamp(f"{row['日期']} {row['時間']}")
            if at > cutoff:
                break
            price = number(row['價格'], positive=True)
            if at < curve.at or number(row['數量']) != 1:
                raise ValueError('H2 匯出時間或口數無效')
            if action == 'Entry':
                curve = curve.enter(direction, price, at)
                open_id = row['No']
            elif action == 'Exit' and open_id == row['No'] and direction == curve.direction:
                curve = curve.close(price, at)
                open_id = None
            else:
                raise ValueError('H2 交易無法配對')
        if not curve.closed:
            raise ValueError('缺少已平倉歷史')
    return curve


def h3_history(path, cutoff):
    curve = Curve()
    with history_rows(path) as rows:
        for row in rows:
            at = stamp(row['timestamp'])
            if at > cutoff:
                break
            price = number(row['price'], positive=True)
            direction = {'bull': 1, 'bear': -1}[row['side']]
            if at < curve.at or number(row['quantity'], positive=True) % 1:
                raise ValueError('H3 歷史時間或口數無效')
            if row['action'] == 'enter':
                curve = curve.enter(direction, price, at)
            elif row['action'] == 'exiting' and curve.direction == direction:
                curve = curve.close(price, at)
            else:
                raise ValueError('H3 交易無法配對')
        if not curve.closed:
            raise ValueError('缺少已平倉歷史')
    return curve


class MarketPrices:
    """Index received MXF1! minute closes; never use a quote received after the signal."""

    def __init__(self, path):
        self.path = path
        self.times, self.quotes = [], []
        self.offset, self.identity = 0, None
        self.header = None

    def refresh(self):
        with self.path.open('rb') as handle:
            stat = os.fstat(handle.fileno())
            identity = stat.st_dev, stat.st_ino
            if self.identity is not None and (identity != self.identity or stat.st_size < self.offset):
                raise LedgerError('行情檔已被替換或截斷，請核對歷史來源')
            self.identity = identity
            handle.seek(self.offset)
            if self.header is None:
                self.header = next(csv.reader([handle.readline().decode('utf-8-sig')]))
                self.offset = handle.tell()
            while raw := handle.readline():
                if not raw.endswith(b'\n'):
                    break
                self.offset = handle.tell()
                row = dict(zip(self.header, next(csv.reader([raw.decode('utf-8')]))))
                if row.get('Symbol') != 'MXF1!' or row.get('Timeframe') != '1':
                    continue
                try:
                    recorded, bar = stamp(row['Record Time']), stamp(row['TradingView Time'])
                    quote = number(row['Close'], positive=True)
                except (KeyError, ValueError):
                    continue
                if recorded < bar or recorded - bar > 120:
                    continue
                i = bisect_right(self.times, recorded)
                self.times.insert(i, recorded)
                self.quotes.insert(i, (bar, quote))

    def at(self, at):
        self.refresh()
        i = bisect_right(self.times, at) - 1
        if i < 0 or at - self.quotes[i][0] > 120:
            raise LedgerError('訊號前 120 秒內沒有已收到的 MXF1! 行情，暫停處理')
        return self.quotes[i][1]
