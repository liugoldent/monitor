"""Historical H3 entry sizing and the broker-fill reconciliation ledger."""
import csv
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import json
from pathlib import Path

from strategy import number

H3_TRADE_HISTORY = Path(__file__).resolve().parents[1] / 'tv_doc/h_trade.csv'


@dataclass(frozen=True)
class HistoricalMdd:
    equity: Decimal
    peak: Decimal
    boosted: bool

    @property
    def drawdown(self):
        return self.peak - self.equity


def read_historical_mdd(path=H3_TRADE_HISTORY):
    """Replay complete CSV rounds in order using the single-lot price difference."""
    equity = peak = Decimal(0)
    boosted = False
    entry = None
    with path.open(encoding='utf-8-sig', newline='') as handle:
        rows = csv.DictReader(handle, strict=True)
        if not {'action', 'side', 'price'}.issubset(rows.fieldnames or ()):
            raise ValueError('H3 歷史交易缺少 action、side 或 price 欄位')
        for row in rows:
            action, side = row['action'], row['side']
            if side not in ('bull', 'bear'):
                raise ValueError(f'H3 歷史交易第 {rows.line_num} 行方向無效')
            if action == 'enter' and entry is None:
                entry = row
            elif action == 'exiting' and entry is not None and side == entry['side']:
                try:
                    points = number(row['price'], positive=True) - number(entry['price'], positive=True)
                except ValueError as exc:
                    raise ValueError(f'H3 歷史交易第 {rows.line_num} 行缺少有效的進出場價格') from exc
                equity += points if side == 'bull' else -points
                peak = max(peak, equity)
                boosted = equity < peak and (boosted or peak - equity > 1000)
                entry = None
            else:
                raise ValueError(f'H3 歷史交易第 {rows.line_num} 行進出場無法配對')
    return HistoricalMdd(equity, peak, boosted)


@dataclass(frozen=True)
class MddSizing:
    equity: str = '0'
    peak: str = '0'
    boosted: bool = False
    closed_rounds: int = 0
    tracking: bool = False
    carry: str = '0'

    def validate(self):
        equity, peak = number(self.equity), number(self.peak)
        number(self.carry)
        if (type(self.boosted) is not bool or type(self.tracking) is not bool
                or type(self.closed_rounds) is not int or self.closed_rounds < 0
                or peak < max(0, equity) or (self.boosted and equity == peak)
                or (peak - equity >= 1000 and not self.boosted)
                or (not self.tracking and number(self.carry) != 0)):
            raise ValueError('MDD加口帳本無效')
        return self

    def quantity(self, unit):
        self.validate()
        if type(unit) is not int or unit < 1:
            raise ValueError('Unit須為正整數')
        return unit + int(self.boosted)

    def filled(self, phase, old, price):
        self.validate()
        price = number(price, positive=True)
        if phase == 'entry':
            return replace(self, tracking=True, carry='0')
        if phase not in {'flatten', 'roll_close'} or not self.tracking:
            return self
        if old.first_price is None or not old.expected:
            raise ValueError('缺少完整輪次成本，不能更新MDD')
        gross = ((price - number(old.first_price)) * (1 if old.expected > 0 else -1)
                 + number(self.carry))
        if phase == 'roll_close':
            return replace(self, carry=str(gross)).validate()
        equity = number(self.equity) + gross
        peak = max(number(self.peak), equity)
        return replace(self, equity=str(equity), peak=str(peak), tracking=False, carry='0',
                       boosted=equity < peak and (self.boosted or peak-equity >= 1000),
                       closed_rounds=self.closed_rounds+1).validate()


def read_sizing(store):
    row = store.db.execute('SELECT payload FROM h3_mdd_sizing WHERE id=1').fetchone()
    return MddSizing(**json.loads(row[0])).validate()


def save_sizing(store, sizing):
    sizing.validate()
    store.db.execute('INSERT OR REPLACE INTO h3_mdd_sizing VALUES (1,?)',
                     (json.dumps(asdict(sizing)),))


def average_fill(entry):
    if entry['fill_status'] != 'complete':
        raise ValueError('缺少完整成交明細，保留guard')
    return str(sum(number(f['price']) * f['quantity'] for f in entry['fills'])
               / entry['fill_quantity'])
