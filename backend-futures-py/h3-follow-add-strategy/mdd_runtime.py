"""API_KEY entry sizing from H3 history and broker-fill reconciliation."""
import csv
from dataclasses import asdict, replace
import time

from runtime import Engine, LedgerError, signal
from mdd_sizing import H3_TRADE_HISTORY, MddSizing, read_historical_mdd, read_sizing, save_sizing


def read_history(path):
    try:
        return read_historical_mdd(path)
    except (OSError, TypeError, ValueError, csv.Error) as exc:
        raise LedgerError(f'無法讀取 H3 歷史回撤，暫停新進場：{exc}') from exc


def wait_for_signal_history(path, event, direction):
    # The router can publish a reversal before the relay appends the close and
    # the next open. Read both the direction and sizing from one CSV snapshot.
    while signal(event) is not None:
        history = read_history(path)
        if history.direction == direction:
            return history
        time.sleep(0.25)
    return None


class MddEngine(Engine):
    strategy_label = 'H3 回撤加口'
    history_path = H3_TRADE_HISTORY

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
                    ('初始化券商成交回撤帳本',))
        self.sizing()

    def sizing(self):
        try:
            return read_sizing(self.store)
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise LedgerError('MDD帳本無效，停止送單並保留原始紀錄') from exc

    def entry_quantity(self):
        return self.entry_plan()[0]

    def entry_plan(self):
        return self.plan_from_history(read_history(self.history_path))

    def signal_entry_plan(self, event, direction):
        history = wait_for_signal_history(self.history_path, event, direction)
        if history is None:
            self.notify('【API_KEY｜H3 回撤加口】H3 歷史未在訊號有效期間同步，本筆跳過進場。')
            return None
        return self.plan_from_history(history)

    def plan_from_history(self, history):
        sizing = history.sizing
        return (sizing.quantity(self.broker.unit),
                f'H3 訊號源目前回撤 {sizing.drawdown:f} 點，{history.streak_text}')

    def on_external_inventory(self, actual):
        save_sizing(self.store, replace(self.sizing(), tracking=False, carry='0'))

    def prepare_signal(self, key, preflight):
        self.sizing()
        self.broker.cancel_working_orders(key, preflight)

    def prepare_order(self, payload):
        if payload['phase'] not in {'entry', 'flatten'}:
            raise LedgerError('MDD策略只允許H3進場與全平')
        if payload['phase'] == 'entry':
            # Use the quantity already selected from the synchronized H3 history.
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

