"""Offline cancellation and signal-ordering regression tests; no broker login."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(BACKEND/'h3-profit-protection-strategy'), str(BACKEND/'h3-follow-add-strategy'),
               str(BACKEND/'h3-common'), str(BACKEND)]
from cancel_working import cancel_working_orders
from h_core import TradingError
import test_signal_flatten as signal_tests
Broker = signal_tests.Broker
from protection_runtime import ProtectionEngine
from protection_account import ProtectionBroker
from broker import PyramidBroker
from mdd_runtime import MddEngine
import shioaji_tmf_target as shared
from h_core import Store
import tempfile


def trade(key='one', status='Submitted', code='TMFK6', account='two'):
    return dict(contract=dict(code=code), order=dict(id=key, account=dict(broker_id='F', account_id=account)),
                status=dict(status=status))


class CancelTests(unittest.TestCase):
    account_label = 'API_KEY2'
    account_id = 'two'

    def setUp(self):
        self.orders = [trade(account=self.account_id)]
        self.api = SimpleNamespace(futopt_account=dict(broker_id='F', account_id=self.account_id),
            list_trades=Mock(side_effect=lambda: self.orders), cancel_order=Mock())
        self.broker = SimpleNamespace(api=self.api, account_label=self.account_label, contract_code='TMFK6', check=Mock(),
            store=SimpleNamespace(pending=Mock(return_value=None), audit=Mock()), guard={})
        self.preflight = Mock()
        refresh = patch('cancel_working.shared._refresh_status')
        self.addCleanup(refresh.stop)
        refresh.start()

    def run_cancel(self):
        return cancel_working_orders(self.broker, 'signal', self.preflight, attempts=2, delay=0)

    def test_only_own_current_contract_cancelled_and_confirmed(self):
        self.orders += [trade('other', account='other'), trade('month', code='TMFL6'),
                        trade('stock', code='2330'), trade('old', status='Filled')]
        self.api.cancel_order.side_effect = lambda t, **kw: t['status'].update(status='Cancelled')
        self.run_cancel()
        self.api.cancel_order.assert_called_once_with(self.orders[0], timeout=5000)
        self.assertEqual(self.broker.store.audit.call_args.args[0], 'h3_cancel_confirmed')
        self.assertEqual(self.broker.store.audit.call_args.args[1]['account'], self.account_label)

    def test_waits_for_final_status(self):
        calls = 0
        def read():
            nonlocal calls
            calls += 1
            if calls == 3:
                self.orders[0]['status']['status'] = 'Cancelled'
            return self.orders
        self.api.list_trades.side_effect = read
        self.run_cancel()
        self.assertGreaterEqual(calls, 3)

    def test_fill_during_cancel_is_final(self):
        self.api.cancel_order.side_effect = lambda t, **kw: t['status'].update(status='Filled')
        self.run_cancel()

    def test_unknown_pending_and_expiry_never_cancel(self):
        for kind in ['strategy', 'broker', 'identity', 'pending_submit', 'expired']:
            with self.subTest(kind=kind):
                self.setUp()
                if kind == 'strategy': self.broker.store.pending.return_value = {'pending': True}
                if kind == 'broker': self.broker.guard['pending'] = {'trade_id': 'uncertain'}
                if kind == 'identity': self.orders[0]['order']['account'] = {}
                if kind == 'pending_submit': self.orders[0]['status']['status'] = 'PendingSubmit'
                if kind == 'expired': self.preflight.side_effect = TradingError('expired')
                with self.assertRaises(TradingError): self.run_cancel()
                self.api.cancel_order.assert_not_called()

    def test_timeout_failure_missing_and_new_order_stop(self):
        for kind in ['timeout', 'failure', 'missing', 'new']:
            with self.subTest(kind=kind):
                self.setUp()
                if kind == 'failure': self.api.cancel_order.side_effect = RuntimeError('provider private error')
                if kind == 'missing': self.api.cancel_order.side_effect = lambda *a, **k: self.orders.clear()
                if kind == 'new':
                    def replace(t, **kw):
                        t['status']['status'] = 'Cancelled'
                        self.orders.append(trade('new', account=self.account_id))
                    self.api.cancel_order.side_effect = replace
                with self.assertRaises(TradingError): self.run_cancel()

    def test_no_orders_no_cancels(self):
        self.orders.clear()
        self.run_cancel()
        self.api.cancel_order.assert_not_called()

    def test_expired_day_rod_is_ignored_for_cancellation_and_order_validation(self):
        self.orders[0]['order']['order_type'] = 'ROD'
        self.orders[0]['status']['order_datetime'] = datetime(2020, 11, 12, 5, 14, tzinfo=timezone.utc)
        self.run_cancel()
        with patch.object(shared, '_contract'):
            shared.validate_tmf_account(self.api, [], contract_code='TMFK6', ignore_expired_rod=True)
            with self.assertRaises(shared.BrokerOrderError):
                shared.validate_tmf_account(self.api, [], contract_code='TMFK6')
        self.api.cancel_order.assert_not_called()

    def test_day_rod_is_expired_in_same_dates_night_session(self):
        order = self.orders[0]
        order['order']['order_type'] = 'ROD'
        order['status']['order_datetime'] = datetime(2026, 11, 12, 5, 14, tzinfo=timezone.utc)
        now = datetime(2026, 11, 12, 23, 16, tzinfo=shared.TAIPEI)
        self.assertFalse(shared._is_working_tmf_trade(order, now))

    def test_night_rod_stays_working_until_next_morning_close(self):
        order = self.orders[0]
        order['order']['order_type'] = 'ROD'
        order['status']['order_datetime'] = datetime(2026, 11, 12, 15, 20)
        self.assertTrue(shared._is_working_tmf_trade(
            order, datetime(2026, 11, 13, 4, 59, tzinfo=shared.TAIPEI)))
        self.assertFalse(shared._is_working_tmf_trade(
            order, datetime(2026, 11, 13, 5, 1, tzinfo=shared.TAIPEI)))
        del order['status']['order_datetime']
        self.assertTrue(shared._is_working_tmf_trade(
            order, datetime(2026, 11, 13, 5, 1, tzinfo=shared.TAIPEI)))

    def test_startup_waits_with_working_orders_without_cancelling(self):
        self.api.list_positions = Mock(return_value=[])
        with patch('shioaji_tmf_target._contract'):
            self.assertEqual(PyramidBroker.startup_position(self.broker), 0)
            # Normal send-time validation still rejects the same working order.
            with self.assertRaises(shared.BrokerOrderError):
                shared.validate_tmf_account(self.api, [], contract_code='TMFK6')
        self.api.cancel_order.assert_not_called()

    def test_startup_still_rejects_other_month_inventory(self):
        self.api.list_positions = Mock(return_value=[dict(code='TMFL6', quantity=1, direction='Buy')])
        with patch('shioaji_tmf_target._contract'):
            with self.assertRaises(shared.BrokerOrderError):
                PyramidBroker.startup_position(self.broker)
        self.api.cancel_order.assert_not_called()


class AccountOneCancelTests(CancelTests):
    account_label = 'API_KEY'
    account_id = 'one'


class SignalCancelTests(unittest.TestCase):
    def setUp(self):
        signal_tests.SignalFlattenTests.setUp(self)
        wait = patch('mdd_runtime.wait_for_signal_history',
                     side_effect=lambda path, event, direction: signal_tests.H3History(signal_tests.MddSizing(), direction, None))
        self.addCleanup(wait.stop)
        wait.start()

    event = signal_tests.SignalFlattenTests.event
    policy = ProtectionEngine

    def test_cancel_before_inventory_then_adopt_fill_race(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root)/'state.sqlite')
            self.addCleanup(store.db.close)
            broker = Broker()
            engine = self.policy(store, broker, 'TMFK6')
            def cancel(key, preflight):
                preflight()
                broker.actual = -3  # Manual order partially filled before cancel.
            broker.cancel_working_orders = Mock(side_effect=cancel)
            engine.on_signal(self.event(100, 1))
            self.assertEqual(broker.orders, [('flatten', -3, 0), ('entry', 0, 2)])
            engine.on_signal(self.event(100, 1))
            broker.cancel_working_orders.assert_called_once()

    def test_cancellation_failure_never_submits(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root)/'state.sqlite')
            self.addCleanup(store.db.close)
            broker = Broker()
            engine = self.policy(store, broker, 'TMFK6')
            broker.cancel_working_orders = Mock(side_effect=TradingError('cancel unconfirmed'))
            with self.assertRaises(TradingError): engine.on_signal(self.event(100, 0))
            self.assertEqual(broker.orders, [])
            self.assertIsNone(store.pending())

    def test_expired_signal_does_not_cancel(self):
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root)/'state.sqlite')
            self.addCleanup(store.db.close)
            broker = Broker()
            engine = self.policy(store, broker, 'TMFK6')
            broker.cancel_working_orders = Mock()
            event = self.event(100, 1)
            event['received_at'] = (datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
            self.assertFalse(engine.on_signal(event))
            broker.cancel_working_orders.assert_not_called()

    def test_flat_signal_cancels_before_flatten(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root)/'state.sqlite')
            self.addCleanup(store.db.close)
            broker = Broker()
            broker.actual = 1
            engine = self.policy(store, broker, 'TMFK6')
            sequence = []
            broker.cancel_working_orders = lambda *args: sequence.append('cancel')
            send = broker.send_and_confirm
            def record(*args):
                sequence.append(args[1])
                return send(*args)
            broker.send_and_confirm = record
            engine.on_signal(self.event(100, 0))
            self.assertEqual(sequence, ['cancel', 'flatten'])


class AccountOneSignalCancelTests(SignalCancelTests):
    policy = MddEngine


if __name__ == '__main__':
    unittest.main()
