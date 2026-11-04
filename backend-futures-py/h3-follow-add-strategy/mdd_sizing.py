"""API_KEY broker-fill ledger for sizing after six consecutive losing rounds."""
from dataclasses import asdict, dataclass, replace
import json

from strategy import number

LOSS_THRESHOLD = 6


@dataclass(frozen=True)
class MddSizing:
    equity: str = '0'
    peak: str = '0'
    boosted: bool = False
    closed_rounds: int = 0
    tracking: bool = False
    carry: str = '0'
    loss_streak: int = 0

    def validate(self):
        equity, peak = number(self.equity), number(self.peak)
        number(self.carry)
        if (type(self.boosted) is not bool or type(self.tracking) is not bool
                or type(self.closed_rounds) is not int or self.closed_rounds < 0
                or peak < max(0, equity) or (self.boosted and equity == peak)
                or type(self.loss_streak) is not int or not 0 <= self.loss_streak <= self.closed_rounds
                or (self.loss_streak >= LOSS_THRESHOLD and not self.boosted)
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
        streak = self.loss_streak + 1 if gross < 0 else 0
        return replace(self, equity=str(equity), peak=str(peak), tracking=False, carry='0',
                       loss_streak=streak, boosted=equity < peak and (self.boosted or streak >= LOSS_THRESHOLD),
                       closed_rounds=self.closed_rounds+1).validate()


def decode_sizing(payload):
    if 'loss_streak' not in payload:
        # Start the new policy at zero while preserving an in-flight round.
        payload = dict(payload, loss_streak=0, equity='0', peak='0', boosted=False)
    return MddSizing(**payload).validate()


def read_sizing_payload(store):
    row = store.db.execute('SELECT payload FROM h3_mdd_sizing WHERE id=1').fetchone()
    return json.loads(row[0])


def read_sizing(store):
    return decode_sizing(read_sizing_payload(store))


def matches_order_snapshot(store, snapshot):
    if 'loss_streak' not in snapshot:
        return read_sizing_payload(store) == snapshot
    return asdict(read_sizing(store)) == snapshot


def save_sizing(store, sizing):
    sizing.validate()
    store.db.execute('INSERT OR REPLACE INTO h3_mdd_sizing VALUES (1,?)',
                     (json.dumps(asdict(sizing)),))


def average_fill(entry):
    if entry['fill_status'] != 'complete':
        raise ValueError('缺少完整成交明細，保留guard')
    return str(sum(number(f['price']) * f['quantity'] for f in entry['fills'])
               / entry['fill_quantity'])
