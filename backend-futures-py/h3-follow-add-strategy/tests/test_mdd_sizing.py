"""Offline tests: no broker connection or real orders."""
from dataclasses import asdict, replace
from pathlib import Path
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE), str(BASE.parent), str(BASE.parent / 'h3-common')]
import test_configured_unit as fixtures
from mdd_runtime import MddEngine
from mdd_sizing import MddSizing, save_sizing, average_fill
from runtime import LedgerError
from strategy import State
from broker import PyramidBroker
from reconcile import reconcile_filled_order
import shioaji_tmf_target as shared


class MddTests(unittest.TestCase):
    event = fixtures.ConfiguredUnitTests.event
    tearDown = fixtures.ConfiguredUnitTests.tearDown

    def setUp(self):
        fixtures.ConfiguredUnitTests.setUp(self)
        self.broker.confirmed_price = '20000'
        self.engine = MddEngine(self.store, self.broker, 'TMFK6')
        self.broker.unit = 2

    def test_sixth_loss_boosts_next_entry_until_recovery(self):
        self.engine.on_signal(self.event('1', 1))
        for loss in range(1, 7):
            self.assertEqual(self.engine.state().expected, 2)
            self.broker.confirmed_price = str(20000 - loss)
            self.engine.on_signal(self.event(str(loss + 1), 1))
            self.assertEqual(self.engine.sizing().loss_streak, loss)
        self.assertEqual(self.engine.state().expected, 3)
        self.broker.confirmed_price = '19995'
        self.engine.on_signal(self.event('8', 1))
        self.assertEqual(self.engine.sizing().loss_streak, 0)
        self.assertEqual(self.engine.state().expected, 3)
        self.broker.confirmed_price = '20000'
        self.engine.on_signal(self.event('9', -1))
        self.assertEqual(self.engine.sizing().equity, '0')
        self.assertEqual(self.engine.state().expected, -2)

    def test_legacy_ledger_starts_at_base_unit_and_preserves_open_round(self):
        legacy = dict(equity='-1200', peak='100', boosted=True,
                      closed_rounds=8, tracking=True, carry='-10')
        with self.store.db:
            self.store.db.execute('UPDATE h3_mdd_sizing SET payload=?', (json.dumps(legacy),))
        sizing = self.engine.sizing()
        self.assertEqual(sizing.loss_streak, 0)
        self.assertEqual(sizing.quantity(2), 2)
        self.assertTrue(sizing.tracking)
        self.assertEqual(sizing.carry, '-10')
    def test_no_intraround_add_and_no_tiers(self):
        with self.store.db:
            save_sizing(self.store, MddSizing(equity='-3000', boosted=True))
        self.engine.on_signal(self.event('1', 1))
        self.assertEqual(self.engine.state().expected, 3)
        for value in [-1300, -1200]:
            self.assertFalse(self.engine.on_sample('near', value, '2026-11-03T10:00:00', 21000))
        self.assertEqual(len(self.broker.orders), 1)

    def test_restart_preserves_peak_boost_and_round(self):
        self.test_no_intraround_add_and_no_tiers()
        before = self.engine.sizing()
        self.engine = MddEngine(self.store, self.broker, 'TMFK6')
        self.assertEqual(self.engine.sizing(), before)
        self.assertEqual(len(self.broker.orders), 1)
        self.engine.on_signal(self.event('2', 0))
        self.assertEqual(self.engine.sizing().closed_rounds, 1)

    def test_unknown_fill_preserves_guard_and_sizing(self):
        self.engine.on_signal(self.event('1', 1))
        before = self.engine.sizing()
        self.broker.confirmed_price = None
        with self.assertRaises(LedgerError):
            self.engine.on_signal(self.event('2', -1))
        self.assertIsNotNone(self.store.pending())
        self.assertEqual(self.engine.sizing(), before)
        with self.assertRaises(Exception):
            self.engine.on_signal(self.event('3', -1))
        self.assertEqual(len(self.broker.orders), 2)

    def test_external_inventory_round_excluded(self):
        self.engine.on_signal(self.event('1', 1))
        self.broker.actual = 1
        self.broker.confirmed_price = '18000'
        self.engine.on_signal(self.event('2', -1))
        self.assertEqual(self.engine.sizing().equity, '0')
        self.assertEqual(self.engine.sizing().closed_rounds, 0)
        self.assertEqual(self.engine.state().expected, -2)

    def test_rollover_single_round_single_contract_points(self):
        sizing = replace(MddSizing(), tracking=True)
        sizing = sizing.filled('roll_close', State(expected=4, first_price='20000'), '19600')
        self.assertEqual(sizing.closed_rounds, 0)
        self.assertEqual(sizing.carry, '-400')
        sizing = sizing.filled('roll_open', State(), '19700')
        sizing = sizing.filled('flatten', State(expected=4, first_price='19700'), '19100')
        self.assertEqual(sizing.equity, '-1000')
        self.assertEqual(sizing.quantity(2), 2)
        self.assertEqual(sizing.closed_rounds, 1)

    def test_weighted_fill_price(self):
        self.assertEqual(average_fill(dict(fill_status='complete', fill_quantity=3,
            fills=[dict(price=20000, quantity=1), dict(price=20003, quantity=2)])), '20002')

    def test_mdd_broker_rejects_add_and_wrong_entry(self):
        broker = PyramidBroker.__new__(PyramidBroker)
        broker.unit, broker.entry_quantity = 3, 4
        self.assertTrue(broker.valid_transition(0, -4, 'entry', -4))
        self.assertFalse(broker.valid_transition(0, 3, 'entry', 3))
        self.assertFalse(broker.valid_transition(4, 1, 'add', 5))
        self.assertTrue(broker.valid_transition(8, -8, 'flatten', 0))

    def test_reconcile_filled_close_updates_mdd_once_without_resubmitting(self):
        self.engine.on_signal(self.event('1', 1))
        self.broker.confirmed_price = None
        with self.assertRaises(LedgerError):
            self.engine.on_signal(self.event('2', 0))
        payload = self.store.pending()
        pending = dict(previous_position=2, target_position=0, quantity=2,
                       side='sell', trade_id='original', contract_code='TMFK6')
        with self.store.db:
            self.store.db.execute('CREATE TABLE shioaji_order_state (id INTEGER PRIMARY KEY, payload TEXT)')
            self.store.db.execute('INSERT INTO shioaji_order_state VALUES (1,?)',
                                  (json.dumps(dict(pending=pending)),))
        trade = dict(order=dict(id='original', action='sell', quantity=2),
                     contract=dict(code='TMFK6'),
                     status=dict(status='Filled', deal_quantity=2,
                                 deals=[dict(price=19000, quantity=2, ts=1793700000)]))
        api = MagicMock()
        api.list_trades.return_value = [trade]
        with patch.object(shared, '_refresh_status'), patch.object(shared, '_contract'), \
                patch.object(shared, '_read_positions', return_value=[]), \
                patch.object(shared, 'validate_tmf_account'):
            reconcile_filled_order(self.store, api, Path(self.temp.name)/'backup.sqlite',
                                   Path(self.temp.name)/'records')
        self.assertEqual(self.engine.sizing().equity, '-1000.0')
        self.assertEqual(self.engine.sizing().closed_rounds, 1)
        self.assertEqual(self.engine.entry_quantity(), 2)
        self.assertIsNone(self.store.pending())
        self.assertEqual(len(self.broker.orders), 2)
        self.assertEqual(self.engine.state().expected, 0)

    def test_corrupt_sizing_is_not_reset(self):
        with self.store.db:
            self.store.db.execute('DELETE FROM h3_mdd_sizing')
        with self.assertRaises(LedgerError):
            MddEngine(self.store, self.broker, 'TMFK6')


if __name__ == '__main__':
    unittest.main()
