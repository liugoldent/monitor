import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import shane_bootstrap
from h_core import Store
from shane_runtime import ShaneEngine
from shane_account import ShaneBroker, position_unit
from h3_signal_router import Router


class FakeBroker:
    first_fill = '20000'
    rollover = None
    unit = 1

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
        self.orders.append((phase, self.actual, target))
        self.actual = target
        return target


class ShaneRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'state.sqlite')
        self.broker = FakeBroker()
        self.engine = ShaneEngine(self.store, self.broker, 'TMFX6')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def event(self, key, direction):
        return dict(event='received', route='h', source='h3_dual_source', signal_key='h3-dual:'+key,
                    source_kind='concord_sqlite', direction=direction,
                    source_reference=dict(rowid=int(key), signal_id=int(key)),
                    received_at=datetime.now(timezone.utc).isoformat(),
                    text=f'浩克3V3 訊號通知 (B={int(direction == 1)} S={int(direction == -1)})')

    def test_each_distinct_direction_signal_replaces_position(self):
        for key, direction in [('1', 1), ('2', 1), ('3', -1), ('4', 0)]:
            self.assertTrue(self.engine.on_signal(self.event(key, direction)))
        self.assertEqual(self.broker.orders, [
            ('entry', 0, 1), ('flatten', 1, 0), ('entry', 0, 1),
            ('flatten', 1, 0), ('entry', 0, -1), ('flatten', -1, 0)])
        self.assertEqual(self.engine.state().expected, 0)

    def test_same_signal_id_does_not_repeat_orders(self):
        event = self.event('1', 1)
        self.assertTrue(self.engine.on_signal(event))
        self.assertFalse(self.engine.on_signal(event))
        self.assertEqual(self.broker.orders, [('entry', 0, 1)])

    def test_unowned_inventory_blocks_orders(self):
        self.broker.actual = 1
        with self.assertRaises(Exception):
            self.engine.on_signal(self.event('1', -1))
        self.assertEqual(self.broker.orders, [])

    def test_configured_unit_and_existing_position_quantity(self):
        self.broker.unit = 3
        self.engine.on_signal(self.event('1', 1))
        self.assertEqual(self.broker.orders, [('entry', 0, 3)])
        self.broker.unit = 2
        self.engine.on_signal(self.event('2', -1))
        self.assertEqual(self.broker.orders[-2:], [('flatten', 3, 0), ('entry', 0, -2)])
        self.assertEqual(self.engine.state().expected, -2)

    def test_broker_only_allows_configured_entry_quantity(self):
        broker = ShaneBroker.__new__(ShaneBroker)
        broker.unit = 3
        self.assertTrue(broker.valid_transition(0, 3, 'entry', 3))
        self.assertFalse(broker.valid_transition(0, 1, 'entry', 1))
        self.assertTrue(broker.valid_transition(-2, 2, 'flatten', 0))

    def test_unit_must_be_positive_integer(self):
        for value in ('', '0', '-1', '1.5', 'abc'):
            with self.subTest(value=value), patch.dict(os.environ, {'SHANE_UNIT': value}):
                with self.assertRaises(ValueError):
                    position_unit()
        with patch.dict(os.environ, {'SHANE_UNIT': '3'}):
            self.assertEqual(position_unit(), 3)


class RouterSameDirectionTests(unittest.TestCase):
    def test_newer_same_direction_is_published_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            router = Router(root/'vendor.db', root/'telegram.jsonl', root)
            now = datetime.now(timezone.utc)
            for rowid, seconds in ((1, -2), (2, -1)):
                router.accept(dict(direction=1, at=(now+timedelta(seconds=seconds)).isoformat(),
                                   source_kind='concord_sqlite', reference=dict(rowid=rowid, signal_id=1)), now)
                router.db.commit()
            router.flush()
            events = [json.loads(line) for line in router.output.read_text().splitlines()]
            self.assertEqual([event['signal_key'] for event in events], ['h3-dual:1', 'h3-dual:2'])
            router.db.close()


if __name__ == '__main__':
    unittest.main()
