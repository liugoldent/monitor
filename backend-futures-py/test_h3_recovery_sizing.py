import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / 'h3-capital-flatten-reentry-strategy'))
from h_core import Store, TradingError, OrderRejected
from h3_recovery_sizing import RecoveryPolicy, process_event
from h3_entry_watch import Curve

HASH = 'a' * 64


class Broker:
    def __init__(self):
        self.current = 0
        self.calls = []
        self.reject_entry = False

    def position(self):
        return self.current

    def send_and_confirm(self, delta, phase, target):
        self.calls.append((delta, phase, target))
        if self.reject_entry and phase == 'entry':
            raise OrderRejected('zero fill', {'zero': True})
        self.current = target
        return target


class RecoverySizingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'account.sqlite')
        self.addCleanup(self.store.db.close)
        self.policy = RecoveryPolicy(self.store, 'TEST', HASH)
        self.broker = Broker()
        self.counter = 0

    def step(self, dd, *, high=0, eligible=False, qualifies=False, side=None, is_new=True):
        self.counter += 1
        side = side or (-1 if self.counter % 2 else 1)
        key = f'1:{self.counter}'
        event = dict(event='received', route='h', sender_username='taiwan_mxf_bot',
                     received_at=datetime.now(timezone.utc).isoformat(), chat_id=1, message_id=self.counter,
                     text=f'浩克3V3 交易訊號通知 (B={int(side == 1)} S={int(side == -1)})')
        snap = dict(rule_version=2, source_sha256=HASH, event_key=key, is_new=is_new,
                    side=side, high_count=high, dd_points=str(dd), ratio=str(dd / 10000),
                    eligible=eligible, qualifies=qualifies)
        with patch('h3_recovery_sizing.validate_inputs', return_value=(Path('seed'),Path('price'),HASH)), \
             patch('h3_recovery_sizing.build_snapshot', return_value=snap):
            return process_event(dict(signal_source='events'), self.store, self.policy, self.broker, event)

    def test_full_cycle_first_wave_never_adds_later_wave_recovers_to_add_and_zero_reduces(self):
        self.assertEqual(self.step(300), 1)
        self.assertEqual(self.step(120, eligible=True, qualifies=True), 1)
        self.assertEqual(self.step(400), 1)
        self.assertEqual(self.step(100, eligible=True), 1)
        self.assertEqual(self.step(0, high=1, eligible=True), 1)
        self.assertEqual(self.step(300, high=1), 1)
        self.assertEqual(self.step(120, high=1, eligible=True, qualifies=True), 2)
        self.assertEqual(self.step(500, high=1), 2)
        self.assertEqual(self.step(0, high=2, eligible=True), 1)
        self.assertEqual(self.step(300, high=2), 1)
        self.assertEqual(self.step(120, high=2, eligible=True, qualifies=True), 2)
        self.assertTrue(all(abs(c[2]) <= 2 for c in self.broker.calls))
        self.assertFalse(self.store.pending())

    def test_restart_preserves_first_wave_lock_and_later_boost(self):
        self.step(120, eligible=True, qualifies=True)
        self.policy = RecoveryPolicy(self.store, 'TEST', HASH)
        self.assertEqual(self.step(100, eligible=True, qualifies=True), 1)
        self.step(0, high=1)
        self.step(120, high=1, eligible=True, qualifies=True)
        self.policy = RecoveryPolicy(self.store, 'TEST', HASH)
        self.assertTrue(self.policy.state()['boosted'])
        self.assertEqual(self.step(400, high=1), 2)

    def test_missed_zero_during_downtime_clears_previous_boost(self):
        self.step(120, eligible=True)
        self.step(0, high=1)
        self.step(120, high=1, eligible=True, qualifies=True)
        self.assertEqual(self.step(300, high=2), 1)

    def test_first_entry_at_zero_is_always_one_and_next_cycle_may_add(self):
        self.assertEqual(self.step(0, high=1, eligible=True, qualifies=True), 1)
        self.assertEqual(self.step(300, high=1), 1)
        self.assertEqual(self.step(120, high=1, eligible=True, qualifies=True), 2)

    def test_duplicate_never_enters_but_first_contract_ignores_recovery_window(self):
        self.assertEqual(self.step(120, eligible=True, qualifies=True, is_new=False), 0)
        self.assertFalse(self.broker.calls)
        self.assertEqual(self.step(200), 1)
        self.assertEqual(abs(self.broker.position()), 1)

    def test_first_signal_does_not_require_prior_drawdown_or_recovery(self):
        self.assertEqual(self.step(50, eligible=False, qualifies=False), 1)
        self.assertTrue(self.policy.state()['active'])

    def test_first_signal_on_recovery_still_opens_only_one(self):
        self.assertEqual(self.step(120, eligible=True, qualifies=True), 1)
        self.assertFalse(self.policy.state()['boosted'])

    def test_daily_restart_preserves_boost_and_waits_for_new_signal(self):
        self.step(0, high=1)
        self.step(300, high=1)
        self.step(120, high=1, eligible=True, qualifies=True)
        calls = list(self.broker.calls)
        self.policy = RecoveryPolicy(self.store, 'TEST', HASH)
        self.assertEqual(self.broker.calls, calls)
        self.assertEqual(abs(self.policy.state()['expected']), 2)
        self.assertEqual(self.step(120, high=1, is_new=False), 0)
        self.assertEqual(self.broker.calls, calls)
        self.assertEqual(self.step(200, high=1), 2)

    def test_first_signal_after_rejected_first_entry_still_opens_one_without_recovery(self):
        self.broker.reject_entry = True
        with self.assertRaises(OrderRejected):
            self.step(300)
        self.broker.reject_entry = False
        self.assertEqual(self.step(400), 1)

    def test_stale_or_untrusted_signal_never_opens_first_contract(self):
        from datetime import timedelta
        stale = dict(event='received', route='h', sender_username='taiwan_mxf_bot',
            received_at=(datetime.now(timezone.utc)-timedelta(seconds=31)).isoformat(),
            chat_id=1, message_id=99, text='浩克3V3 交易訊號通知 (B=1 S=0)')
        self.assertIsNone(process_event({}, self.store, self.policy, self.broker, stale))
        stale.update(received_at=datetime.now(timezone.utc).isoformat(), sender_username='untrusted')
        self.assertIsNone(process_event({}, self.store, self.policy, self.broker, stale))
        self.assertFalse(self.broker.calls)

    def test_first_trade_rejection_does_not_activate_or_retry(self):
        self.broker.reject_entry = True
        with self.assertRaises(OrderRejected):
            self.step(120, eligible=True, qualifies=True)
        self.assertFalse(self.policy.state()['active'])
        self.assertFalse(self.store.pending())
        self.assertEqual(len(self.broker.calls), 1)

    def test_rejected_reentry_preserves_confirmed_flat_state(self):
        self.step(120, eligible=True)
        self.broker.reject_entry = True
        with self.assertRaises(OrderRejected):
            self.step(100)
        self.assertEqual(self.policy.state()['expected'], 0)
        self.assertTrue(self.policy.state()['active'])
        self.assertFalse(self.store.pending())

    def test_crash_before_account_commit_keeps_guard(self):
        with patch.object(self.policy, 'confirmed', side_effect=TradingError('crash')):
            with self.assertRaises(TradingError):
                self.step(120, eligible=True)
        self.assertIsNotNone(self.store.pending())
        self.assertEqual(self.policy.state()['expected'], 0)
        self.assertEqual(self.broker.position(), -1)

    def test_old_inventory_never_gets_flattened_implicitly(self):
        self.broker.current = 1
        with self.assertRaises(TradingError):
            self.step(120, eligible=True)
        self.assertFalse(self.broker.calls)

    def test_changed_source_or_contract_is_blocked(self):
        for symbol, digest in [('OTHER', HASH), ('TEST', 'b' * 64)]:
            with self.assertRaises(TradingError):
                RecoveryPolicy(self.store, symbol, digest)

    def test_both_account_monitors_dispatch_to_the_same_recovery_watcher(self):
        from h_monitor import watch as capital_watch
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'h3-kgi-flatten-reentry-strategy'))
        from kgi_monitor import watch as kgi_watch
        config = dict(recovery_sizing=True)
        with patch('h3_recovery_sizing.watch', return_value='shared') as shared:
            self.assertEqual(capital_watch(config, self.store), 'shared')
            self.assertEqual(kgi_watch(config, self.store), 'shared')
            self.assertEqual(shared.call_count, 2)

    def test_recovery_mode_cannot_bypass_entry_gate_with_startup_buy(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'h3-kgi-flatten-reentry-strategy'))
        from kgi_monitor import main
        with patch('kgi_monitor.load_env'), patch('kgi_monitor.read_config', return_value=dict(recovery_sizing=True)):
            for option in ['--startup-simulation-buy-one','--startup-production-buy-one']:
                with self.assertRaisesRegex(TradingError, '避免繞過'):
                    main(['--check-config', option])

    def test_curve_alert_once_per_cycle_and_window_expires_on_deeper_dd(self):
        curve = Curve(dict(equity_points='0',peak_points='0',entry_price='10000',side=1,
            armed=False,phase='waiting_drawdown',closed_trades=0,high_count=0,
            recovered=False,entry_ready=False,qualifies=False))
        for dd, ready, alert in [(300,False,False),(120,True,True),(100,True,False),
                                 (200,False,False),(100,True,False),(0,True,False),
                                 (300,False,False),(120,True,True)]:
            curve.equity = -dd
            curve.check(10000)
            self.assertEqual((curve.entry_ready,curve.qualifies), (ready,alert))


if __name__ == '__main__':
    unittest.main()
