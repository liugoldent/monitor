import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE), str(BASE.parent), str(BASE.parent / 'h3-common')]
from h_core import Store
from runtime import Engine
from broker import PyramidBroker
from strategy import State
from auto_trade import position_unit
from auto_rollover import AutomaticRollover


class FakeBroker:
    unit = 3
    first_fill = '20000'
    rollover = None

    def __init__(self):
        self.actual = 0
        self.orders = []
        self.guard = {}
        self.preflight = None

    def check_rollover(self):
        pass

    def position(self):
        return self.actual

    def send_and_confirm(self, delta, phase, target):
        if self.preflight:
            self.preflight()
        self.orders.append((phase, delta, target))
        self.actual = target
        return target


class ConfiguredUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite'
        self.store = Store(self.path)
        self.broker = FakeBroker()
        self.engine = Engine(self.store, self.broker, 'TMFK6')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def event(self, key, direction):
        return dict(event='received', route='h', source='h3_dual_source',
                    signal_key='h3-dual:' + key, source_kind='concord_sqlite',
                    source_reference=dict(rowid=int(key), signal_id=int(key)),
                    direction=direction, received_at=datetime.now(timezone.utc).isoformat(),
                    text=f'浩克3V3 訊號通知 (B={int(direction == 1)} S={int(direction == -1)})')

    def test_entry_add_once_reverse_and_flat(self):
        self.engine.on_signal(self.event('1', 1))
        self.assertEqual(self.engine.state().expected, 3)
        self.engine.on_sample('near', -1300, '2026-11-03T10:00:00', 20500)
        self.assertTrue(self.engine.on_sample('near', -1200, '2026-11-03T10:01:00', 20500))
        self.engine.on_sample('far', -1300, '2026-11-03T10:00:00', 20500)
        self.assertFalse(self.engine.on_sample('far', -1200, '2026-11-03T10:01:00', 20500))
        self.engine.on_signal(self.event('2', 1))
        self.assertEqual(self.engine.state().expected, 3)
        self.engine.on_signal(self.event('3', -1))
        self.engine.on_signal(self.event('4', 0))
        self.assertEqual(self.broker.orders, [('entry', 3, 3), ('add', 1, 4),
                         ('flatten', -4, 0), ('entry', 3, 3),
                         ('flatten', -3, 0), ('entry', -3, -3), ('flatten', 3, 0)])

    def test_restart_new_unit_does_not_resize_old_position(self):
        self.engine.on_signal(self.event('1', -1))
        self.store.db.close()
        self.store = Store(self.path)
        self.engine = Engine(self.store, self.broker, 'TMFK6')
        self.broker.unit = 5
        self.engine.on_signal(self.event('2', -1))
        self.assertEqual(self.engine.state().expected, -5)
        self.engine.on_sample('near', 1300, '2026-11-03T10:00:00', 19500)
        self.engine.on_sample('near', 1200, '2026-11-03T10:01:00', 19500)
        self.assertEqual(self.engine.state().expected, -6)
        self.engine.on_signal(self.event('3', 1))
        self.assertEqual(self.broker.orders[-2:], [('flatten', 6, 0), ('entry', 5, 5)])

    def test_legacy_one_and_two_lot_ledgers_remain_readable(self):
        for quantity, added in [(1, False), (2, True)]:
            state = State(**json.loads(json.dumps(dict(expected=-quantity, first_price='20000', added=added))))
            self.assertEqual(state.validate().expected, -quantity)

    def test_restart_after_add_keeps_flag_and_uncertain_orders_are_not_resent(self):
        self.engine.on_signal(self.event('1', 1))
        self.engine.on_sample('near', -1300, '2026-11-03T10:00:00', 20500)
        self.engine.on_sample('near', -1200, '2026-11-03T10:01:00', 20500)
        self.store.db.close()
        self.store = Store(self.path)
        self.engine = Engine(self.store, self.broker, 'TMFK6')
        self.engine.on_sample('far', -1300, '2026-11-03T10:02:00', 20500)
        self.assertFalse(self.engine.on_sample('far', -1200, '2026-11-03T10:03:00', 20500))
        self.assertEqual(self.engine.state().expected, 4)
        count = len(self.broker.orders)
        with patch.object(self.broker, 'send_and_confirm', side_effect=RuntimeError('unknown fill')):
            with self.assertRaises(RuntimeError):
                self.engine.on_signal(self.event('2', -1))
        self.assertIsNotNone(self.store.pending())
        self.engine = Engine(self.store, self.broker, 'TMFK6')
        with self.assertRaises(Exception):
            self.engine.on_signal(self.event('3', -1))
        self.assertEqual(len(self.broker.orders), count)

    def test_broker_gates_quantities(self):
        broker = PyramidBroker.__new__(PyramidBroker)
        broker.unit = 3
        self.assertTrue(broker.valid_transition(0, 3, 'entry', 3))
        self.assertFalse(broker.valid_transition(0, 1, 'entry', 1))
        self.assertTrue(broker.valid_transition(-3, -1, 'add', -4))
        self.assertFalse(broker.valid_transition(-3, -3, 'add', -6))
        self.assertTrue(broker.valid_transition(8, -8, 'flatten', 0))
        self.assertFalse(broker.valid_transition(8, -1, 'flatten', 0))

    def test_positive_integer_config(self):
        for value in ['', '0', '-1', '1.5', 'True']:
            with self.subTest(value=value), patch.dict(os.environ, H_UNIT=value):
                with self.assertRaises(ValueError):
                    position_unit()
        with patch.dict(os.environ, H_UNIT='3'):
            self.assertEqual(position_unit(), 3)

    def test_rollover_preserves_larger_round(self):
        rollover = AutomaticRollover.__new__(AutomaticRollover)
        rollover.engine = self.engine
        for added in [False, True]:
            state = dict(expected=4, first_price='20000', added=added)
            rollover.validate_plan(dict(stage='open', quantity=4, old_state=state,
                                        key='auto-roll:test', old='TMFK6', new='TMFL6'))


if __name__ == '__main__':
    unittest.main()
