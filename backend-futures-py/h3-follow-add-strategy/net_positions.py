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


def net_transition(store, payload):
    """Verify the pending order against the committed, pre-order strategy book."""
    try:
        source = payload['strategy']
        before = Positions(**payload['positions_before']).validate()
        after = Positions(**payload['positions_after']).validate()
        target = payload['target']
        trigger = payload['trigger']
        if (source not in {'h3', 'h2'} or not isinstance(trigger, str)
                or trigger.startswith('h2-tg:') != (source == 'h2')
                or (source == 'h3' and after.h2 != before.h2)
                or (source == 'h2' and after.h3 != before.h3)
                or before != read_positions(store)
                or payload['previous'] != before.net or target != after.net
                or before.net == after.net):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise TradingError('合計委託與 H3／H2 部位帳本不符，保留 guard') from exc
    return after
