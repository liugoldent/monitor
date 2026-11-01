"""Expiry-day manual rolls use fake inventory and never connect to a broker."""
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_trade
from broker import PyramidBroker
from h_core import Store, TradingError
from rollover import ManualRollover, ledger_symbol
from runtime import Engine, LedgerError, RolloverPause
from strategy import State
from test_pyramid import API, event


class Contracts:
    def __init__(self):
        self.old = NS(code='TMFJ6', delivery_month='202610', delivery_date='2026-10-21')
        self.new = NS(code='TMFK6', delivery_month='202611', delivery_date='2026-11-18')
        self.december = NS(code='TMFL6', delivery_month='202612', delivery_date='2026-12-16')
        self.TMFR1 = self.old  # Alias deliberately stays on the expiring month.

    def __iter__(self):
        return iter([self.old, self.new, self.december])


class RollAPI(API):
    def __init__(self):
        super().__init__()
        self.Contracts.Futures.TMF = Contracts()
        self.current_code = 'TMFJ6'
        self.extra = []
        self.sent_codes = []

    def list_positions(self, account):
        rows = [] if not self.current else [dict(code=self.current_code, quantity=abs(self.current),
            direction='Buy' if self.current > 0 else 'Sell', price=self.price)]
        return rows + self.extra

    def place_order(self, contract, order, **kwargs):
        self.sent_codes.append(contract.code)
        return super().place_order(contract, order, **kwargs)


class ManualRolloverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = Store(self.base / 'state.sqlite')
        self.addCleanup(self.store.db.close)
        self.api = RollAPI()
        self.api.current = -1
        self.sj = NS(constant=NS(Action=NS(Buy='Buy', Sell='Sell'),
            FuturesPriceType=NS(MKT='MKT'), OrderType=NS(IOC='IOC'), FuturesOCType=NS(Auto='Auto')))
        watchdog = Mock()
        watchdog.status.return_value = (False, False)
        self.broker = PyramidBroker(self.api, self.sj, self.store, watchdog, records=self.base / 'records')
        self.broker.contract_code = 'TMFJ6'
        self.notify = Mock()
        self.engine = Engine(self.store, self.broker, 'TMFJ6', self.notify)
        with self.store.db:
            self.engine.save(State(expected=-1, first_price='21000', round_key='old-round',
                                   previous_guerrilla={'old': '123'}))
        self.now = datetime(2026, 10, 21, 13, 25)
        self.tick = 0
        self.roll = ManualRollover(self.engine, self.base, clock=lambda: self.now, monotonic=lambda: self.tick)
        self.broker.rollover = self.roll
        self.prints = patch('builtins.print')
        self.prints.start()
        self.addCleanup(self.prints.stop)

    def move_to_next(self, quantity=1):
        self.api.current_code = 'TMFK6'
        self.api.current = quantity
        self.api.price = 22500

    def confirm_roll(self):
        self.assertFalse(self.roll.synchronize())
        self.tick += 2
        self.assertTrue(self.roll.synchronize())

    def test_boundary_uses_broker_expiry_date_and_taipei_1325(self):
        self.now = datetime(2026, 10, 21, 13, 24, 59)
        self.assertTrue(self.roll.synchronize())
        self.engine.validate_position()
        self.now = datetime(2026, 10, 21, 13, 25)
        self.assertFalse(self.roll.synchronize())
        self.assertEqual(self.api.orders, [])
        self.assertEqual(self.engine.state().expected, -1)

    def test_manual_direction_change_adopts_cost_and_next_signal_reverses_correct_month(self):
        self.move_to_next(1)
        self.confirm_roll()
        self.assertEqual(self.engine.symbol, 'TMFK6')
        self.assertEqual(self.engine.state().expected, 1)
        self.assertEqual(self.engine.state().first_price, '22500')
        self.assertEqual(self.engine.state().previous_guerrilla, {})
        self.assertEqual(self.api.orders, [])
        self.assertEqual(len(list(self.base.glob('api_key.before_rollover_*.sqlite'))), 1)
        with patch.object(auto_trade.shared.time, 'sleep'):
            self.engine.on_signal(event(-1))
        self.assertEqual([(o.action, o.quantity) for o in self.api.orders], [('Sell', 1), ('Sell', 1)])
        self.assertEqual(self.api.sent_codes, ['TMFK6', 'TMFK6'])
        self.assertEqual(self.engine.state().expected, -1)
        self.assertFalse(self.store.pending())

    def test_flat_notice_closes_manual_position_on_next_month(self):
        self.move_to_next(-2)
        self.confirm_roll()
        self.assertTrue(self.engine.state().added)
        self.engine.on_signal(event(0))
        self.assertEqual([(o.action, o.quantity) for o in self.api.orders], [('Buy', 2)])
        self.assertEqual(self.api.sent_codes, ['TMFK6'])
        self.assertEqual(self.engine.state().expected, 0)

    def test_same_direction_signal_keeps_manual_position_without_duplicate_entry(self):
        self.move_to_next(1)
        self.confirm_roll()
        self.engine.on_signal(event(1))
        self.assertEqual(self.api.orders, [])
        self.assertEqual(self.engine.state().expected, 1)

    def test_add_after_roll_uses_new_cost_and_new_contract(self):
        self.move_to_next(1)
        self.confirm_roll()
        self.api.price = 23000
        self.engine.on_sample('near', -1300, '01', 23000)
        self.assertTrue(self.engine.on_sample('near', -1200, '02', 23000))
        self.assertEqual(self.api.sent_codes, ['TMFK6'])
        self.assertEqual(self.engine.state().expected, 2)

    def test_rollover_is_idempotent_and_route_survives_restart_before_alias_switch(self):
        self.move_to_next(1)
        self.confirm_roll()
        self.assertTrue(self.roll.synchronize())
        symbol = ledger_symbol(self.store, 'TMFJ6')
        self.assertEqual(symbol, 'TMFK6')
        restarted = Engine(self.store, self.broker, symbol)
        self.broker.rollover = ManualRollover(restarted, self.base, clock=lambda: self.now)
        self.assertTrue(self.broker.rollover.synchronize())
        restarted.validate_position()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM audit WHERE event='pyramid_manual_rollover'").fetchone()[0], 1)

    def test_two_months_open_orders_flat_or_excess_inventory_never_adopt(self):
        before = self.store.db.execute('SELECT * FROM pyramid_state').fetchall()
        self.move_to_next(1)
        for problem in ('old_month', 'open_order', 'flat', 'too_many', 'opposite_hedge'):
            with self.subTest(problem=problem):
                self.api.extra, self.api.trades, self.api.current = [], [], 1
                if problem == 'old_month':
                    self.api.extra = [dict(code='TMFJ6', direction='Sell', quantity=1)]
                elif problem == 'open_order':
                    self.api.trades = [NS(contract=NS(code='TMFK6'), status=NS(status='Submitted'))]
                elif problem == 'flat':
                    self.api.current = 0
                elif problem == 'too_many':
                    self.api.current = 3
                else:
                    self.api.extra = [dict(code='TMFK6', direction='Sell', quantity=1)]
                self.assertFalse(self.roll.synchronize())
                self.tick += 3
                self.assertFalse(self.roll.synchronize())
                self.assertEqual(self.store.db.execute('SELECT * FROM pyramid_state').fetchall(), before)
        self.assertEqual(self.api.orders, [])

    def test_signal_is_not_consumed_while_roll_is_incomplete(self):
        message = event(-1)
        with self.assertRaises(TradingError):
            self.engine.on_signal(message)
        self.move_to_next(1)
        self.confirm_roll()
        self.assertTrue(self.engine.on_signal(message))
        self.assertEqual(self.api.sent_codes, ['TMFK6', 'TMFK6'])

    def test_pending_strategy_order_blocks_adoption_without_clearing_it(self):
        self.move_to_next(1)
        self.store.begin(dict(phase='entry', previous=0, target=1))
        with self.assertRaises(LedgerError):
            self.roll.synchronize()
        self.assertTrue(self.store.pending())
        self.assertEqual(self.engine.symbol, 'TMFJ6')

    def test_cutoff_after_intent_persisted_does_not_leave_an_unsent_guard(self):
        self.now = datetime(2026, 10, 21, 13, 24, 59)
        original_begin = self.store.begin
        def begin(payload):
            original_begin(payload)
            self.now = datetime(2026, 10, 21, 13, 25)
        with patch.object(self.store, 'begin', side_effect=begin):
            with self.assertRaises(RolloverPause):
                self.engine.on_signal(event(1))
        self.assertFalse(self.store.pending())
        self.assertFalse(self.broker.guard.get('pending'))
        self.assertEqual(self.api.orders, [])
        self.move_to_next(1)
        self.confirm_roll()

    def test_unconfirmed_broker_guard_and_concurrent_ledger_change_never_overwrite(self):
        self.move_to_next(1)
        self.broker.guard['pending'] = {'trade_id': 'unknown'}
        with self.assertRaises(LedgerError):
            self.roll.synchronize()
        self.broker.guard.clear()
        self.assertFalse(self.roll.synchronize())
        self.tick += 2
        original = self.roll.snapshot
        calls = 0
        def snapshot(target):
            nonlocal calls
            result = original(target)
            calls += 1
            if calls == 2:
                with self.store.db:
                    self.engine.save(State(expected=-1, first_price='21200'))
            return result
        with patch.object(self.roll, 'snapshot', side_effect=snapshot):
            with self.assertRaises(LedgerError):
                self.roll.synchronize()
        self.assertEqual(self.engine.symbol, 'TMFJ6')
        self.assertEqual(self.engine.state().first_price, '21200')
        self.assertEqual(self.api.orders, [])

    def test_changed_second_snapshot_requires_another_stable_pair(self):
        self.move_to_next(1)
        self.assertFalse(self.roll.synchronize())
        self.tick += 2
        self.api.current = -1
        self.assertFalse(self.roll.synchronize())
        self.assertEqual(self.engine.state().expected, -1)
        self.assertEqual(self.engine.symbol, 'TMFJ6')
        self.tick += 2
        self.assertTrue(self.roll.synchronize())
        self.assertEqual(self.engine.symbol, 'TMFK6')

    def test_missing_cost_disables_adds_but_flat_still_works(self):
        self.move_to_next(1)
        self.api.price = None
        self.confirm_roll()
        self.assertIsNone(self.engine.state().first_price)
        self.engine.on_sample('near', -1300, '01', 23000)
        self.assertFalse(self.engine.on_sample('near', -1200, '02', 23000))
        self.engine.on_signal(event(0))
        self.assertEqual(self.api.sent_codes, ['TMFK6'])

    def test_holiday_adjusted_expiry_and_december_year_boundary(self):
        with self.store.db:
            self.store.db.execute("UPDATE h3_contract_metadata SET expiry='2026-10-22' WHERE code='TMFJ6'")
        self.move_to_next(1)
        self.assertTrue(self.roll.synchronize())  # Calendar day alone cannot trigger.
        self.now = datetime(2026, 10, 22, 13, 25)
        self.assertEqual(self.roll.due(), 'TMFK6')
        self.engine.symbol = 'TMFL6'
        with self.store.db:
            self.store.db.execute("INSERT INTO h3_contract_metadata VALUES ('TMFA7','202701','2027-01-20')")
        self.now = datetime(2026, 12, 16, 13, 25)
        self.assertEqual(self.roll.due(), 'TMFA7')

    def test_sdk_date_objects_are_saved_as_iso_dates(self):
        contracts = self.api.Contracts.Futures.TMF
        contracts.old.delivery_date = datetime(2026, 10, 21).date()
        contracts.new.delivery_date = datetime(2026, 11, 18).date()
        self.roll.remember_contracts()
        self.assertEqual(self.roll.due(), 'TMFK6')
        self.move_to_next(1)
        self.confirm_roll()


if __name__ == '__main__':
    unittest.main()
