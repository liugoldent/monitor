"""API_KEY entry sizing from H3 history and broker-fill reconciliation."""
import csv
from dataclasses import asdict, replace

from runtime import Engine, LedgerError
from mdd_sizing import MddSizing, read_historical_mdd, read_sizing, save_sizing


def historical_mdd():
    try:
        return read_historical_mdd()
    except (OSError, ValueError, csv.Error) as exc:
        raise LedgerError(f'無法讀取 H3 歷史回撤，暫停新進場：{exc}') from exc


class MddEngine(Engine):
    strategy_label = 'H3 MDD加口'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self.store.db:
            existed = self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='h3_mdd_sizing'").fetchone()
            self.store.db.execute('CREATE TABLE IF NOT EXISTS h3_mdd_sizing '
                                  '(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
            if not self.store.db.execute('SELECT 1 FROM h3_mdd_sizing').fetchone():
                if existed or self.store.pending():
                    raise LedgerError('MDD帳本缺漏或有待確認委託，不自動重設')
                save_sizing(self.store, MddSizing())
                self.store.db.execute('INSERT INTO audit(at,event,detail) VALUES '
                    "(datetime('now'),'mdd_initialized',?)",
                    ('初始化券商成交核對帳本；新進場口數使用 h_trade.csv',))
        self.sizing()

    def sizing(self):
        try:
            return read_sizing(self.store)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise LedgerError('MDD帳本無效，停止送單並保留原始紀錄') from exc

    def entry_quantity(self):
        return self.broker.unit + int(historical_mdd().boosted)

    def on_external_inventory(self, actual):
        save_sizing(self.store, replace(self.sizing(), tracking=False, carry='0'))

    def prepare_order(self, payload):
        if payload['phase'] not in {'entry', 'flatten'}:
            raise LedgerError('MDD策略只允許H3進場與全平')
        if payload['phase'] == 'entry':
            # Use the quantity already selected from one history snapshot.
            self.broker.entry_quantity = payload['quantity']
        payload['mdd_before'] = asdict(self.sizing())

    def confirmed_order(self, old, planned, payload):
        if asdict(self.sizing()) != payload['mdd_before']:
            raise LedgerError('成交後MDD帳本改變，保留guard')
        try:
            price = self.broker.confirmed_price
            sizing = self.sizing().filled(payload['phase'], old, price)
        except (ValueError, TypeError, AttributeError) as exc:
            raise LedgerError('缺少完整成交價，保留guard等待核對') from exc
        save_sizing(self.store, sizing)
        return replace(planned, first_price=str(price)) if payload['phase'] == 'entry' else planned

    def on_sample(self, *args, **kwargs):
        return False

