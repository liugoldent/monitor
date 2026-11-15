"""API_KEY historical sizing and separate broker-fill accounting."""
import csv
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from strategy import number

H3_TRADE_HISTORY = Path(__file__).resolve().parents[1] / 'tv_doc' / 'h_trade.csv'
TAIPEI = timezone(timedelta(hours=8))


def drawdown_tier(drawdown):
    return int(drawdown >= 750) + int(drawdown >= 1000)


def loss_tier(streak):
    return int(streak >= 4) + int(streak >= 6)


@dataclass(frozen=True)
class MddSizing:
    equity: str = '0'
    peak: str = '0'
    extra_contracts: int = 0
    closed_rounds: int = 0
    tracking: bool = False
    carry: str = '0'

    @property
    def drawdown(self):
        return number(self.peak) - number(self.equity)

    def validate(self):
        equity, peak = number(self.equity), number(self.peak)
        number(self.carry)
        drawdown = peak - equity
        if (type(self.extra_contracts) is not int or not drawdown_tier(drawdown) <= self.extra_contracts <= 2
                or type(self.tracking) is not bool
                or type(self.closed_rounds) is not int or self.closed_rounds < 0
                or peak < max(0, equity) or (self.extra_contracts and not drawdown)
                or (not self.tracking and number(self.carry) != 0)):
            raise ValueError('MDD加口帳本無效')
        return self

    def quantity(self, unit):
        self.validate()
        if type(unit) is not int or unit < 1:
            raise ValueError('Unit須為正整數')
        return unit + self.extra_contracts

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
        return self.close(gross)

    def close(self, points):
        equity = number(self.equity) + points
        peak = max(number(self.peak), equity)
        drawdown = peak - equity
        extra = max(self.extra_contracts, drawdown_tier(drawdown)) if drawdown else 0
        return replace(self, equity=str(equity), peak=str(peak), tracking=False, carry='0',
                       extra_contracts=extra,
                       closed_rounds=self.closed_rounds+1).validate()


@dataclass(frozen=True)
class H3History:
    sizing: MddSizing
    direction: int
    recovered_at: float | None
    win_streak: int = 0
    loss_streak: int = 0
    loss_extra_contracts: int = 0

    @property
    def streak_text(self):
        return f'連贏{self.win_streak}筆、連輸{self.loss_streak}筆'


def read_historical_mdd(path):
    sizing = MddSizing()
    entry = None
    recovered_at = None
    wins = losses = loss_extra = 0
    with path.open(encoding='utf-8-sig', newline='') as handle:
        rows = csv.DictReader(handle, strict=True)
        if not {'timestamp', 'action', 'side', 'price'}.issubset(rows.fieldnames or ()):
            raise ValueError('h_trade.csv 缺少 timestamp、action、side 或 price 欄位')
        for row in rows:
            action, side = row['action'], row['side']
            if action == 'enter' and side in {'bull', 'bear'} and entry is None:
                entry = row
            elif action == 'exiting' and entry is not None and side == entry['side']:
                try:
                    points = number(row['price'], positive=True) - number(entry['price'], positive=True)
                except ValueError as exc:
                    raise ValueError(f'h_trade.csv 第 {rows.line_num} 行的交易缺少有效價格') from exc
                if side == 'bear':
                    points = -points
                updated = sizing.close(points)
                wins = wins + 1 if points > 0 else 0
                losses = losses + 1 if points < 0 else 0
                loss_extra = (max(loss_extra, loss_tier(losses))
                              if updated.drawdown else 0)
                if sizing.drawdown and not updated.drawdown:
                    stamp = datetime.fromisoformat(row['timestamp'])
                    recovered_at = (stamp if stamp.tzinfo else stamp.replace(tzinfo=TAIPEI)).timestamp()
                sizing = updated
                entry = None
            else:
                raise ValueError(f'h_trade.csv 第 {rows.line_num} 行的交易無法配對')
    direction = 0 if entry is None else {'bull': 1, 'bear': -1}[entry['side']]
    return H3History(sizing, direction, recovered_at, wins, losses, loss_extra)


def decode_sizing(payload):
    if 'boosted' in payload and 'extra_contracts' not in payload:
        payload = dict(payload)
        boosted = payload.pop('boosted')
        if type(boosted) is not bool:
            raise ValueError('舊版MDD帳本無效')
        drawdown = number(payload['peak']) - number(payload['equity'])
        if boosted and not drawdown:
            raise ValueError('舊版MDD帳本無效')
        extra = drawdown_tier(drawdown)
        if 'loss_streak' in payload:
            streak = payload.pop('loss_streak')
            if (type(streak) is not int or not 0 <= streak <= payload['closed_rounds']
                    or (streak >= 6 and not boosted)):
                raise ValueError('舊版MDD帳本無效')
        elif boosted:
            # The pre-streak ledger latched at a 1000-point drawdown.
            extra = 2
        payload['extra_contracts'] = extra
    return MddSizing(**payload).validate()


def read_sizing_payload(store):
    row = store.db.execute('SELECT payload FROM h3_mdd_sizing WHERE id=1').fetchone()
    return json.loads(row[0])


def read_sizing(store):
    return decode_sizing(read_sizing_payload(store))


def matches_order_snapshot(store, snapshot):
    payload = read_sizing_payload(store)
    if 'extra_contracts' in snapshot:
        return asdict(decode_sizing(payload)) == snapshot
    if 'loss_streak' in snapshot and 'loss_streak' not in payload and 'boosted' in payload:
        # The previous release could prepare a guard before persisting its normalized ledger.
        payload = dict(payload, loss_streak=0, equity='0', peak='0', boosted=False)
    return payload == snapshot


def save_sizing(store, sizing):
    sizing.validate()
    store.db.execute('INSERT OR REPLACE INTO h3_mdd_sizing VALUES (1,?)',
                     (json.dumps(asdict(sizing)),))


def average_fill(entry):
    if entry['fill_status'] != 'complete':
        raise ValueError('缺少完整成交明細，保留guard')
    return str(sum(number(f['price']) * f['quantity'] for f in entry['fills'])
               / entry['fill_quantity'])
