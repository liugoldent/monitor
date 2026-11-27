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
from mdd_runtime import MddEngine
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

    def test_protection_legacy_boost_preserves_six_loss_tier(self):
        sizing = RecoverySizing.from_payload(dict(equity_points='-10', closed_trades=7,
                                                  loss_streak=0, boosted=True))
        self.assertEqual(sizing.quantity(1), 3)

    def test_protection_legacy_losses_and_open_size_do_not_increase_new_entry(self):
        store = Store(':memory:')
        self.addCleanup(store.db.close)
        broker = Broker()
        broker.unit = POSITION_UNIT
        engine = ProtectionEngine(store, broker, 'TMFK6')
        old_sizing = RecoverySizing()
        for _ in range(6):
            old_sizing = old_sizing.close(-1200, 1)
        with store.db:
            engine.save_sizing(old_sizing)
            engine.save(engine.state_type(expected=4, first_price='20000'))
            engine.on_external_inventory(4)
        broker.actual = 4
        restarted = ProtectionEngine(store, broker, 'TMFK6')
        self.assertTrue(restarted.on_signal(self.event(1, -1)))
        self.assertEqual(broker.orders, [('flatten', 4, 0), ('entry', 0, -1)])
        self.assertEqual(restarted.sizing().closed_trades, old_sizing.closed_trades + 1)

if __name__ == '__main__':
    unittest.main()
