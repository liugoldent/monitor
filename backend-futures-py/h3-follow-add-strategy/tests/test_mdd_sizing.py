"""Offline tests: no broker connection or real orders."""
from dataclasses import replace
from pathlib import Path
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE), str(BASE.parent), str(BASE.parent / 'h3-common')]
import test_configured_unit as fixtures
from mdd_runtime import MddEngine
from mdd_sizing import MddSizing, matches_order_snapshot, save_sizing, average_fill
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
        self.notices = []
        self.engine = MddEngine(self.store, self.broker, 'TMFK6', self.notices.append)
        self.history = Path(self.temp.name) / 'h_trade.csv'
        self.history.write_text('timestamp,action,side,price\n'
                                '2026-11-04 19:01:02,enter,bull,20000\n', encoding='utf-8')
        self.engine.history_path = self.history
        self.engine.drawdown_since = None
        self.broker.unit = 1

    def add_history_round(self, points, next_side='bull', *, closed_at='2026-11-04 23:44:02'):
        with self.history.open('a', encoding='utf-8') as handle:
            handle.write(f'{closed_at},exiting,bull,{20000 + points}\n'
                         f'{closed_at},enter,{next_side},20000\n')

    def test_signal_source_adds_at_4000_and_6000_and_holds_until_recovery(self):
        self.add_history_round(100)
        self.add_history_round(-999)
        self.engine.on_signal(self.event('1', 1))
        self.assertIn('目前回撤 999 點', self.notices[-1])
        self.assertEqual(self.broker.orders, [('entry', 1, 1)])
        previous = 1
        for key, points, drawdown, quantity in [
            (2, -3000, 3999, 1), (3, -1, 4000, 2), (4, -1999, 5999, 2),
            (5, -1, 6000, 3), (6, 5999, 1, 3), (7, 1, 0, 1),
        ]:
            with self.subTest(drawdown=drawdown):
                self.add_history_round(points)
                self.engine.on_signal(self.event(str(key), 1))
                self.assertEqual(self.broker.orders[-2:], [('flatten', -previous, 0), ('entry', quantity, quantity)])
                self.assertIn(f'目前回撤 {drawdown} 點', self.notices[-1])
                previous = quantity
        self.assertEqual(self.engine.sizing().equity, '0')

    def test_single_loss_counts_only_from_cutoff_and_stacks_once_until_recovery(self):
        self.add_history_round(-1000, closed_at='2026-11-18 14:16:19')
        self.assertEqual(self.engine.entry_quantity(), 1)
        self.add_history_round(-1000, closed_at='2026-11-18 14:16:20')
        self.engine.on_signal(self.event('1', 1))
        self.assertEqual(self.broker.orders, [('entry', 2, 2)])
        self.assertIn('累計回撤加 0 口，單筆虧損加 1 口', self.notices[-1])
        self.add_history_round(-2000, closed_at='2026-11-18 14:17:00')
        self.assertEqual(self.engine.entry_quantity(), 3)
        self.add_history_round(-2000, closed_at='2026-11-18 14:17:01')
        self.assertEqual(self.engine.entry_quantity(), 4)
        self.add_history_round(5999, closed_at='2026-11-18 14:18:00')
        restarted = MddEngine(self.store, self.broker, 'TMFK6')
        restarted.history_path = self.history
        restarted.drawdown_since = None
        self.assertEqual(restarted.entry_quantity(), 4)
        self.add_history_round(1, closed_at='2026-11-18 14:19:00')
        self.add_history_round(-999, closed_at='2026-11-18 14:20:00')
        self.assertEqual(restarted.entry_quantity(), 1)

    def test_drawdown_reset_keeps_existing_equity_and_clears_old_tiers(self):
        self.add_history_round(-5259)
        self.add_history_round(3556, closed_at='2026-11-19 23:31:02')
        restarted = MddEngine(self.store, self.broker, 'TMFK6')
        restarted.history_path = self.history
        self.assertEqual(restarted.entry_quantity(), 1)
        self.assertIn('回撤 1703 點', restarted.entry_plan()[1])
        for index, (points, quantity) in enumerate(
            [(-800, 1), (-800, 1), (-696, 1), (-1, 2), (-999, 2),
             (-1000, 3), (-1, 4), (5999, 4), (1, 1)], start=3,
        ):
            with self.subTest(points=points):
                self.add_history_round(points, closed_at=f'2026-11-19 23:31:{index:02d}')
                self.assertEqual(restarted.entry_quantity(), quantity)

    def test_missing_history_allows_full_exit_and_blocks_reentry(self):
        self.engine.on_signal(self.event('1', 1))
        self.history.unlink()
        with self.assertRaisesRegex(LedgerError, '暫停新進場'):
            self.engine.on_signal(self.event('2', 1))
        self.assertEqual(self.broker.orders, [('entry', 1, 1), ('flatten', -1, 0)])
        self.assertIsNone(self.store.pending())

    def test_reversal_waits_for_csv_recovery_before_sizing(self):
        self.add_history_round(-2696)
        def relay_append(_):
            self.assertEqual(self.broker.orders, [])
            self.add_history_round(5110, next_side='bear')
        with patch('mdd_runtime.time.sleep', side_effect=relay_append) as wait:
            self.engine.on_signal(self.event('1', -1))
        wait.assert_called_once()
        self.assertEqual(self.broker.orders, [('entry', -1, -1)])
        self.assertIn('目前回撤 0 點', self.notices[-1])

    def test_expiry_waiting_for_csv_skips_entry(self):
        self.add_history_round(-2696)
        with patch('mdd_runtime.signal', side_effect=[True, None]), patch('mdd_runtime.time.sleep'):
            self.engine.on_signal(self.event('1', -1))
        self.assertEqual(self.broker.orders, [])
        self.assertIn('本筆跳過進場', self.notices[-1])

    def test_legacy_ledger_preserves_previous_drawdown_tier_and_open_round(self):
        legacy = dict(equity='-500', peak='100', boosted=True,
                      closed_rounds=8, tracking=True, carry='-10')
        with self.store.db:
            self.store.db.execute('UPDATE h3_mdd_sizing SET payload=?', (json.dumps(legacy),))
        sizing = self.engine.sizing()
        self.assertEqual(sizing.quantity(1), 3)
        self.assertTrue(sizing.tracking)
        self.assertEqual(sizing.carry, '-10')

    def test_streak_ledger_migrates_using_existing_drawdown(self):
        legacy = dict(equity='-650', peak='100', boosted=False,
                      closed_rounds=8, tracking=True, carry='-10', loss_streak=0)
        with self.store.db:
            self.store.db.execute('UPDATE h3_mdd_sizing SET payload=?', (json.dumps(legacy),))
        sizing = self.engine.sizing()
        self.assertEqual(sizing.quantity(1), 2)
        self.assertEqual(sizing.carry, '-10')
        self.assertTrue(matches_order_snapshot(self.store, legacy))
        self.assertFalse(matches_order_snapshot(self.store, dict(legacy, loss_streak=1)))

    def test_no_intraround_add(self):
        self.add_history_round(-4000)
        with self.store.db:
            save_sizing(self.store, MddSizing(equity='-4000', extra_contracts=2))
        self.engine.on_signal(self.event('1', 1))
        self.assertEqual(self.engine.state().expected, 2)
        for value in [-1300, -1200]:
            self.assertFalse(self.engine.on_sample('near', value, '2026-11-03T10:00:00', 21000))
        self.assertEqual(len(self.broker.orders), 1)

    def test_restart_preserves_peak_boost_and_round(self):
        self.test_no_intraround_add()
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
        self.broker.actual = 5
        self.broker.confirmed_price = '18000'
        self.add_history_round(0, next_side='bear')
        self.engine.on_signal(self.event('2', -1))
        self.assertEqual(self.engine.sizing().equity, '0')
        self.assertEqual(self.engine.sizing().closed_rounds, 0)
        self.assertEqual(self.broker.orders[-2:], [('flatten', -5, 0), ('entry', -1, -1)])

    def test_rollover_single_round_single_contract_points(self):
        sizing = replace(MddSizing(), tracking=True)
        sizing = sizing.filled('roll_close', State(expected=4, first_price='20000'), '19600')
        self.assertEqual(sizing.closed_rounds, 0)
        self.assertEqual(sizing.carry, '-400')
        sizing = sizing.filled('roll_open', State(), '19700')
        sizing = sizing.filled('flatten', State(expected=4, first_price='19700'), '19100')
        self.assertEqual(sizing.equity, '-1000')
        self.assertEqual(sizing.quantity(1), 3)
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

    def test_reconcile_zero_fill_requires_matching_inventory_and_keeps_consumed_signal(self):
        event = self.event('1', -1)
        self.engine.consume_signal(('h3-dual:1', -1, 0))
        payload = dict(trigger='h3-dual:1', phase='entry', previous=0, target=-3, side='sell', quantity=3)
        self.engine.prepare_order(payload)
        self.store.begin(payload)
        before = self.engine.sizing()
        api = MagicMock()
        result = dict(previous_position=0, target_position=-3, quantity=3, side='sell',
                      trade_id='original', contract_code='TMFK6', status='failed',
                      filled_quantity=0, actual_position=0)
        broker = PyramidBroker(api, None, self.store, MagicMock(), records=self.temp.name)
        broker.guard = dict(last_resolved=result)
        broker.persist()
        api.list_trades.return_value = [dict(order=dict(id='original', action='sell', quantity=3),
            contract=dict(code='TMFK6'), status=dict(status='Failed', deal_quantity=0, msg='可委託金額不足'))]
        backup = Path(self.temp.name) / 'backup.sqlite'
        with patch.object(shared, '_refresh_status'), patch.object(shared, '_contract'), \
                patch.object(shared, '_read_positions', return_value=[]), \
                patch.object(shared, 'validate_tmf_account'), \
                patch.object(shared, '_net_position', side_effect=[-3, 0]):
            with self.assertRaisesRegex(Exception, '券商庫存 -3 口'):
                reconcile_filled_order(self.store, api, backup, self.temp.name, allow_rejected=True)
            self.assertEqual(self.store.pending(), payload)
            evidence = reconcile_filled_order(self.store, api, backup, self.temp.name, allow_rejected=True)
        self.assertTrue(evidence['rejected'])
        self.assertEqual(evidence['message'], '可委託金額不足')
        self.assertIsNone(self.store.pending())
        self.assertEqual(self.engine.sizing(), before)
        self.assertFalse(self.engine.on_signal(event))
        api.place_order.assert_not_called()

    def test_reconcile_filled_close_updates_mdd_once_without_resubmitting(self):
        self.engine.on_signal(self.event('1', 1))
        self.broker.confirmed_price = None
        with self.assertRaises(LedgerError):
            self.engine.on_signal(self.event('2', 0))
        pending = dict(previous_position=1, target_position=0, quantity=1,
                       side='sell', trade_id='original', contract_code='TMFK6')
        with self.store.db:
            self.store.db.execute('CREATE TABLE shioaji_order_state (id INTEGER PRIMARY KEY, payload TEXT)')
            self.store.db.execute('INSERT INTO shioaji_order_state VALUES (1,?)',
                                  (json.dumps(dict(pending=pending)),))
        trade = dict(order=dict(id='original', action='sell', quantity=1),
                     contract=dict(code='TMFK6'),
                     status=dict(status='Filled', deal_quantity=1,
                                 deals=[dict(price=19000, quantity=1, ts=1793700000)]))
        api = MagicMock()
        api.list_trades.return_value = [trade]
        with patch.object(shared, '_refresh_status'), patch.object(shared, '_contract'), \
                patch.object(shared, '_read_positions', return_value=[]), \
                patch.object(shared, 'validate_tmf_account'):
            reconcile_filled_order(self.store, api, Path(self.temp.name)/'backup.sqlite',
                                   Path(self.temp.name)/'records')
        self.assertEqual(self.engine.sizing().equity, '-1000.0')
        self.assertEqual(self.engine.sizing().closed_rounds, 1)
        self.assertEqual(self.engine.sizing().quantity(1), 3)
        self.assertEqual(self.engine.entry_quantity(), 1)
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
