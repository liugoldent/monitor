"""Offline regressions for the API_KEY shared net position and H2 Telegram route."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE), str(BASE.parent), str(BASE.parent / 'h3-common')]

import test_configured_unit as fixture
from h_core import Store, TradingError
from h_signal import parse_h2_signal
from net_positions import Positions, read_positions
from portfolio_runtime import FixedPortfolioEngine, PortfolioEngine
from reconcile import reconcile_filled_order
from signal_journal import SignalJournal
from strategy import State
import shioaji_tmf_target as shared


def telegram(direction, message=1, *, sent=None, sender='taiwan_mxf_bot'):
    now = datetime.now(timezone.utc)
    return dict(event='received', route='h2', chat_id=-20, message_id=message,
                sender_username=sender, received_at=now.isoformat(),
                telegram_message_at=(sent or now).isoformat(),
                text=f'自動交易\n浩克2V3 交易訊號通知 (B={int(direction == 1)} S={int(direction == -1)})\nFrom:')


class PortfolioTests(unittest.TestCase):
    event = fixture.ConfiguredUnitTests.event
    tearDown = fixture.ConfiguredUnitTests.tearDown

    def setUp(self):
        fixture.ConfiguredUnitTests.setUp(self)
        self.broker.unit = 1
        self.broker.confirmed_price = '20000'
        self.cancelled = []
        def cancel(key, preflight):
            preflight()
            self.cancelled.append(key)
        self.broker.cancel_working_orders = cancel

    def portfolio(self):
        self.engine = PortfolioEngine(self.store, self.broker, 'TMFK6')
        return self.engine

    def test_netting_h3_plus_two_h2_short_then_reverse(self):
        engine = self.portfolio()
        with patch.object(engine, 'signal_entry_plan', return_value=(2, 'H3加碼')):
            engine.on_signal(self.event('1', 1))
        engine.on_signal(telegram(-1))
        engine.on_signal(telegram(1, 2))
        engine.on_signal(self.event('2', 0))
        self.assertEqual(self.broker.orders, [('net', 2, 2), ('net', -1, 1),
                                              ('net', 2, 3), ('net', -2, 1)])
        self.assertEqual(engine.strategy_positions(), Positions(0, 1))
        self.assertEqual(self.cancelled, ['h3-dual:1', 'h2-tg:-20:1', 'h2-tg:-20:2', 'h3-dual:2'])

    def test_no_delta_still_cancels_and_duplicate_cannot_cancel(self):
        engine = self.portfolio()
        first = telegram(1)
        engine.on_signal(first)
        self.assertFalse(engine.on_signal(first))
        self.assertTrue(engine.on_signal(telegram(1, 2)))
        self.assertEqual(self.broker.orders, [('net', 1, 1)])
        self.assertEqual(self.cancelled, ['h2-tg:-20:1', 'h2-tg:-20:2'])

    def test_h2_first_net_zero_survives_restart_and_h3_flip(self):
        engine = self.portfolio()
        engine.on_signal(telegram(-1))
        with patch.object(engine, 'signal_entry_plan', return_value=(1, '')):
            engine.on_signal(self.event('1', 1))
        self.assertEqual(engine.state().expected, 0)
        self.store.db.close()
        self.store = Store(self.path)
        engine = self.portfolio()
        self.assertEqual(engine.strategy_positions(), Positions(1, -1))
        with patch.object(engine, 'signal_entry_plan', return_value=(3, 'H3加碼')):
            engine.on_signal(self.event('2', -1))
        self.assertEqual(engine.strategy_positions(), Positions(-3, -1))
        self.assertEqual(self.broker.orders[-1], ('net', -4, -4))

    def test_migration_preserves_h3_and_blocks_unexplained_inventory(self):
        with self.store.db:
            self.engine.save(State(expected=2, first_price='20000'))
        self.broker.actual = 1
        engine = self.portfolio()
        self.assertEqual(engine.strategy_positions(), Positions(2, 0))
        with self.assertRaises(TradingError):
            engine.on_signal(telegram(-1))
        self.assertEqual(self.broker.orders, [])
        self.assertEqual(engine.strategy_positions(), Positions(2, 0))

    def test_cancellation_failure_does_not_change_either_leg(self):
        engine = self.portfolio()
        self.broker.cancel_working_orders = MagicMock(side_effect=TradingError('撤單失敗'))
        with self.assertRaises(TradingError):
            engine.on_signal(telegram(-1))
        self.assertEqual(self.broker.orders, [])
        self.assertEqual(engine.strategy_positions(), Positions())

    def test_read_only_recovery_commits_legs_once_after_full_fill(self):
        engine = self.portfolio()
        with patch.object(engine, 'signal_entry_plan', return_value=(2, '')):
            engine.on_signal(self.event('1', 1))
        with patch.object(self.broker, 'send_and_confirm', side_effect=TradingError('斷線')):
            with self.assertRaises(TradingError):
                engine.on_signal(telegram(-1))
        payload = self.store.pending()
        evidence = dict(previous_position=2, target_position=1, quantity=1, side='sell',
                        trade_id='original', contract_code='TMFK6', intent_id=payload['trigger'])
        with self.store.db:
            self.store.db.execute('CREATE TABLE shioaji_order_state (id INTEGER PRIMARY KEY, payload TEXT)')
            self.store.db.execute('INSERT INTO shioaji_order_state VALUES (1,?)',
                                  (json.dumps(dict(pending=evidence)),))
        status = dict(status='Cancelled', deal_quantity=0)
        trade = dict(order=dict(id='original', action='sell', quantity=1),
                     contract=dict(code='TMFK6'), status=status)
        api = MagicMock()
        api.list_trades.return_value = [trade]
        backup = Path(self.temp.name) / 'backup.sqlite'
        with patch.object(shared, '_refresh_status'), patch.object(shared, '_contract'), \
                patch.object(shared, '_read_positions', return_value=[]), \
                patch.object(shared, 'validate_tmf_account'), \
                patch.object(shared, '_net_position', return_value=1):
            with self.assertRaises(TradingError):
                reconcile_filled_order(self.store, api, backup, self.temp.name)
            self.assertEqual(read_positions(self.store), Positions(2, 0))
            self.assertEqual(self.store.pending(), payload)
            status.update(status='Filled', deal_quantity=1)
            reconcile_filled_order(self.store, api, backup, self.temp.name)
        self.assertIsNone(self.store.pending())
        self.assertEqual(engine.strategy_positions(), Positions(2, -1))
        self.assertEqual(engine.state().expected, 1)
        self.assertFalse(engine.on_signal(telegram(-1)))
        api.place_order.assert_not_called()

    def test_h3_history_tiers_still_size_only_h3_while_h2_holds(self):
        engine = self.portfolio()
        engine.on_signal(telegram(-1))
        engine.single_loss_since = None
        engine.drawdown_since = None
        engine.history_path = Path(self.temp.name) / 'history.csv'
        engine.history_path.write_text('timestamp,action,side,price\n'
                                        '2026-11-20T09:00:00,enter,bull,20000\n', encoding='utf-8')
        steps = [(0, 1), (-1000, 2), (-3000, 3), (-2000, 4), (6000, 1)]
        for index, (points, quantity) in enumerate(steps, 1):
            stamp = f'2026-11-20T09:0{index}:00'
            with engine.history_path.open('a', encoding='utf-8') as history:
                history.write(f'{stamp},exiting,bull,{20000+points}\n{stamp},enter,bull,20000\n')
            self.assertTrue(engine.on_signal(self.event(str(index), 1)))
            self.assertEqual(engine.strategy_positions(), Positions(quantity, -1))
            self.assertEqual(engine.state().expected, quantity - 1)
        self.assertEqual(self.broker.orders, [('net', -1, -1), ('net', 1, 0), ('net', 1, 1),
                                              ('net', 1, 2), ('net', 1, 3), ('net', -3, 0)])

    def test_unknown_sizing_keeps_both_confirmed_legs(self):
        engine = self.portfolio()
        engine.on_signal(telegram(-1))
        with patch.object(engine, 'signal_entry_plan', return_value=(2, '')):
            engine.on_signal(self.event('1', 1))
        with patch.object(engine, 'signal_entry_plan', return_value=None):
            engine.on_signal(self.event('2', -1))
        self.assertEqual(engine.strategy_positions(), Positions(2, -1))
        self.assertEqual(self.broker.orders, [('net', -1, -1), ('net', 2, 1)])

    def test_h2_auth_and_original_message_age(self):
        self.assertEqual(parse_h2_signal(telegram(1))[1], 1)
        self.assertEqual(parse_h2_signal(telegram(-1))[1], -1)
        self.assertIsNone(parse_h2_signal(telegram(0)))
        self.assertIsNone(parse_h2_signal(telegram(1, sender='untrusted')))
        self.assertIsNone(parse_h2_signal(telegram(1, sent=datetime.now(timezone.utc) - timedelta(seconds=31))))
        self.assertIsNone(parse_h2_signal(dict(telegram(1), route='h')))
        self.assertIsNone(parse_h2_signal(dict(telegram(1), event='csv_record')))

    def test_delayed_h2_can_flatten_same_direction_legs_within_grace(self):
        engine = self.portfolio()
        with patch.object(engine, 'signal_entry_plan', return_value=(1, '')):
            engine.on_signal(self.event('1', -1))
        engine.on_signal(telegram(-1))
        too_old = telegram(1, 2, sent=datetime.now(timezone.utc) - timedelta(seconds=46))
        self.assertFalse(engine.on_signal(too_old))
        delayed = telegram(1, 3, sent=datetime.now(timezone.utc) - timedelta(seconds=32))
        self.assertTrue(engine.on_signal(delayed))
        self.assertEqual(self.broker.orders[-1], ('net', 2, 0))
        self.assertEqual(engine.strategy_positions(), Positions(-1, 1))

    def test_delayed_h2_cannot_increase_or_reverse_net_exposure(self):
        engine = self.portfolio()
        delayed = telegram(-1, 2, sent=datetime.now(timezone.utc) - timedelta(seconds=32))
        self.assertFalse(engine.on_signal(delayed))
        engine.on_signal(telegram(1))
        self.assertFalse(engine.on_signal(delayed))
        self.assertEqual(self.broker.orders, [('net', 1, 1)])
        self.assertEqual(self.cancelled, ['h2-tg:-20:1'])

    def test_concord_h2_uses_normalized_id_and_net_order(self):
        engine = FixedPortfolioEngine(self.store, self.broker, 'TMFK6')
        event = dict(event='received', route='h2', source='h2_dual_source',
                     signal_key='h2-dual:1', source_kind='concord_sqlite', direction=1,
                     received_at=datetime.now(timezone.utc).isoformat())
        self.assertTrue(engine.on_signal(event))
        self.assertFalse(engine.on_signal(event))
        self.assertEqual(engine.strategy_positions(), Positions(0, 1))
        self.assertEqual(self.broker.orders, [('net', 1, 1)])
        self.assertEqual(self.cancelled, ['h2-dual:1'])
        with patch('mdd_runtime.wait_for_signal_history', side_effect=AssertionError('history consulted')):
            self.assertTrue(engine.on_signal(self.event('1', 1)))
            self.assertEqual(engine.strategy_positions(), Positions(1, 1))
            self.assertTrue(engine.on_signal(self.event('2', -1)))
        self.assertEqual(engine.strategy_positions(), Positions(-1, 1))
        self.assertEqual(self.broker.orders[-2:], [('net', 1, 2), ('net', -2, 0)])

    def test_h2_journal_ignores_history_and_waits_for_complete_lines(self):
        path = Path(self.temp.name) / 'journal.jsonl'
        path.write_text(json.dumps(telegram(1)) + '\n', encoding='utf-8')
        journal = SignalJournal(path, 'h2')
        self.assertIsNone(journal.peek())
        event = telegram(-1, 2)
        with path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(dict(event, route='h')) + '\n')
            file.write(json.dumps(event))
        self.assertIsNone(journal.peek())
        with path.open('a', encoding='utf-8') as file:
            file.write('\n')
        parsed, offset = journal.peek()
        self.assertEqual(parsed, event)
        self.assertEqual(journal.peek(), (parsed, offset))
        journal.offset = offset
        self.assertIsNone(journal.peek())
