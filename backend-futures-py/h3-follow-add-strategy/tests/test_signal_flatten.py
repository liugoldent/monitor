"""Offline execution checks shared by all three account policies."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
BACKEND = BASE.parent
sys.path[:0] = [str(BASE), str(BACKEND), str(BACKEND / 'h3-common'),
               str(BACKEND / 'h3-shane-use'), str(BACKEND / 'h3-profit-protection-strategy')]
from h_core import Store, TradingError
from runtime import Engine
from shane_runtime import ShaneEngine
from protection_runtime import ProtectionEngine
from protection_account import ProtectionBroker
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
                engine = ProtectionEngine(store, broker, 'TMFK6')
                sizing = RecoverySizing()
                for _ in range(6):
                    sizing = sizing.close(-10, 2)
                with store.db:
                    engine.save_sizing(sizing)
                engine.on_signal(self.event(1, 1))
                self.assertEqual(engine.state().expected, 3)
                broker.actual = 1  # User takes profit on two of three contracts.
                engine.on_signal(self.event(2, -1))
                self.assertEqual(broker.orders[-2:], [('flatten', 1, 0), ('entry', 0, -3)])
                self.assertEqual(engine.sizing(), sizing)
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

    def test_protection_six_confirmed_rounds_activate_next_entry_boost(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / 'state.sqlite')
            try:
                broker = Broker()
                engine = ProtectionEngine(store, broker, 'TMFK6')
                for key in range(1, 8):
                    engine.on_signal(self.event(key, 1))
                self.assertEqual(engine.sizing().closed_trades, 6)
                self.assertEqual(engine.sizing().loss_streak, 6)
                self.assertTrue(engine.sizing().boosted)
                self.assertEqual(engine.state().expected, 3)
                self.assertEqual(broker.orders[-2:], [('flatten', 2, 0), ('entry', 0, 3)])
            finally:
                store.db.close()


if __name__ == '__main__':
    unittest.main()
