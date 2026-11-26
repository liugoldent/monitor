"""Offline execution checks shared by all three account policies."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
BACKEND = BASE.parent
sys.path[:0] = [str(BASE), str(BACKEND), str(BACKEND / 'h3-common'),
               str(BACKEND / 'h3-shane-use'), str(BACKEND / 'h3-profit-protection-strategy')]
from h_core import Store, TradingError
from auto_rollover import AutomaticRollover
from mdd_runtime import MddEngine, read_history, wait_for_signal_history
from mdd_sizing import H3History, MddSizing
from runtime import Engine
from shane_runtime import ShaneEngine
from protection_runtime import ProtectionEngine
from protection_rollover import ProtectionRollover
from protection_account import POSITION_UNIT, ProtectionBroker
from recovery_sizing import RecoverySizing


class Broker:
    unit = 2
    first_fill = '20000'
    confirmed_price = '20000'
    rollover = None

    def __init__(self):
        self.actual = 0
        self.orders = []
        self.guard = {}
        self.preflight = None
        self.entry_quantity = self.unit

    def position(self):
        return self.actual

    def check_rollover(self):
        pass

    def cancel_working_orders(self, trigger, preflight):
        preflight()
        if self.guard.get('pending'):
            raise TradingError('pending order')

    def valid_transition(self, *args):
        return ProtectionBroker.valid_transition(self, *args)

    def send_and_confirm(self, delta, phase, target):
        if self.preflight:
            self.preflight()
        self.orders.append((phase, self.actual, target))
        self.actual = target
        return target


class SignalFlattenTests(unittest.TestCase):
    def setUp(self):
        history_start = patch.object(ProtectionEngine, 'history_start',
                                     datetime.fromisoformat('2026-11-04T19:01:00+08:00'))
        self.addCleanup(history_start.stop)
        history_start.start()
        wait = patch('protection_runtime.wait_for_signal_history',
                     side_effect=lambda path, event, direction, **kwargs: H3History(MddSizing(), direction, None))
        self.addCleanup(wait.stop)
        wait.start()

    def event(self, key, direction):
        return dict(event='received', route='h', source='h3_dual_source',
                    signal_key='h3-dual:' + str(key), source_kind='concord_sqlite',
                    source_reference=dict(rowid=key, signal_id=key), direction=direction,
                    received_at=datetime.now(timezone.utc).isoformat(),
                    text=f'浩克3V3 訊號通知 (B={int(direction == 1)} S={int(direction == -1)})')

    def test_external_rollover_routes_next_h3_signal_to_new_month(self):
        contracts = [
            SimpleNamespace(code='TMFK6', delivery_month='202611', delivery_date='2026-11-18'),
            SimpleNamespace(code='TMFL6', delivery_month='202612', delivery_date='2026-12-16'),
        ]
        for policy, rollover_type, old_qty, actual in [
            (MddEngine, AutomaticRollover, -4, 0),
            (ProtectionEngine, ProtectionRollover, -1, -1),
        ]:
            with (self.subTest(policy=policy.__name__), tempfile.TemporaryDirectory() as root,
                  ExitStack() as cleanup):
                store = Store(Path(root) / 'state.sqlite')
                cleanup.callback(store.db.close)
                broker = Broker()
                broker.contract_code = 'TMFK6'
                broker.actual = actual
                broker.api = SimpleNamespace(
                    Contracts=SimpleNamespace(Futures=SimpleNamespace(TMF=contracts)),
                    futopt_account=object(), update_status=lambda *args, **kwargs: None,
                    list_trades=lambda: [], list_positions=lambda account: [
                        dict(code='TMFL6', direction='Sell', quantity=abs(actual))
                    ] if actual else [],
                )
                engine = policy(store, broker, 'TMFK6')
                with store.db:
                    engine.save(engine.state_type(expected=old_qty, first_price='20000'))
                    engine.on_external_inventory(old_qty)
                rollover = rollover_type(engine, root, follow_external_rollover=True,
                                         clock=lambda: datetime(2026, 11, 18, 13, 31))
                self.assertFalse(rollover.synchronize(allow_adopt=False))
                self.assertTrue(rollover.synchronize())
                self.assertTrue(rollover.synchronize())
                self.assertEqual(engine.state().expected, actual)
                self.assertEqual(broker.orders, [])
                def send_on_next_month(delta, phase, target):
                    self.assertEqual(broker.contract_code, 'TMFL6')
                    return Broker.send_and_confirm(broker, delta, phase, target)

                with (patch.object(broker, 'send_and_confirm', side_effect=send_on_next_month),
                      patch('mdd_runtime.wait_for_signal_history',
                            return_value=H3History(MddSizing(), 1, None))):
                    engine.on_signal(self.event(1, 1))
                self.assertEqual(broker.orders,
                                 ([('flatten', actual, 0)] if actual else []) + [('entry', 0, 2)])

    def test_all_policies_close_actual_then_enter_unit(self):
        for policy in [Engine, ShaneEngine, ProtectionEngine]:
            # Include manual partial exit, fully flat, increased size, opposite
            # inventory and same-direction signals, without changing UNIT=2.
            for actual in [0, 1, 2, 7, -5]:
                with self.subTest(policy=policy.__name__, actual=actual), tempfile.TemporaryDirectory() as root:
                    store = Store(Path(root) / 'state.sqlite')
                    try:
                        broker = Broker()
                        engine = policy(store, broker, 'TMFK6')
                        engine.on_signal(self.event(1, 1))
                        broker.actual = actual
                        broker.orders.clear()
                        engine.on_signal(self.event(2, 1))
                        expected = ([('flatten', actual, 0)] if actual else []) + [('entry', 0, 2)]
                        self.assertEqual(broker.orders, expected)
                        engine.on_signal(self.event(2, 1))
                        self.assertEqual(broker.orders, expected)  # Same ID is never replayed.
                        engine.on_signal(self.event(3, -1))
                        self.assertEqual(broker.orders[-2:], [('flatten', 2, 0), ('entry', 0, -2)])
                        engine.on_signal(self.event(4, 0))
                        self.assertEqual(broker.orders[-1], ('flatten', -2, 0))
                        self.assertEqual(engine.state().expected, 0)
                    finally:
                        store.db.close()

    def test_pending_unknown_and_partial_close_never_open_new_position(self):
        for policy in [Engine, ShaneEngine, ProtectionEngine]:
            for failure in ['strategy_guard', 'broker_guard', 'exception', 'partial', 'invalid_inventory']:
                with self.subTest(policy=policy.__name__, failure=failure), tempfile.TemporaryDirectory() as root:
                    store = Store(Path(root) / 'state.sqlite')
                    try:
                        broker = Broker()
                        engine = policy(store, broker, 'TMFK6')
                        broker.actual = 5
                        if failure == 'strategy_guard':
                            store.begin(dict(phase='flatten', previous=5, target=0))
                        elif failure == 'broker_guard':
                            broker.guard['pending'] = dict(trade_id='unknown')
                        elif failure == 'invalid_inventory':
                            broker.actual = None
                        def fail(*args):
                            if failure == 'partial':
                                broker.actual = 1
                                return 1
                            raise TradingError('unconfirmed close')
                        with patch.object(broker, 'send_and_confirm', side_effect=fail):
                            with self.assertRaises(TradingError):
                                engine.on_signal(self.event(1, -1))
                        self.assertEqual(broker.orders, [])
                        if failure in ['exception', 'partial']:
                            self.assertIsNotNone(store.pending())
                            with self.assertRaises(TradingError):
                                engine.on_signal(self.event(2, -1))
                    finally:
                        store.db.close()

    def test_expired_signal_never_adopts_or_orders(self):
        for policy in [Engine, ShaneEngine, ProtectionEngine]:
            with self.subTest(policy=policy.__name__), tempfile.TemporaryDirectory() as root:
                store = Store(Path(root) / 'state.sqlite')
                try:
                    broker = Broker()
                    engine = policy(store, broker, 'TMFK6')
                    broker.actual = 7
                    event = self.event(1, -1)
                    event['received_at'] = (datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
                    self.assertFalse(engine.on_signal(event))
                    self.assertEqual(engine.state().expected, 0)
                    self.assertEqual(broker.orders, [])
                finally:
                    store.db.close()

    def test_protection_retains_boost_after_manual_partial_exit(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / 'state.sqlite')
            try:
                broker = Broker()
                broker.unit = POSITION_UNIT
                engine = ProtectionEngine(store, broker, 'TMFK6')
                source = H3History(MddSizing(), 1, None, loss_streak=6, loss_extra_contracts=2)
                with patch('protection_runtime.wait_for_signal_history', return_value=source):
                    engine.on_signal(self.event(1, 1))
                    self.assertEqual(engine.state().expected, 3)
                    broker.actual = 1
                    engine.on_signal(self.event(2, -1))
                self.assertEqual(broker.orders[-2:], [('flatten', 1, 0), ('entry', 0, -3)])
                self.assertEqual(engine.sizing().closed_trades, 0)
                self.assertIsNotNone(store.db.execute(
                    "SELECT 1 FROM audit WHERE event='sizing_external_round_excluded'").fetchone())
            finally:
                store.db.close()

    def test_manual_partial_exit_across_restart(self):
        for policy in [Engine, ShaneEngine, ProtectionEngine]:
            with self.subTest(policy=policy.__name__), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'state.sqlite'
                store = Store(path)
                broker = Broker()
                policy(store, broker, 'TMFK6').on_signal(self.event(1, 1))
                store.db.close()
                broker.actual = 1
                store = Store(path)
                try:
                    engine = policy(store, broker, 'TMFK6')
                    self.assertEqual(engine.validate_position(require_match=False), 1)
                    broker.orders.clear()
                    self.assertEqual(engine.state().expected, 2)  # Boot alone does not adopt/order.
                    engine.on_signal(self.event(2, 1))
                    self.assertEqual(broker.orders, [('flatten', 1, 0), ('entry', 0, 2)])
                finally:
                    store.db.close()

    def test_protection_source_losses_boost_independently_of_broker_fills(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / 'state.sqlite')
            try:
                broker = Broker()
                broker.unit = POSITION_UNIT
                notices = []
                engine = ProtectionEngine(store, broker, 'TMFK6', notices.append)
                history = Path(root) / 'h_trade.csv'
                history.write_text('timestamp,action,side,price\n'
                                   '2026-11-04 19:01:02,enter,bull,20000\n', encoding='utf-8')
                engine.history_path = history
                wait = patch('protection_runtime.wait_for_signal_history',
                             side_effect=lambda path, *_, **kwargs: read_history(path, **kwargs))
                self.addCleanup(wait.stop)
                wait.start()
                engine.on_signal(self.event(1, 1))
                self.assertIn('連輸0筆', notices[-1])
                previous = 1
                broker.confirmed_price = '20010'
                for losses, quantity in enumerate([1, 1, 1, 2, 2, 3], start=1):
                    with self.subTest(losses=losses):
                        with history.open('a', encoding='utf-8') as handle:
                            handle.write(f'2026-11-04 23:44:0{losses},exiting,bull,19990\n'
                                         f'2026-11-04 23:44:0{losses},enter,bull,20000\n')
                        engine.on_signal(self.event(losses + 1, 1))
                        self.assertEqual(broker.orders[-2:], [('flatten', previous, 0), ('entry', 0, quantity)])
                        self.assertIn(f'連輸{losses}筆', notices[-1])
                        previous = quantity
                self.assertEqual(engine.sizing().closed_trades, 6)
                self.assertEqual(engine.sizing().loss_streak, 5)
                broker.actual = 0
                with history.open('a', encoding='utf-8') as handle:
                    handle.write('2026-11-05 08:45:00,exiting,bull,20020\n'
                                 '2026-11-05 08:45:00,enter,bull,20000\n')
                engine.on_signal(self.event(8, 1))
                self.assertIn('連贏1筆、連輸0筆', notices[-1])
                self.assertEqual(broker.orders[-1], ('entry', 0, 3))
                self.assertEqual(engine.sizing().closed_trades, 6)
                with history.open('a', encoding='utf-8') as handle:
                    handle.write('2026-11-05 08:50:00,exiting,bull,20040\n'
                                 '2026-11-05 08:50:00,enter,bear,20040\n')
                engine.on_signal(self.event(9, -1))
                self.assertIn('連贏2筆、連輸0筆', notices[-1])
                self.assertEqual(broker.orders[-2:], [('flatten', 3, 0), ('entry', 0, -1)])
            finally:
                store.db.close()

    def test_protection_legacy_boost_preserves_six_loss_tier(self):
        sizing = RecoverySizing.from_payload(dict(equity_points='-10', closed_trades=7,
                                                  loss_streak=0, boosted=True))
        self.assertEqual(sizing.quantity(1), 3)

    def test_protection_large_loss_adds_once_per_drawdown_and_stacks_with_streak(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(':memory:')
            self.addCleanup(store.db.close)
            broker = Broker()
            broker.unit = POSITION_UNIT
            engine = ProtectionEngine(store, broker, 'TMFK6')
            history = Path(root) / 'h_trade.csv'
            history.write_text('timestamp,action,side,price\n'
                               '2026-11-04 18:00:00,enter,bear,20000\n'
                               '2026-11-04 18:30:00,exiting,bear,22000\n'
                               '2026-11-04 19:00:00,enter,bear,20000\n', encoding='utf-8')
            engine.history_path = history
            self.assertEqual(engine.entry_quantity(), 1)
            previous = 0
            with patch('protection_runtime.wait_for_signal_history', wraps=wait_for_signal_history):
                for key, (points, quantity) in enumerate([
                    (-999, 1), (-1000, 2), (-1500, 2), (-1, 3), (-1, 3),
                    (-1, 4), (3501, 4), (1, 1), (-1000, 2), (-1000, 2),
                ], start=1):
                    with self.subTest(points=points, quantity=quantity):
                        with history.open('a', encoding='utf-8') as handle:
                            stamp = f'2026-11-04 19:{key:02d}:00'
                            handle.write(f'{stamp},exiting,bear,{20000 - points}\n'
                                         f'{stamp},enter,bear,20000\n')
                        broker.orders.clear()
                        engine.on_signal(self.event(key, -1))
                        orders = [('flatten', -previous, 0)] if previous else []
                        self.assertEqual(broker.orders, orders + [('entry', 0, -quantity)])
                        previous = quantity
                        if key == 7:
                            engine = ProtectionEngine(store, broker, 'TMFK6')
                            engine.history_path = history
                            self.assertEqual(engine.entry_quantity(), 4)

    def test_protection_source_recovery_leaves_broker_accounting_intact(self):
        with tempfile.TemporaryDirectory() as root:
            history = Path(root) / 'h_trade.csv'
            history.write_text(
                'timestamp,action,side,price\n'
                '2026-11-04 19:01:02,enter,bull,20000\n'
                '2026-11-04 23:44:02,exiting,bull,17304\n'
                '2026-11-04 23:44:02,enter,bear,20000\n', encoding='utf-8')
            store = Store(Path(root) / 'state.sqlite')
            try:
                broker = Broker()
                broker.unit = 1
                engine = ProtectionEngine(store, broker, 'TMFK6')
                engine.history_path = history
                sizing = RecoverySizing()
                for _ in range(4):
                    sizing = sizing.close(-10, 1)
                with store.db:
                    engine.save_sizing(sizing)

                def relay_append(_):
                    self.assertEqual(broker.orders, [])
                    with history.open('a', encoding='utf-8') as handle:
                        handle.write('2026-11-06 08:46:05,exiting,bear,14890\n'
                                     '2026-11-06 08:46:05,enter,bull,14890\n')

                with patch('protection_runtime.wait_for_signal_history', wraps=wait_for_signal_history), \
                        patch('mdd_runtime.time.sleep', side_effect=relay_append):
                    engine.on_signal(self.event(1, 1))
                self.assertEqual(broker.orders, [('entry', 0, 1)])
                self.assertEqual(engine.sizing(), sizing)
                restarted = ProtectionEngine(store, broker, 'TMFK6')
                restarted.history_path = history
                self.assertEqual(restarted.entry_quantity(), 1)
                self.assertEqual(restarted.sizing(), sizing)
            finally:
                store.db.close()


if __name__ == '__main__':
    unittest.main()
