"""Offline regressions for the three live H3 monitors' one-attempt retry."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
BACKEND = BASE.parent
sys.path[:0] = [str(BASE), str(BACKEND), str(BACKEND / 'h3-common'),
               str(BACKEND / 'h3-shane-use'), str(BACKEND / 'h3-profit-protection-strategy')]
from broker import PyramidBroker
from h3_signal_retry import handle_signal, signal_step
from h_core import Store, TradingError
from protection_account import ProtectionBroker
from shane_account import ShaneBroker
import shioaji_tmf_target as shared


class TokenError(Exception):
    pass


class SignalRetryTests(unittest.TestCase):
    def make_broker(self, broker_type, *, target=2, position=0):
        store = Store(':memory:')
        self.addCleanup(store.db.close)
        api = Mock()
        api.list_trades.return_value = []
        watchdog = Mock()
        watchdog.status.return_value = (False, False)
        broker = broker_type(api, object(), store, watchdog, records='.')
        broker.unit = 2
        broker.entry_quantity = 2
        broker.contract_code = 'TMFL6'
        broker.reconnect_for_signal = Mock()
        inv = SimpleNamespace(quantity=position)
        api.list_positions.side_effect = lambda account: ([] if not inv.quantity else [
            dict(code='TMFL6', quantity=abs(inv.quantity), direction='Buy' if inv.quantity > 0 else 'Sell')])
        phase = 'entry' if target else 'flatten'
        payload = dict(trigger='h3-dual:42', phase=phase, previous=position, target=target,
                       quantity=abs(target-position), side='buy' if target > position else 'sell')
        store.begin(payload)
        return broker, api, inv, payload

    def run_order(self, broker, payload):
        entrypoint = SimpleNamespace(
            broker=broker, on_signal=lambda event: broker.send_and_confirm(
                payload['target']-payload['previous'], payload['phase'], payload['target']))
        return handle_signal(entrypoint, dict(signal_key=payload['trigger']))

    def configure_adapter(self):
        for name, kwargs in (
            ('_contract', dict(return_value=object())),
            ('_build_order', dict(side_effect=lambda api, sj, side, quantity: (side, quantity))),
            ('POSITION_VERIFY_ATTEMPTS', dict(new=1)),
        ):
            mock = patch.object(shared, name, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        for name in ('append_entry', 'append_order'):
            mock = patch('broker.' + name)
            mock.start()
            self.addCleanup(mock.stop)

    @staticmethod
    def add_trade(api, trade_id, *, side='buy', quantity=2, filled=2, status='Filled'):
        trade = dict(order=dict(id=trade_id, action=side, quantity=quantity),
                     contract=dict(code='TMFL6'),
                     status=dict(status=status, deal_quantity=filled,
                                 deals=[dict(price=39000, quantity=filled, ts=1795074247)] if filled else []))
        api.list_trades.return_value.append(trade)
        return trade

    def test_token_error_before_submission_reuses_two_lot_plan_for_all_accounts(self):
        self.configure_adapter()
        for broker_type in (PyramidBroker, ProtectionBroker, ShaneBroker):
            with self.subTest(account=broker_type.__name__), patch('shane_account.position_unit', return_value=2):
                broker, api, inv, payload = self.make_broker(broker_type)
                api.update_status.side_effect = TokenError
                broker.reconnect_for_signal.side_effect = lambda: setattr(api.update_status, 'side_effect', None)

                def place(*args, **kwargs):
                    inv.quantity = 2
                    return self.add_trade(api, 'entry')

                api.place_order.side_effect = place
                self.assertEqual(self.run_order(broker, payload), 2)
                broker.reconnect_for_signal.assert_called_once()
                self.assertEqual([call.args[1] for call in api.place_order.call_args_list], [('buy', 2)])

    def test_zero_fill_retry_full_fill_confirmation_and_unknown_orders(self):
        self.configure_adapter()
        for result in ('zero', 'full', 'partial', 'unknown', 'changed'):
            with self.subTest(result=result):
                broker, api, inv, payload = self.make_broker(PyramidBroker)

                def place(*args, **kwargs):
                    if api.place_order.call_count == 2:
                        inv.quantity = 2
                        return self.add_trade(api, 'retry')
                    if result == 'unknown':
                        raise TokenError()
                    if result in ('zero', 'changed'):
                        return self.add_trade(api, 'original', filled=0, status='Cancelled')
                    inv.quantity = 2 if result == 'full' else 1
                    trade = self.add_trade(api, 'original', filled=inv.quantity,
                                           status='Filled' if result == 'full' else 'Cancelled')
                    if result == 'full':
                        api.update_status.side_effect = [TokenError()]
                        broker.reconnect_for_signal.side_effect = lambda: setattr(api.update_status, 'side_effect', None)
                    return trade

                api.place_order.side_effect = place
                if result == 'changed':
                    broker.reconnect_for_signal.side_effect = lambda: setattr(inv, 'quantity', 1)
                if result in ('zero', 'full'):
                    self.assertEqual(self.run_order(broker, payload), 2)
                else:
                    with self.assertRaises((TradingError, shared.BrokerOrderError)):
                        self.run_order(broker, payload)
                    self.assertIsNotNone(broker.guard.get('pending'))
                broker.reconnect_for_signal.assert_called_once()
                self.assertEqual([call.args[1] for call in api.place_order.call_args_list],
                                 [('buy', 2)] * (2 if result == 'zero' else 1))

    def test_retry_budget_is_shared_across_cancel_flatten_and_entry(self):
        self.configure_adapter()
        broker, api, _, payload = self.make_broker(PyramidBroker, target=0, position=-2)
        cancel = Mock(side_effect=[TokenError(), None])
        api.update_status.side_effect = TokenError

        def process(event):
            signal_step(broker, 'cancel_working_orders', cancel)
            return broker.send_and_confirm(2, 'flatten', 0)

        with self.assertRaises(TokenError):
            handle_signal(SimpleNamespace(broker=broker, on_signal=process),
                          dict(signal_key=payload['trigger']))
        self.assertEqual(cancel.call_count, 2)
        broker.reconnect_for_signal.assert_called_once()
        api.place_order.assert_not_called()
        self.assertIsNone(broker.store.pending())

    def test_flatten_retries_once_and_releases_guard_after_second_zero_fill(self):
        self.configure_adapter()
        broker, api, _, payload = self.make_broker(PyramidBroker, target=0, position=-2)
        api.place_order.side_effect = lambda *args, **kwargs: self.add_trade(
            api, f'close-{api.place_order.call_count}', filled=0, status='Cancelled')
        with self.assertRaises(shared.BrokerOrderError):
            self.run_order(broker, payload)
        broker.reconnect_for_signal.assert_called_once()
        self.assertEqual([call.args[1] for call in api.place_order.call_args_list],
                         [('buy', 2), ('buy', 2)])
        self.assertIsNone(broker.guard.get('pending'))
        self.assertIsNone(broker.store.pending())
