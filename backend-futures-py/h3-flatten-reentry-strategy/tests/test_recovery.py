import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import monitor_and_trade as monitor
import auto_trade
from recovery_broker import ShioajiRecoveryBroker
from h_core import Store, TradingError, OrderRejected
import h3_recovery_sizing as recovery

HASH = 'a' * 64


class API:
    def __init__(self):
        self.current = 0
        self.orders = []
        self.trades = []
        self.reject = self.partial = self.unknown = False
        self.futopt_account = object()
        self.Contracts = NS(Futures=NS(TMF=NS(TMFR1=NS(code='TMFJ6'))))

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
        trade = NS(contract=contract, order=NS(id=str(len(self.orders))), status=NS(
            status=status, deal_quantity=filled, deals=[dict(price=47000, quantity=filled,
                                                         ts=datetime.now().timestamp())] if filled else []))
        self.trades.append(trade)
        return trade


class RecoveryTests(unittest.TestCase):
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
        self.broker = ShioajiRecoveryBroker(self.api, self.sj, self.store, self.watchdog,
                                            records=self.base / 'records')
        self.policy = recovery.RecoveryPolicy(self.store, 'TMFJ6', HASH)
        self.count = 0

    def step(self, side=1, dd=0, high=1, qualifies=False):
        self.count += 1
        event = dict(event='received', route='h', sender_username='taiwan_mxf_bot',
                     chat_id=1, message_id=self.count, received_at=datetime.now(timezone.utc).isoformat(),
                     text=f'浩克3V3 交易訊號通知 (B={int(side == 1)} S={int(side == -1)})')
        snapshot = dict(rule_version=2, source_sha256=HASH, event_key=f'1:{self.count}',
                        is_new=True, side=side, high_count=high, dd_points=str(dd),
                        ratio='0.01', eligible=qualifies, qualifies=qualifies)
        with patch.object(recovery, 'validate_inputs', return_value=(Path('seed'), Path('price'), HASH)), \
             patch.object(recovery, 'build_snapshot', return_value=snapshot), \
             patch.object(self.broker.api, 'update_status'), \
             patch.object(auto_trade.shared.time, 'sleep'):
            return recovery.process_event(dict(signal_source='events'), self.store,
                                          self.policy, self.broker, event)

    def test_shared_policy_first_wave_no_add_then_recovery_add_and_zero_back_to_one(self):
        self.assertIs(monitor.watch, recovery.watch)
        self.assertEqual(self.step(1, dd=120, high=0, qualifies=True), 1)
        self.assertEqual(self.step(-1, dd=100, high=0, qualifies=True), 1)
        self.assertEqual(self.step(1, dd=0, high=1), 1)
        self.assertEqual(self.step(-1, dd=120, high=1, qualifies=True), 2)
        self.assertEqual(self.step(1, dd=0, high=2), 1)
        self.assertEqual([(o.action, o.quantity) for o in self.api.orders][-4:],
                         [('Sell', 1), ('Sell', 2), ('Buy', 2), ('Buy', 1)])
        self.assertEqual(self.policy.state()['expected'], 1)
        self.assertFalse(self.store.pending())
        self.assertTrue((self.base / 'records/entries.csv').exists())

    def test_zero_fill_rejection_preserves_position_then_next_fresh_signal_runs(self):
        self.step(1)
        self.api.reject = True
        with self.assertRaises(OrderRejected):
            self.step(-1)
        self.assertEqual(self.api.current, 1)
        self.assertEqual(len(self.api.orders), 2)  # Failed flatten never opens short.
        self.assertFalse(self.store.pending())
        self.assertFalse(self.broker.guard.get('pending'))
        self.api.reject = False
        self.assertIsNone(self.step(-1))  # Still within the shared 10-second duplicate window.
        self.store.db.execute('UPDATE signals SET received = received - 11')
        self.store.db.commit()
        self.step(-1)
        self.assertEqual(self.api.current, -1)

    def test_partial_fill_retains_both_guards_and_cannot_restart_or_open(self):
        self.step(1)
        self.step(-1, dd=120, qualifies=True)
        self.api.partial = True
        with self.assertRaises(auto_trade.shared.BrokerOrderError):
            self.step(1)
        self.assertTrue(self.store.pending())
        self.assertTrue(self.broker.guard.get('pending'))
        with self.assertRaises(TradingError):
            ShioajiRecoveryBroker(self.api, self.sj, self.store, self.watchdog)
        self.assertEqual(len(self.api.orders), 4)

    def test_unknown_outcome_stays_blocked(self):
        self.api.unknown = True
        with self.assertRaises(auto_trade.shared.BrokerOrderError):
            self.step()
        self.assertTrue(self.store.pending())
        self.assertEqual(len(self.api.orders), 1)

    def test_manual_inventory_change_is_never_adopted_or_flattened(self):
        self.api.current = 1
        with self.assertRaises(TradingError):
            self.step()
        self.assertEqual(self.api.orders, [])

    def test_untrusted_and_stale_events_cannot_order(self):
        event = dict(event='received', route='h', sender_username='other_bot', chat_id=1,
                     message_id=1, received_at=datetime.now(timezone.utc).isoformat(),
                     text='浩克3V3 訊號通知 多1口')
        for values in ({}, dict(sender_username='taiwan_mxf_bot', received_at='2020-01-01T00:00:00+08:00')):
            recovery.process_event({}, self.store, self.policy, self.broker, dict(event, **values))
        self.assertEqual(self.api.orders, [])

    def test_disconnect_does_not_order(self):
        self.watchdog.status.return_value = (True, False)
        with self.assertRaises(TradingError):
            self.step()
        self.assertEqual(self.api.orders, [])

    def test_restart_keeps_recovery_state_and_separate_api3_database(self):
        self.step(1)
        new_policy = recovery.RecoveryPolicy(self.store, 'TMFJ6', HASH)
        self.assertEqual(new_policy.state()['expected'], 1)
        self.assertTrue(new_policy.state()['active'])

    def test_legacy_pending_and_positions_are_preserved_and_block_migration(self):
        path = self.base / 'old.json'
        for old in ({'guard': {'pending': {'trade_id': 'old'}}}, {'current_entry': {'quantity': 1}}):
            text = json.dumps(old)
            path.write_text(text, encoding='utf-8')
            with patch.object(monitor, 'STATE', path), self.assertRaises(TradingError):
                monitor.check_legacy_state()
            self.assertEqual(path.read_text(encoding='utf-8'), text)

    def test_config_only_never_logs_in_and_rejects_placeholder_key_and_nonbase_unit(self):
        with patch.object(monitor, 'validate_inputs'), patch.object(monitor, 'check_legacy_state'), \
             patch.object(monitor, 'SOURCE', self.base / 'events'), \
             patch.object(auto_trade, 'initialize_broker_session') as login:
            monitor.SOURCE.touch()
            with patch.dict(os.environ, {'H_UNIT': '1', 'API_KEY3': 'bad', 'SECRET_KEY3': 'test'}):
                with self.assertRaisesRegex(TradingError, 'API_KEY3'):
                    monitor.read_config()
            with patch.dict(os.environ, {'H_UNIT': '2'}), self.assertRaises(TradingError):
                monitor.read_config()
            login.assert_not_called()

    def test_broker_login_uses_only_api_key3_and_secret3(self):
        ca = self.base / 'test.pfx'
        ca.touch()
        sdk = Mock()
        with patch.dict(sys.modules, {'shioaji': sdk}), \
             patch.object(auto_trade, '_api', None), patch.object(auto_trade, '_sj', None), \
             patch.dict(os.environ, {'API_KEY3': 'third-account-key', 'SECRET_KEY3': 'third-secret',
                         'API_KEY': 'first-key', 'SECRET_KEY': 'first-secret',
                         'CA_PATH3': str(ca), 'PERSON_ID3': 'test-person'}):
            auto_trade.initialize_broker_session()
        sdk.Shioaji.assert_called_once_with(simulation=False)
        sdk.Shioaji.return_value.login.assert_called_once_with('third-account-key', 'third-secret')

    def test_check_config_entrypoint_cannot_start_account_or_watch(self):
        (self.base / '.env').write_text('', encoding='utf-8')
        with patch.object(monitor, 'BACKEND', self.base), \
             patch.object(monitor, 'read_config', return_value={}), \
             patch.object(auto_trade, 'initialize_broker_session') as login, \
             patch.object(monitor, 'Store') as store, patch.object(monitor, 'watch') as watch, \
             patch('builtins.print'):
            monitor.main(['--check-config'])
        login.assert_not_called()
        store.assert_not_called()
        watch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
