"""Inventory reporting must be durable and must never call an order path."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import monitor_and_trade  # establishes the shared module paths, without login
from h_core import Store
from inventory import InventoryReporter


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'state.sqlite')
        self.addCleanup(self.store.db.close)
        self.engine = Mock(store=self.store)
        self.engine.broker.position.return_value = -2
        self.engine.state.return_value.expected = -2
        self.reporter = InventoryReporter(self.engine)

    def assert_no_orders(self):
        self.engine.order.assert_not_called()
        self.engine.on_signal.assert_not_called()
        self.engine.broker.send_and_confirm.assert_not_called()
        self.assertIsNone(self.store.pending())

    def test_0835_boundary_once_per_day_and_next_day(self):
        self.assertFalse(self.reporter.daily(datetime(2026, 10, 19, 8, 34, 59)))
        self.assertTrue(self.reporter.daily(datetime(2026, 10, 19, 8, 35)))
        self.assertFalse(self.reporter.daily(datetime(2026, 10, 19, 8, 36)))
        self.assertTrue(self.reporter.daily(datetime(2026, 10, 20, 8, 35)))
        self.assertEqual(self.engine.broker.position.call_count, 2)
        self.assertIn('空2口', self.engine.notify.call_args.args[0])
        self.assertIn('08:35查倉', self.engine.notify.call_args.args[0])
        self.assert_no_orders()

    def test_same_day_restart_does_not_duplicate_scheduled_report(self):
        self.reporter.daily(datetime(2026, 10, 19, 8, 35))
        restarted = InventoryReporter(self.engine)
        self.assertFalse(restarted.daily(datetime(2026, 10, 19, 9)))
        self.assertEqual(self.engine.broker.position.call_count, 1)
        self.assert_no_orders()

    def test_startup_before_0835_reports_then_checks_at_0835(self):
        self.reporter.startup(datetime(2026, 10, 19, 8, 30))
        self.assertIn('啟動查倉', self.engine.notify.call_args.args[0])
        self.assertTrue(self.reporter.daily(datetime(2026, 10, 19, 8, 35)))
        self.assertEqual(self.engine.broker.position.call_count, 2)
        self.assert_no_orders()

    def test_startup_after_0835_counts_as_today_but_each_restart_reports(self):
        self.reporter.startup(datetime(2026, 10, 19, 9))
        self.assertFalse(self.reporter.daily(datetime(2026, 10, 19, 9, 1)))
        restarted = InventoryReporter(self.engine)
        restarted.startup(datetime(2026, 10, 19, 10))
        self.assertFalse(restarted.daily(datetime(2026, 10, 19, 10, 1)))
        self.assertEqual(self.engine.broker.position.call_count, 2)
        self.assert_no_orders()

    def test_mismatched_inventory_only_reports_without_adjusting_it(self):
        self.engine.broker.position.return_value = 1
        self.reporter.daily(datetime(2026, 10, 19, 8, 35))
        self.assertIn('不一致', self.engine.notify.call_args.args[0])
        self.assertIn('多1口', self.engine.notify.call_args.args[0])
        self.assert_no_orders()

    def test_failure_does_not_infer_flat_or_retry_and_redacts_exception(self):
        self.engine.broker.position.side_effect = RuntimeError('private-secret-person-id')
        self.reporter.daily(datetime(2026, 10, 19, 8, 35))
        self.assertFalse(self.reporter.daily(datetime(2026, 10, 19, 8, 36)))
        message = self.engine.notify.call_args.args[0]
        self.assertIn('查倉失敗', message)
        self.assertNotIn('private-secret', message)
        self.assertNotIn('空手', message)
        row = self.store.db.execute('SELECT status,payload FROM h3_inventory_reports').fetchone()
        self.assertEqual(row[0], 'failed')
        self.assert_no_orders()


if __name__ == '__main__':
    unittest.main()
