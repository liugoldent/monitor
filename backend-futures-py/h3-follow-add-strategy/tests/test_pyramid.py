import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import monitor_and_trade as monitor
import auto_trade
from h_core import Store, TradingError, OrderRejected
from strategy import State, observe, number
from runtime import Engine, signal, LedgerError
from broker import PyramidBroker
from feeds import CsvTail, current_price
from reconcile import reconcile_filled_entry, reconcile_filled_order
from goodway_guerrilla import GOODWAY_GUERRILLA_SYMBOLS, fetch_goodway_history

NEAR, NEXT = GOODWAY_GUERRILLA_SYMBOLS


def event(side=1, message_id=1, age=0):
    return dict(event='received', route='h', sender_username='taiwan_mxf_bot',
                chat_id=1, message_id=message_id,
                received_at=(datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
                text=f'浩克3V3 訊號通知 (B={int(side == 1)} S={int(side == -1)})')


class RuleTests(unittest.TestCase):
    def test_symmetric_crossing_at_exact_500_and_only_one_add(self):
        for side in (1, -1):
            state = State(expected=side, first_price='22000')
            state, add = observe(state, NEAR, -1300 * side, '01', 22000 + 500 * side)
            self.assertFalse(add)
            state, add = observe(state, NEAR, -1200 * side, '02', 22000 + 500 * side)
            self.assertTrue(add)
            state = replace(state, expected=2 * side, added=True)
            state, _ = observe(state, NEAR, -1300 * side, '03', 22000 + 900 * side)
            _, add = observe(state, NEAR, -1200 * side, '04', 22000 + 900 * side)
            self.assertFalse(add)

    def test_below_500_cross_does_not_latch_or_add_when_profit_rises_later(self):
        state = State(expected=1, first_price='22000')
        state, _ = observe(state, NEAR, -1300, '01', 22499)
        state, add = observe(state, NEAR, -1200, '02', 22499)
        self.assertFalse(add)
        state, add = observe(state, NEAR, -1100, '03', 22501)
        self.assertFalse(add)
        state, _ = observe(state, NEAR, -1300, '04', 22501)
        _, add = observe(state, NEAR, -1200, '05', 22501)
        self.assertTrue(add)

    def test_profit_is_current_not_peak(self):
        state = State(expected=1, first_price='22000')
        state, _ = observe(state, NEAR, -1300, '01', 22600)
        _, add = observe(state, NEAR, -1200, '02', 22499)
        self.assertFalse(add)

    def test_sources_are_independent_and_either_can_trigger(self):
        for source in (NEAR, NEXT):
            state = State(expected=1, first_price='22000')
            state, _ = observe(state, NEAR, -1300, '01', 22600)
            state, add = observe(state, NEXT, -1200, '01', 22600)
            self.assertFalse(add)  # Cannot cross from the other contract's value.
            state, _ = observe(state, source, -1300, '02', 22600)
            _, add = observe(state, source, -1200, '03', 22600)
            self.assertTrue(add)

    def test_touch_equality_is_not_itself_a_cross(self):
        for side in (1, -1):
            state = State(expected=side, first_price='22000')
            state, _ = observe(state, NEAR, -1300 * side, '01', 22000 + 600 * side)
            state, add = observe(state, NEAR, -1250 * side, '02', 22000 + 600 * side)
            self.assertFalse(add)
            _, add = observe(state, NEAR, -1200 * side, '03', 22000 + 600 * side)
            self.assertTrue(add)

    def test_revisions_in_same_minute_and_identical_repeats(self):
        state = State(expected=1, first_price='22000')
        state, _ = observe(state, NEAR, -1300, '01', 22600)
        same, add = observe(state, NEAR, -1300, '01', 22600)
        self.assertEqual(same, state)
        self.assertFalse(add)
        state, add = observe(state, NEAR, -1200, '01', 22600)
        self.assertTrue(add)
        same, add = observe(state, NEAR, -1300, '00', 22600)
        self.assertEqual(same, state)
        self.assertFalse(add)

    def test_initial_value_on_far_side_missing_price_and_flat_never_add(self):
        for state in (State(), State(expected=1), State(expected=1, first_price='22000')):
            state, add = observe(state, NEAR, -1200, '01', 22600)
            self.assertFalse(add)
        state = State(expected=1, first_price='22000')
        state, _ = observe(state, NEAR, -1300, '01', None)
        _, add = observe(state, NEAR, -1200, '02', None)
        self.assertFalse(add)

    def test_invalid_numbers_are_rejected(self):
        for value in (float('nan'), float('inf'), None, 'bad'):
            with self.assertRaises(ValueError):
                number(value)


class Catalog(NS):
    def __iter__(self):
        return iter([self.actual])


class API:
    def __init__(self):
        self.current = 0
        self.orders = []
        self.trades = []
        self.price = 22000
        self.reject = self.partial = self.unknown = self.missing_fills = False
        self.futopt_account = object()
        contract = NS(code='TMFJ6', delivery_month='202610', delivery_date='2026-10-21')
        self.Contracts = NS(Futures=NS(TMF=Catalog(TMFR1=contract, actual=contract)))

    def list_positions(self, account):
        return [] if not self.current else [dict(code='TMFJ6', quantity=abs(self.current),
                                                 direction='Buy' if self.current > 0 else 'Sell')]

    def list_trades(self):
        return self.trades

    def update_status(self, *args, **kwargs):
        pass

    def Order(self, **kwargs):
        return NS(**kwargs)

    def place_order(self, contract, order, **kwargs):
        self.orders.append(order)
        filled = 0 if self.reject or self.unknown else order.quantity - int(self.partial)
        self.current += filled * (1 if order.action == 'Buy' else -1)
        status = 'Submitted' if self.unknown else ('Cancelled' if self.reject or self.partial else 'Filled')
        trade = NS(contract=contract, order=NS(id=str(len(self.orders)), action=order.action,
                   quantity=order.quantity), status=NS(
            status=status, deal_quantity=filled, deals=[] if self.missing_fills else [
                dict(price=self.price, quantity=filled, ts=datetime.now().timestamp())] if filled else []))
        self.trades.append(trade)
        return trade


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = Store(self.base / 'state.sqlite')
        self.addCleanup(self.store.db.close)
        self.api = API()
        self.sj = NS(constant=NS(Action=NS(Buy='Buy', Sell='Sell'),
                               FuturesPriceType=NS(MKT='MKT'), OrderType=NS(IOC='IOC'),
                               FuturesOCType=NS(Auto='Auto')))
        self.watchdog = Mock()
        self.watchdog.status.return_value = (False, False)
        self.broker = PyramidBroker(self.api, self.sj, self.store, self.watchdog,
                                    records=self.base / 'records')
        self.engine = Engine(self.store, self.broker, 'TMFJ6')
        self.timer = patch.object(auto_trade.shared.time, 'sleep')
        self.timer.start()
        self.addCleanup(self.timer.stop)

    def add(self, source=NEAR, side=1):
        self.engine.on_sample(source, -1300 * side, '01', 22000 + 600 * side)
        return self.engine.on_sample(source, -1200 * side, '02', 22000 + 600 * side)

    def test_month_change_reports_both_contracts_without_changing_ledger_or_orders(self):
        self.engine.on_signal(event(-1))
        before = self.store.db.execute('SELECT * FROM pyramid_state').fetchall()
        orders = len(self.api.orders)
        with self.assertRaisesRegex(LedgerError, r'帳本=TMFJ6.*部位=-1口.*目前近月=TMFK6'):
            Engine(self.store, self.broker, 'TMFK6')
        self.assertEqual(self.store.db.execute('SELECT * FROM pyramid_state').fetchall(), before)
        self.assertEqual(len(self.api.orders), orders)
        self.assertFalse(self.store.pending())

    def test_end_to_end_long_add_reverse_short_add_then_flat(self):
        self.engine.on_signal(event(1))
        self.api.price = 22600
        self.assertTrue(self.add())
        self.assertEqual(self.engine.state().first_price, '22000.0')
        self.assertFalse(self.add(NEXT))
        self.engine.on_signal(event(-1, 2))
        self.assertEqual(self.api.current, -1)
        self.engine.on_sample(NEXT, 1300, '03', 22000)
        self.assertTrue(self.engine.on_sample(NEXT, 1200, '04', 22000))
        self.engine.on_signal(event(0, 3))
        self.assertEqual(self.api.current, 0)
        self.assertEqual([(o.action, o.quantity) for o in self.api.orders],
                         [('Buy', 1), ('Buy', 1), ('Sell', 2), ('Sell', 1), ('Sell', 1), ('Buy', 2)])
        self.assertFalse(self.store.pending())
        self.assertFalse(self.broker.guard.get('pending'))

    def test_continuous_alias_resolves_real_inventory_on_entry_add_and_exit(self):
        self.api.Contracts.Futures.TMF.TMFR1 = NS(code='TMFR1', target_code='TMFJ6')
        original = self.api.place_order
        def place(contract, order, **kwargs):
            return original(NS(code='TMFJ6'), order, **kwargs)
        self.api.place_order = place
        self.engine.on_signal(event())
        self.add()
        self.engine.on_signal(event(0, 2))
        self.assertEqual(self.api.current, 0)
        self.assertEqual(len(self.api.orders), 3)
        self.assertFalse(self.store.pending())

    def test_alias_missing_target_and_other_month_inventory_still_block(self):
        for contract in (NS(code='TMFR1'), NS(code='TMFR1', target_code='TMFK6')):
            self.api.Contracts.Futures.TMF.TMFR1 = contract
            self.api.current = 1
            with self.assertRaises(auto_trade.shared.BrokerOrderError):
                self.broker.position()
        self.assertEqual(self.api.orders, [])

    def pending_filled_entry(self):
        self.api.price = 46800
        # Reproduce the production failure after Filled but before clearing guards.
        with patch.object(auto_trade.shared, '_resolve_pending', side_effect=[None,
                          auto_trade.shared.BrokerOrderError('contract alias mismatch')]):
            with self.assertRaises(auto_trade.shared.BrokerOrderError):
                self.engine.on_signal(event())
        self.store.db.execute("UPDATE pyramid_state SET symbol='TMFR1'")
        self.store.db.commit()
        self.api.Contracts.Futures.TMF.TMFR1 = NS(code='TMFR1', target_code='TMFJ6')

    def test_reconcile_records_filled_entry_without_resubmitting(self):
        self.pending_filled_entry()
        result = reconcile_filled_entry(self.store, self.api, self.base / 'backup.sqlite', self.base / 'records')
        self.assertEqual(result['actual'], 1)
        engine = Engine(self.store, self.broker, 'TMFJ6')
        self.assertEqual(engine.state().first_price, '46800.0')
        self.assertEqual(engine.state().expected, 1)
        self.assertEqual(len(self.api.orders), 1)
        self.assertFalse(self.store.pending())
        guard = json.loads(self.store.db.execute('SELECT payload FROM shioaji_order_state').fetchone()[0])
        self.assertNotIn('pending', guard)
        backup = sqlite3.connect(self.base / 'backup.sqlite')
        self.addCleanup(backup.close)
        self.assertIsNotNone(backup.execute('SELECT * FROM guard').fetchone())

    def test_reconcile_add_and_flatten_only_records_original_fill(self):
        self.engine.on_signal(event())
        for phase in ('add', 'flatten'):
            with self.subTest(phase=phase):
                with patch.object(auto_trade.shared, '_resolve_pending', side_effect=[None,
                                  auto_trade.shared.BrokerOrderError('lost confirmation')]):
                    with self.assertRaises(auto_trade.shared.BrokerOrderError):
                        if phase == 'add':
                            self.add()
                        else:
                            self.engine.on_signal(event(0, 2))
                count = len(self.api.orders)
                reconcile_filled_order(self.store, self.api, self.base / (phase + '.sqlite'), self.base / 'records')
                self.assertEqual(len(self.api.orders), count)
                self.assertFalse(self.store.pending())
                self.assertEqual(self.engine.state().expected, 2 if phase == 'add' else 0)
                self.broker = PyramidBroker(self.api, self.sj, self.store, self.watchdog, records=self.base / 'records')
                self.engine = Engine(self.store, self.broker, 'TMFJ6')
                self.engine.validate_position()

    def test_reconcile_after_broker_guard_already_resolved(self):
        self.pending_filled_entry()
        row = self.store.db.execute('SELECT payload FROM shioaji_order_state').fetchone()
        guard = json.loads(row[0])
        guard['last_resolved'] = guard.pop('pending')
        with self.store.db:
            self.store.db.execute('UPDATE shioaji_order_state SET payload=?', (json.dumps(guard),))
        reconcile_filled_order(self.store, self.api, self.base / 'backup.sqlite', self.base / 'records')
        self.assertEqual(self.engine.state().expected, 1)
        self.assertEqual(len(self.api.orders), 1)

    def test_reconcile_short_fill_preserves_direction_without_new_order(self):
        with patch.object(auto_trade.shared, '_resolve_pending', side_effect=[None,
                          auto_trade.shared.BrokerOrderError('lost confirmation')]):
            with self.assertRaises(auto_trade.shared.BrokerOrderError):
                self.engine.on_signal(event(-1))
        reconcile_filled_order(self.store, self.api, self.base / 'backup.sqlite', self.base / 'records')
        self.assertEqual(self.engine.state().expected, -1)
        self.assertEqual(self.api.current, -1)
        self.assertEqual(len(self.api.orders), 1)

    def test_reconcile_partial_flatten_keeps_guards(self):
        self.engine.on_signal(event())
        self.add()
        self.api.partial = True
        with self.assertRaises(auto_trade.shared.BrokerOrderError):
            self.engine.on_signal(event(0, 2))
        count = len(self.api.orders)
        with self.assertRaises(TradingError):
            reconcile_filled_order(self.store, self.api, self.base / 'backup.sqlite', self.base / 'records')
        self.assertTrue(self.store.pending())
        self.assertEqual(self.engine.state().expected, 2)
        self.assertEqual(len(self.api.orders), count)

    def test_reconcile_mismatch_never_clears_guards_or_sends_order(self):
        for failure in ('inventory', 'month', 'partial', 'direction', 'missing', 'active'):
            with self.subTest(failure=failure):
                if not self.store.pending():
                    self.pending_filled_entry()
                trade = self.api.trades[0]
                with patch.object(self.api, 'current', 0 if failure == 'inventory' else 1), \
                     patch.object(trade.contract, 'code', 'TMFK6' if failure == 'month' else 'TMFJ6'), \
                     patch.object(trade.status, 'deal_quantity', 0 if failure == 'partial' else 1), \
                     patch.object(trade.order, 'action', 'Sell' if failure == 'direction' else 'Buy', create=True), \
                     patch.object(trade.order, 'quantity', 1, create=True), \
                     patch.object(trade.status, 'status', 'Submitted' if failure == 'active' else 'Filled'), \
                     patch.object(self.api, 'list_trades', return_value=[] if failure == 'missing' else [trade]):
                    with self.assertRaises(TradingError):
                        reconcile_filled_entry(self.store, self.api, self.base / 'backup.sqlite', self.base / 'records')
                self.assertTrue(self.store.pending())
                self.assertEqual(len(self.api.orders), 1)

    def test_restart_persists_added_and_symbol_and_only_resets_observations(self):
        self.engine.on_signal(event())
        self.add()
        engine = Engine(self.store, self.broker, 'TMFJ6')
        engine.validate_position()
        engine.reset_observations()
        self.assertTrue(engine.state().added)
        self.assertEqual(engine.state().first_price, '22000.0')
        self.assertEqual(engine.state().previous_guerrilla, {})
        with self.assertRaises(TradingError):
            Engine(self.store, self.broker, 'TMFK6')

    def test_startup_notice_reports_saved_position_without_reset_or_order(self):
        self.engine.on_signal(event())
        self.add()
        count = len(self.api.orders)
        notice = monitor.startup_notice(self.engine.state())
        self.assertIn('多2口', notice)
        self.assertIn('本輪已加碼：是', notice)
        self.assertIn('不補歷史訊號', notice)
        self.assertIn('結算日13:25自動先平舊月', notice)
        self.assertIn('浮盈≥500點', notice)
        self.assertNotIn('回撤', notice)
        self.assertEqual(len(self.api.orders), count)
        self.assertEqual(self.engine.state().expected, 2)

    def test_duplicate_signal_does_not_reset_round_or_send_again(self):
        original = event()
        self.engine.on_signal(original)
        self.add()
        self.assertFalse(self.engine.on_signal(original))
        self.assertFalse(self.engine.on_signal(event(1, 2)))
        self.assertEqual(self.api.current, 2)
        self.assertEqual(len(self.api.orders), 2)

    def test_same_side_signal_after_dedupe_window_preserves_round_and_add_flag(self):
        self.engine.on_signal(event())
        self.add()
        self.store.db.execute('UPDATE signals SET received=received-11')
        self.store.db.commit()
        self.engine.on_signal(event(1, 2))
        self.assertEqual(self.api.current, 2)
        self.assertTrue(self.engine.state().added)
        self.assertEqual(len(self.api.orders), 2)
        self.assertEqual(self.engine.state().round_key, '1:1')

    def test_untrusted_expired_and_malformed_events_never_order(self):
        for value in (None, [], event(age=31), dict(event(), sender_username='fake'),
                      dict(event(), route='ef'), dict(event(), event='delivered'),
                      dict(event(), message_id=True), dict(event(), received_at='invalid')):
            self.assertFalse(self.engine.on_signal(value))
        self.assertEqual(self.api.orders, [])

    def test_explicit_flat_notice_and_ambiguous_notice(self):
        self.engine.on_signal(event())
        flat = dict(event(0, 2), text='浩克3V3 訊號通知 小型台指近一訊號部位為：空手')
        self.assertTrue(self.engine.on_signal(flat))
        self.assertEqual(self.api.current, 0)
        self.assertIsNone(signal(dict(event(), text='浩克3V3 訊號通知')))
        self.assertIsNone(signal(dict(event(), text='浩克3V3 訊號通知 (B=1 S=1)')))

    def test_partial_flatten_retains_guards_and_never_opens_reverse(self):
        self.engine.on_signal(event())
        self.add()
        self.api.partial = True
        with self.assertRaises(auto_trade.shared.BrokerOrderError):
            self.engine.on_signal(event(-1, 2))
        self.assertEqual(len(self.api.orders), 3)
        self.assertTrue(self.store.pending())
        self.assertTrue(self.broker.guard.get('pending'))
        with self.assertRaises(TradingError):
            self.engine.validate_position()

    def test_rejected_add_stops_without_marking_added_or_retrying(self):
        self.engine.on_signal(event())
        self.api.reject = True
        with self.assertRaises(OrderRejected):
            self.add()
        self.assertFalse(self.engine.state().added)
        self.assertEqual(self.api.current, 1)
        self.assertTrue(self.store.pending())
        with self.assertRaises(TradingError):
            self.add()
        self.assertEqual(len(self.api.orders), 2)

    def test_unknown_entry_is_guarded_before_request_and_cannot_restart(self):
        self.api.unknown = True
        with self.assertRaises(auto_trade.shared.BrokerOrderError):
            self.engine.on_signal(event())
        self.assertTrue(self.store.pending())
        with self.assertRaises(TradingError):
            PyramidBroker(self.api, self.sj, self.store, self.watchdog)

    def test_manual_position_and_disconnect_block_orders(self):
        self.api.current = 1
        with self.assertRaises(TradingError):
            self.engine.on_signal(event())
        self.api.current = 0
        self.watchdog.status.return_value = (True, False)
        with self.assertRaises(TradingError):
            self.engine.on_signal(event(-1, 2))
        self.assertEqual(self.api.orders, [])

    def test_missing_fill_details_disable_add_but_allow_h3_exit(self):
        self.api.missing_fills = True
        self.engine.on_signal(event())
        self.assertIsNone(self.engine.state().first_price)
        self.assertFalse(self.add())
        self.engine.on_signal(event(0, 2))
        self.assertEqual(self.api.current, 0)

    def test_add_preflight_failure_prevents_broker_call_and_retains_guard(self):
        self.engine.on_signal(event())
        self.engine.on_sample(NEAR, -1300, '01', 22600)
        def expired():
            raise TradingError('expired')
        with self.assertRaises(TradingError):
            self.engine.on_sample(NEAR, -1200, '02', 22600, expired)
        self.assertEqual(len(self.api.orders), 1)
        self.assertTrue(self.store.pending())

    def test_live_poll_uses_high_allows_either_source_and_never_adds_twice(self):
        self.engine.on_signal(event())
        source = self.base / 'events.jsonl'
        source.touch()
        now = datetime(2026, 10, 17, 1, 0)
        bar = int(now.replace(tzinfo=monitor.ZoneInfo('Asia/Taipei')).timestamp() * 1000)
        prices = NS(latest={'Record Time': '2026-10-17 01:00:00',
                            'TradingView Time': '2026-10-17 01:00:00', 'Close': '22600'})
        with patch.object(monitor, 'SOURCE', source), patch.object(monitor, 'now_local', return_value=now):
            for ticker in (NEAR, NEXT):
                monitor.poll_guerrilla(self.engine, ticker,
                    {bar: {'high': -1300, 'open': -1200, 'close': -1200}}, prices, 0)
            monitor.poll_guerrilla(self.engine, NEXT,
                {bar: {'high': -1200, 'open': -1300, 'close': -1300}}, prices, 0)
            monitor.poll_guerrilla(self.engine, NEAR, {bar: {'high': -1200}}, prices, 0)
        self.assertEqual(self.api.current, 2)
        self.assertEqual(len(self.api.orders), 2)

    def test_stale_or_failed_source_resets_only_its_baseline(self):
        self.engine.on_signal(event())
        source = self.base / 'events.jsonl'
        source.touch()
        now = datetime(2026, 10, 17, 1, 0)
        bar = int(now.replace(tzinfo=monitor.ZoneInfo('Asia/Taipei')).timestamp() * 1000)
        prices = NS(latest={'Record Time': '2026-10-17 01:00:00',
                            'TradingView Time': '2026-10-17 01:00:00', 'Close': '22600'})
        with patch.object(monitor, 'SOURCE', source), patch.object(monitor, 'now_local', return_value=now):
            monitor.poll_guerrilla(self.engine, NEAR, {bar: {'high': -1300}}, prices, 0)
            monitor.poll_guerrilla(self.engine, NEXT, {bar: {'high': -1300}}, prices, 0)
            monitor.poll_guerrilla(self.engine, NEAR, {}, prices, 0)
            self.assertNotIn(NEAR, self.engine.state().previous_guerrilla)
            self.assertIn(NEXT, self.engine.state().previous_guerrilla)
            monitor.poll_guerrilla(self.engine, NEXT, {bar - 181000: {'high': -1200}}, prices, 0)
            monitor.poll_guerrilla(self.engine, NEXT, {bar: {'high': -1200}}, prices, 0)
        self.assertEqual(self.api.current, 1)  # Recovery establishes a new baseline.

    def test_new_journal_data_is_processed_before_any_add(self):
        self.engine.on_signal(event())
        self.engine.on_sample(NEAR, -1300, '2026-10-17T01:00:00', 22600)
        source = self.base / 'events.jsonl'
        source.write_text(json.dumps(event(-1, 2)) + '\n', encoding='utf-8')
        with patch.object(monitor, 'SOURCE', source):
            monitor.poll_guerrilla(self.engine, NEAR, {}, NS(latest=None), 0)
        self.assertEqual(len(self.api.orders), 1)
        self.assertIn(NEAR, self.engine.state().previous_guerrilla)

    def test_h3_flat_then_reentry_starts_one_new_add_opportunity(self):
        self.engine.on_signal(event())
        self.add()
        self.engine.on_signal(event(0, 2))
        self.engine.on_signal(event(1, 3))
        self.assertEqual(self.api.current, 1)
        self.assertFalse(self.engine.state().added)
        self.assertTrue(self.add())
        self.assertEqual(self.api.current, 2)

    def test_legacy_positions_and_pending_orders_block_migration(self):
        legacy = self.base / 'legacy'
        (legacy / 'runtime').mkdir(parents=True)
        with patch.object(monitor, 'LEGACY', legacy):
            old = legacy / 'runtime/state.json'
            old.write_text(json.dumps(dict(current_entry={'quantity': 1})), encoding='utf-8')
            with self.assertRaises(TradingError):
                monitor.check_legacy()
            old.unlink()
            db = sqlite3.connect(legacy / 'runtime/recovery.sqlite')
            try:
                db.execute('CREATE TABLE h3_recovery_state (payload TEXT)')
                db.execute('INSERT INTO h3_recovery_state VALUES (?)', (json.dumps(dict(expected=1)),))
                db.commit()
                with self.assertRaises(TradingError):
                    monitor.check_legacy()
            finally:
                db.close()

    def test_login_uses_only_api_key_and_check_config_does_not_login(self):
        ca = self.base / 'test.pfx'
        ca.touch()
        sdk = Mock()
        with patch.dict(sys.modules, {'shioaji': sdk}), \
             patch.object(auto_trade, '_api', None), patch.object(auto_trade, '_sj', None), \
             patch.dict(os.environ, {'API_KEY3': 'third-account-key', 'SECRET_KEY3': 'third-secret',
                                     'API_KEY': 'first-account-key', 'SECRET_KEY': 'first-secret',
                                     'CA_PATH': str(ca), 'PERSON_ID': 'first-person',
                                     'CA_PATH3': 'unused-third.pfx', 'PERSON_ID3': 'third-person'}, clear=True):
            auto_trade.initialize_broker_session()
        sdk.Shioaji.assert_called_once_with(simulation=False)
        sdk.Shioaji.return_value.login.assert_called_once_with('first-account-key', 'first-secret')
        sdk.Shioaji.return_value.activate_ca.assert_called_once_with(
            ca_path=str(ca), ca_passwd='first-person', person_id='first-person')
        with patch.object(monitor, 'load_env'), patch.object(monitor, 'check_config'), \
             patch.object(auto_trade, 'initialize_broker_session') as login, \
             patch.object(monitor, 'watch') as watch:
            monitor.main(['--check-config'])
        login.assert_not_called()
        watch.assert_not_called()

    def test_missing_primary_credentials_never_fall_back_to_third_account(self):
        for missing in ('API_KEY', 'SECRET_KEY', 'PERSON_ID'):
            env = dict(API_KEY='first-account-key', SECRET_KEY='first-secret', PERSON_ID='first-person',
                       API_KEY3='third-account-key', SECRET_KEY3='third-secret', PERSON_ID3='third-person')
            del env[missing]
            sdk = Mock()
            with patch.dict(sys.modules, {'shioaji': sdk}), patch.object(auto_trade, '_api', None), \
                 patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(RuntimeError, missing):
                auto_trade.initialize_broker_session()
            sdk.Shioaji.assert_not_called()

    def test_config_uses_primary_key_without_requiring_third_credentials(self):
        ca = self.base / 'test.pfx'
        ca.touch()
        source, prices = self.base / 'events.jsonl', self.base / 'prices.csv'
        source.touch()
        prices.touch()
        env = dict(API_KEY='first-account-key', SECRET_KEY='first-secret', PERSON_ID='first-person',
                   CA_PATH=str(ca), H_UNIT='1')
        with patch.dict(os.environ, env, clear=True), patch.object(monitor, 'SOURCE', source), \
             patch.object(monitor, 'PRICES', prices), patch.object(monitor, 'check_legacy'), \
             patch.object(auto_trade, 'initialize_broker_session') as login:
            monitor.check_config()
            os.environ['API_KEY'] = 'bad'
            os.environ['API_KEY3'] = 'valid-third-account-key'
            with self.assertRaisesRegex(TradingError, 'API_KEY 格式無效'):
                monitor.check_config()
        login.assert_not_called()


class FeedTests(unittest.TestCase):
    def test_partial_csv_lines_and_contract_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prices.csv'
            path.write_text('Symbol,Timeframe,Close\nMXF1!,1,22000\nTX1!,1,999\nMXF1!,1,22', encoding='utf-8')
            feed = CsvTail(path, latest_only=True)
            feed.read()
            self.assertEqual(feed.latest['Close'], '22000')
            with path.open('a') as handle:
                handle.write('100\n')
            feed.read()
            self.assertEqual(feed.latest['Close'], '22100')

    def test_stale_future_invalid_and_wrong_bar_prices_are_unusable(self):
        now = datetime(2026, 10, 17, 1, 0)
        row = {'Record Time': '2026-10-17 01:00:00', 'TradingView Time': '2026-10-17 00:59:00', 'Close': '22500'}
        self.assertEqual(current_price(row, now), 22500)
        for values in ({'Close': 'nan'}, {'Record Time': '2026-10-17 01:01:00'},
                       {'TradingView Time': '2026-10-16 23:00:00'},
                       {'TradingView Time': '2026-10-17 01:01:00'}):
            self.assertIsNone(current_price(dict(row, **values), now))

    def test_live_reader_selects_high_for_each_source_and_filters_future_bars(self):
        now = datetime.now(timezone.utc)
        stamp = int(now.timestamp() * 1000) - 60000
        response = Mock()
        response.json.return_value = {'bars': [{'time': stamp, 'high': -1300, 'open': 9000},
                                               {'time': stamp + 999999, 'high': 9999}]}
        requests = NS(get=Mock(return_value=response))
        with patch.dict(sys.modules, {'requests': requests}):
            for ticker in GOODWAY_GUERRILLA_SYMBOLS:
                history = fetch_goodway_history(ticker, now)
                self.assertEqual(history[stamp]['high'], -1300)
                self.assertEqual(list(history), [stamp])
                self.assertEqual(requests.get.call_args.kwargs['params']['symbol'], ticker)


if __name__ == '__main__':
    unittest.main()
