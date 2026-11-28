"""Offline tests for the API_KEY3 history, timing and netting rules."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE), str(BASE.parent), str(BASE.parent / 'h3-common')]

from api_key3_history import Curve, MarketPrices
from api_key3_monitor import Key3Broker, connect
from api_key3_strategy import ConditionalPortfolio
from h_core import Store, TradingError
from net_positions import Positions
from reconcile import reconcile_filled_order
import shioaji_tmf_target as shared


START = datetime(2026, 11, 26, 1, 0, tzinfo=timezone.utc)


class Broker:
    def __init__(self):
        self.actual, self.guard, self.orders = 0, {}, []
        self.confirmed_price = '20000'

    def check_rollover(self):
        pass

    def cancel_working_orders(self, key, preflight):
        preflight()

    def position(self):
        return self.actual

    def send_and_confirm(self, delta, phase, target):
        self.preflight()
        self.orders.append((delta, target))
        self.actual = target
        return target


class ConditionalTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.db.close)
        self.broker = Broker()
        self.prices = {}
        self.seq = 0

    def engine(self, h2=None, h3=None):
        initial = Curve(direction=1, entry='20000', at=START.timestamp()-120, closed=10)
        return ConditionalPortfolio(self.store, self.broker, 'TMFL6',
                                    seeds={'h2': h2 or initial, 'h3': h3 or initial},
                                    market=self, started_at=START.timestamp())

    def at(self, at):
        return self.prices[at]

    def emit(self, engine, source, direction, seconds, price, *, age=0):
        self.seq += 1
        at = START + timedelta(seconds=seconds)
        self.prices[at.timestamp()] = Decimal(price)
        counters = f'(B={int(direction == 1)} S={int(direction == -1)})'
        event = dict(event='received', received_at=at.isoformat())
        if source == 'h2':
            event.update(route='h2', chat_id=-1, message_id=self.seq, sender_username='taiwan_mxf_bot',
                         telegram_message_at=at.isoformat(), text=f'浩克2V3 交易訊號通知 {counters}')
        else:
            event.update(route='h', source='h3_dual_source', direction=direction,
                         signal_key=f'h3-dual:{self.seq}', source_kind='concord_sqlite',
                         source_reference={'rowid': self.seq, 'signal_id': 1}, text=f'浩克3V3 訊號通知 {counters}')
        return engine.on_signal(event, now=at + timedelta(seconds=age))

    def test_single_loss_triggers_one_contract_and_exact_recovery_exits(self):
        engine = self.engine()
        self.assertEqual(self.broker.orders, [])
        self.emit(engine, 'h3', -1, 1, 19000)
        self.assertEqual(engine.strategy_positions(), Positions(h3=-1))
        self.emit(engine, 'h3', 1, 2, 18000)
        self.assertEqual(engine.strategy_positions(), Positions())
        self.assertEqual(self.broker.orders, [(-1, -1), (1, 0)])

    def test_fresh_signal_targets_short_one_from_actual_long_one(self):
        engine = self.engine()
        self.broker.actual = 1
        self.emit(engine, 'h3', -1, 1, 19000)
        self.assertEqual(self.broker.orders, [(-2, -1)])
        self.assertEqual(engine.strategy_positions(), Positions(h3=-1))
        self.broker.actual = 0
        self.emit(engine, 'h3', -1, 2, 19000)
        self.assertEqual(self.broker.orders, [(-2, -1), (-1, -1)])

    def test_historical_recovery_updates_target_without_sending_until_fresh_signal(self):
        engine = self.engine()
        self.emit(engine, 'h3', -1, 1, 19000)
        self.emit(engine, 'h3', 1, 2, 18000, age=60)
        self.assertEqual(engine.strategy_positions(), Positions())
        self.assertEqual(self.broker.actual, -1)
        self.assertEqual(self.broker.orders, [(-1, -1)])
        self.emit(engine, 'h3', 1, 65, 18000)
        self.assertEqual(self.broker.orders, [(-1, -1), (1, 0)])

    def test_cross_entry_requires_later_timestamp_and_recovery_clears_both_legs(self):
        engine = self.engine()
        self.emit(engine, 'h3', -1, 1, 19625)
        self.emit(engine, 'h3', 1, 2, 20000)
        self.assertEqual(engine.curves()['h3'].drawdown, 750)
        self.emit(engine, 'h2', -1, 2, 20000)
        self.assertEqual(engine.strategy_positions(), Positions(h3=1))
        self.emit(engine, 'h2', 1, 3, 19700)
        self.assertEqual(engine.strategy_positions(), Positions(h3=1, h2=1))
        self.emit(engine, 'h3', -1, 4, 20750)
        self.assertEqual(engine.strategy_positions(), Positions())
        self.assertEqual(self.broker.orders, [(1, 1), (1, 2), (-2, 0)])

    def test_h2_own_condition_survives_h3_recovery(self):
        h2 = Curve(equity='-500', losses=4, longest_streak=4, direction=1, entry='20000',
                   closed=10, at=START.timestamp()-120)
        h3 = Curve(equity='-750', drawdown_750_at=START.timestamp()-120, direction=-1,
                   entry='20000', closed=10, at=START.timestamp()-120)
        engine = self.engine(h2=h2, h3=h3)
        self.emit(engine, 'h2', -1, 1, 20000)
        self.emit(engine, 'h3', 1, 2, 19250)
        self.assertEqual(engine.strategy_positions(), Positions(h2=-1))
        self.emit(engine, 'h2', 1, 3, 19500)
        self.assertEqual(engine.strategy_positions(), Positions())

    def test_h2_waits_for_four_complete_losses(self):
        engine = self.engine()
        for i in range(1, 5):
            direction = -1 if i % 2 else 1
            self.emit(engine, 'h2', direction, i, 19900 if direction < 0 else 20000)
            self.assertEqual(engine.curves()['h2'].losses, i)
            self.assertEqual(engine.strategy_positions(), Positions(h2=1 if i == 4 else 0))
        self.assertEqual(self.broker.orders, [(1, 1)])

    def test_dual_h2_concord_entry_and_telegram_flat_use_original_times(self):
        h2 = Curve(equity='-500', losses=4, longest_streak=4, direction=1, entry='20000',
                   closed=10, at=START.timestamp()-120)
        engine = self.engine(h2=h2)
        first = START + timedelta(seconds=1)
        self.prices[first.timestamp()] = Decimal('19900')
        entry = dict(event='received', route='h2', source='h2_dual_source',
                     source_kind='concord_sqlite', signal_key='h2-dual:1',
                     direction=-1, received_at=first.isoformat())
        self.assertTrue(engine.on_signal(entry, now=first))
        self.assertFalse(engine.on_signal(entry, now=first))
        self.assertEqual(engine.strategy_positions(), Positions(h2=-1))

        second = first + timedelta(seconds=1)
        self.prices[second.timestamp()] = Decimal('19800')
        received = second + timedelta(seconds=12)
        exit_signal = dict(entry, source_kind='telegram', signal_key='h2-dual:2', direction=0,
                           received_at=second.isoformat(), relay_received_at=received.isoformat())
        self.assertTrue(engine.on_signal(exit_signal, now=received))
        self.assertEqual(engine.curves()['h2'].at, second.timestamp())
        self.assertEqual(engine.strategy_positions(), Positions())
        self.assertEqual(self.broker.orders, [(-1, -1), (1, 0)])

    def test_replay_rebuilds_curve_without_opening_and_restart_loads_state(self):
        engine = self.engine()
        self.emit(engine, 'h3', -1, -10, 19000, age=60)
        self.assertTrue(engine.curves()['h3'].large_loss)
        self.assertEqual(self.broker.orders, [])
        restarted = self.engine()
        self.emit(restarted, 'h3', 1, 1, 19100)
        self.assertEqual(restarted.strategy_positions(), Positions(h3=1))
        self.assertEqual(self.broker.orders, [(1, 1)])

    def test_opposite_legs_net_to_zero_and_h3_flat_releases_cross_h2(self):
        h3 = Curve(equity='-750', longest_streak=2, losses=2,
                   drawdown_750_at=START.timestamp()-120, direction=-1,
                   entry='20000', closed=10, at=START.timestamp()-120)
        engine = self.engine(h3=h3)
        self.emit(engine, 'h2', -1, 1, 20000)
        self.emit(engine, 'h3', 1, 2, 20000)
        self.assertEqual(engine.strategy_positions(), Positions(h3=1, h2=-1))
        self.assertEqual(self.broker.actual, 0)
        self.emit(engine, 'h3', 0, 3, 20750)
        self.assertEqual(engine.strategy_positions(), Positions())
        self.assertEqual(self.broker.orders, [(-1, -1), (1, 0)])

    def test_uncertain_submission_keeps_guard_and_defers_history_commit(self):
        def uncertain(*args):
            raise TradingError('原委託待核對')

        engine = self.engine()
        self.broker.send_and_confirm = uncertain
        self.broker.actual = 1
        with self.assertRaises(TradingError):
            self.emit(engine, 'h3', -1, 1, 19000)
        self.assertEqual(self.store.pending()['trigger'], 'h3-dual:1')
        self.assertFalse(engine.curves()['h3'].large_loss)
        self.assertEqual(engine.strategy_positions(), Positions())

        pending = self.store.pending()
        guard = dict(previous_position=1, target_position=-1, quantity=2, side='sell',
                     trade_id='original', contract_code='TMFL6', intent_id=pending['trigger'])
        with self.store.db:
            self.store.db.execute('CREATE TABLE shioaji_order_state (id INTEGER PRIMARY KEY, payload TEXT)')
            self.store.db.execute('INSERT INTO shioaji_order_state VALUES (1,?)',
                                  (json.dumps(dict(pending=guard)),))
        api = MagicMock()
        api.list_trades.return_value = [dict(
            order=dict(id='original', action='sell', quantity=2), contract=dict(code='TMFL6'),
            status=dict(status='Filled', deal_quantity=2))]
        with tempfile.TemporaryDirectory() as directory, patch.object(shared, '_refresh_status'), \
                patch.object(shared, '_contract'), patch.object(shared, '_read_positions', return_value=[]), \
                patch.object(shared, 'validate_tmf_account'), patch.object(shared, '_net_position', return_value=-1):
            reconcile_filled_order(self.store, api, Path(directory) / 'backup.sqlite', directory)
        self.assertIsNone(self.store.pending())
        self.assertTrue(engine.curves()['h3'].large_loss)
        self.assertEqual(engine.strategy_positions(), Positions(h3=-1))
        self.assertEqual(self.store.db.execute('SELECT id FROM signals').fetchone()[0], 'h3-dual:1')
        api.place_order.assert_not_called()

    def test_proved_zero_fill_commits_history_but_does_not_reopen(self):
        def rejected(*args):
            self.store.resolve_rejected(self.store.pending(), {'filled_quantity': 0})
            raise TradingError('已核對零成交')

        engine = self.engine()
        self.broker.send_and_confirm = rejected
        with self.assertRaises(TradingError):
            self.emit(engine, 'h3', -1, 1, 19000)
        self.assertTrue(engine.curves()['h3'].large_loss)
        self.assertIsNone(self.store.pending())
        self.assertEqual(engine.strategy_positions(), Positions())
        self.assertFalse(self.emit(engine, 'h3', -1, 1, 19000))

    def test_signal_price_must_already_be_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quote.csv'
            path.write_text('Record Time,Symbol,Timeframe,TradingView Time,Close\n'
                            '2026-11-26 09:00:01,MXF1!,1,2026-11-26 09:00:00,20000\n'
                            '2026-11-26 09:01:01,MXF1!,1,2026-11-26 09:01:00,20020\n', encoding='utf-8')
            market = MarketPrices(path)
            self.assertEqual(market.at(START.timestamp()+60), 20000)
            with self.assertRaises(TradingError):
                market.at(START.timestamp()+181)


class AccountIsolationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.db.close)
        self.api = MagicMock()
        self.api.futopt_account = {'broker_id': 'broker', 'account_id': 'third'}
        self.sj = MagicMock()
        self.sj.Shioaji.return_value = self.api
        self.settings = ('fake-api-key3', 'fake-secret3', 'fake-person3', Path('fake.pfx'))

    def test_missing_or_duplicate_account_id_does_not_login(self):
        for settings in ({}, {'API_KEY3_ACCOUNT_ID': 'second', 'H3_PROTECTION_ACCOUNT_ID': 'second'}):
            with self.subTest(settings=settings), patch.dict(os.environ, settings, clear=True), \
                    patch.dict(sys.modules, {'shioaji': self.sj}), self.assertRaises(TradingError):
                connect(self.store, self.settings)
        self.sj.Shioaji.assert_not_called()

    def test_broker_default_must_match_before_enabling_orders(self):
        with patch.dict(os.environ, {'API_KEY3_ACCOUNT_ID': 'other'}, clear=True), \
                patch.dict(sys.modules, {'shioaji': self.sj}), self.assertRaises(TradingError):
            connect(self.store, self.settings)
        self.api.activate_ca.assert_not_called()
        self.api.place_order.assert_not_called()
        self.api.cancel_order.assert_not_called()
        self.api.logout.assert_called_once()

    def test_changed_account_stops_key3_before_order_and_cancel(self):
        with patch.dict(os.environ, {'API_KEY3_ACCOUNT_ID': 'third'}, clear=True), \
                patch.dict(sys.modules, {'shioaji': self.sj}):
            connect(self.store, self.settings)
        watchdog = MagicMock()
        watchdog.status.return_value = False, None
        broker = Key3Broker(self.api, self.sj, self.store, watchdog, records='unused')
        broker.check()
        self.api.futopt_account = {'broker_id': 'broker', 'account_id': 'other'}
        with self.assertRaises(TradingError):
            broker.cancel_working_orders('test', lambda: None)
        self.api.cancel_order.assert_not_called()
        self.api.place_order.assert_not_called()


if __name__ == '__main__':
    unittest.main()
