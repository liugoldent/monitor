"""Service lifecycle tests use no broker connections or real sleeps."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor_and_trade as monitor
import auto_trade
from h_core import TradingError
from runtime import LedgerError


class SupervisorTests(unittest.TestCase):
    def test_repeated_error_keeps_retrying_without_exiting_or_spamming(self):
        store, notify = Mock(), Mock()
        store.pending.return_value = {'phase': 'entry'}
        error = auto_trade.shared.BrokerOrderError('等待原委託核對')
        with patch.object(monitor, 'run_session', side_effect=[error, error, KeyboardInterrupt()]) as run, \
             patch.object(monitor.time, 'sleep') as sleep, patch('builtins.print') as output, \
             patch.dict(os.environ, {'H3_RECOVERY_RETRY_SECONDS': '5'}):
            with self.assertRaises(KeyboardInterrupt):
                monitor.supervise(store, notify, 2)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        store.audit.assert_called_once()
        notify.assert_called_once()
        self.assertIn('程序仍在', notify.call_args.args[0])
        output.assert_called_once()

    def test_ledger_failure_waits_one_minute_and_does_not_spam(self):
        store, notify = Mock(), Mock()
        store.pending.return_value = None
        with patch.object(monitor, 'run_session', side_effect=[LedgerError('合約月份不符'),
                          LedgerError('合約月份不符'), KeyboardInterrupt()]), \
             patch.object(monitor.time, 'monotonic', side_effect=[0, 60]), \
             patch.object(monitor.time, 'sleep') as sleep, patch('builtins.print') as output:
            with self.assertRaises(KeyboardInterrupt):
                monitor.supervise(store, notify, 2)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 60])
        notify.assert_called_once()
        output.assert_called_once()

    def test_notification_failure_does_not_stop_retry(self):
        store, notify = Mock(), Mock(side_effect=OSError('notification failed'))
        store.pending.return_value = None
        with patch.object(monitor, 'run_session', side_effect=[TimeoutError(), KeyboardInterrupt()]) as run, \
             patch.object(monitor.time, 'sleep'), patch('builtins.print'):
            with self.assertRaises(KeyboardInterrupt):
                monitor.supervise(store, notify, 2)
        self.assertEqual(run.call_count, 2)

    def test_same_failure_reminder_is_throttled_for_five_minutes(self):
        store, notify = Mock(), Mock()
        store.pending.return_value = None
        with patch.object(monitor, 'run_session', side_effect=[TimeoutError(), TimeoutError(),
                          TimeoutError(), KeyboardInterrupt()]), \
             patch.object(monitor.time, 'monotonic', side_effect=[0, 5, 300]), \
             patch.object(monitor.time, 'sleep'), patch('builtins.print'):
            with self.assertRaises(KeyboardInterrupt):
                monitor.supervise(store, notify, 2)
        self.assertEqual(notify.call_count, 2)

    def test_unconfirmed_order_is_read_only_and_session_always_closed(self):
        store, api = Mock(), Mock()
        store.pending.return_value = {'phase': 'entry'}
        with patch.object(monitor, 'check_legacy'), \
             patch.object(auto_trade, 'initialize_broker_session', return_value=api), \
             patch.object(auto_trade, 'close_broker_session') as close, \
             patch.object(monitor, 'reconcile_filled_order', side_effect=TradingError('not confirmed')) as reconcile, \
             patch.object(monitor, 'PyramidBroker') as broker, patch.object(monitor, 'watch') as watch:
            with self.assertRaises(TradingError):
                monitor.run_session(store, Mock(), 2)
        reconcile.assert_called_once()
        broker.assert_not_called()
        watch.assert_not_called()
        api.place_order.assert_not_called()
        close.assert_called_once()

    def test_reconciled_order_resumes_watch_without_resubmitting(self):
        store, api = Mock(), Mock()
        store.pending.return_value = {'phase': 'entry'}
        with patch.object(monitor, 'check_legacy'), \
             patch.object(auto_trade, 'initialize_broker_session', return_value=api), \
             patch.object(auto_trade, 'close_broker_session') as close, \
             patch.object(monitor, 'reconcile_filled_order', return_value={'actual': 1}), \
             patch.object(auto_trade.shared, 'tmf_contract_code', return_value='TMFJ6'), \
             patch.object(monitor, 'PyramidBroker'), patch.object(monitor, 'Engine'), \
             patch.object(monitor, 'ledger_symbol', return_value='TMFJ6'), \
             patch.object(monitor, 'AutomaticRollover'), \
             patch.object(monitor, 'watch') as watch, patch('builtins.print'):
            monitor.run_session(store, Mock(), 2)
        watch.assert_called_once()
        api.place_order.assert_not_called()
        close.assert_called_once()

    def test_closed_or_failed_login_is_not_reused(self):
        api, failed = Mock(), Mock()
        api.logout.side_effect = OSError('logout failed')
        with patch.object(auto_trade, '_api', api), patch.object(auto_trade, '_sj', object()), \
             patch.object(auto_trade, '_failed_apis', [failed]), patch('builtins.print'):
            auto_trade.close_broker_session()
            self.assertIsNone(auto_trade._api)
            self.assertIsNone(auto_trade._sj)
            self.assertEqual(auto_trade._failed_apis, [])
        api.logout.assert_called_once()
        failed.logout.assert_called_once()

    def test_rollover_pending_uses_its_own_recovery_and_allows_original_broker_guard(self):
        store, api = Mock(), Mock()
        store.pending.return_value = {'phase': 'roll_close'}
        with patch.object(monitor, 'check_legacy'), \
             patch.object(auto_trade, 'initialize_broker_session', return_value=api), \
             patch.object(auto_trade, 'close_broker_session'), \
             patch.object(monitor, 'reconcile_filled_order') as regular, \
             patch.object(auto_trade.shared, 'tmf_contract_code', return_value='TMFJ6'), \
             patch.object(monitor, 'ledger_symbol', return_value='TMFJ6'), \
             patch.object(monitor, 'PyramidBroker') as broker, \
             patch.object(monitor, 'Engine'), patch.object(monitor, 'AutomaticRollover') as rollover, \
             patch.object(monitor, 'watch') as watch:
            monitor.run_session(store, Mock(), 2)
        regular.assert_not_called()
        self.assertTrue(broker.call_args.kwargs['allow_pending'])
        rollover.assert_called_once()
        watch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
