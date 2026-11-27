"""Durable H3/H2 positions for the single API_KEY broker book."""
from dataclasses import asdict, dataclass
import json

from h_core import TradingError


@dataclass(frozen=True)
class Positions:
    h3: int = 0
    h2: int = 0

    def validate(self):
        if type(self.h3) is not int or type(self.h2) is not int:
            raise TradingError('H3／H2 部位帳本無效')
        return self

    @property
    def net(self):
        return self.h3 + self.h2


def positions_initialized(store):
    return store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='api_key_strategy_positions'").fetchone() is not None


def read_positions(store):
    row = store.db.execute('SELECT payload FROM api_key_strategy_positions WHERE id=1').fetchone()
    if row is None:
        raise TradingError('缺少 H3／H2 部位帳本')
    try:
        return Positions(**json.loads(row[0])).validate()
    except (TypeError, ValueError) as exc:
        raise TradingError('H3／H2 部位帳本無效') from exc


def save_positions(store, positions):
    store.db.execute('INSERT OR REPLACE INTO api_key_strategy_positions VALUES (1,?)',
                     (json.dumps(asdict(positions.validate())),))


def verify_api_key3_signal(store, payload):
    signal = payload.get('api_key3_signal')
    if signal is None:
        return
    row = store.db.execute('SELECT payload FROM api_key3_curves WHERE id=1').fetchone()
    if (not row or json.loads(row[0]) != signal['before']
            or store.db.execute('SELECT 1 FROM signals WHERE id=?', (payload['trigger'],)).fetchone()):
        raise TradingError('API_KEY3 虛擬歷史或訊號帳本已變動，保持保護')


def commit_api_key3_signal(store, payload):
    signal = payload.get('api_key3_signal')
    if signal is not None:
        verify_api_key3_signal(store, payload)
        store.db.execute('UPDATE api_key3_curves SET payload=? WHERE id=1',
                         (json.dumps(signal['after']),))
        store.db.execute('INSERT INTO signals VALUES (?,?,?,?)',
                         (payload['trigger'], signal['direction'], signal['at'], 'consumed'))


def net_transition(store, payload):
    """Verify the pending order against the committed, pre-order strategy book."""
    try:
        source = payload['strategy']
        before = Positions(**payload['positions_before']).validate()
        after = Positions(**payload['positions_after']).validate()
        target = payload['target']
        trigger = payload['trigger']
        api_key3 = payload.get('api_key3_signal') is not None
        if api_key3:
            verify_api_key3_signal(store, payload)
        h2_unchanged = after.h2 == before.h2 or (api_key3 and after.h2 == 0)
        h3_unchanged = after.h3 == before.h3 or (api_key3 and after.h3 == 0)
        if (source not in {'h3', 'h2'} or not isinstance(trigger, str)
                or trigger.startswith(('h2-tg:', 'h2-dual:')) != (source == 'h2')
                or (source == 'h3' and not h2_unchanged)
                or (source == 'h2' and not h3_unchanged)
                or (api_key3 and any(abs(q) > 1 for q in (before.h2, before.h3, after.h2, after.h3)))
                or before != read_positions(store)
                or payload['previous'] != before.net or target != after.net
                or before.net == after.net):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise TradingError('合計委託與 H3／H2 部位帳本不符，保留 guard') from exc
    return after
