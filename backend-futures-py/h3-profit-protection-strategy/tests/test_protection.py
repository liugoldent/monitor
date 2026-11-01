"""Fake-broker execution only. No network or real order submission."""
from dataclasses import replace, asdict
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import protection_bootstrap
sys.path.insert(0, str(BASE.parent/'h3-follow-add-strategy/tests'))
from test_pyramid import API, event
from test_auto_rollover import AutoAPI
from h_core import Store, TradingError
from strategy import State
from runtime import LedgerError, RolloverPause
from protection_policy import Protection
from protection_runtime import ProtectionEngine
from protection_account import ProtectionBroker
from protection_rollover import ProtectionRollover
import protection_account as account
import protection_monitor as monitor


class LegacyLedgerPolicyTests(unittest.TestCase):
    def test_old_peak_and_lock_remain_readable(self):
        old = Protection(h3_direction=1, peak='1500', armed=True, locked=True)
        self.assertIs(old.validate(), old)

    def test_corrupt_legacy_ledger_still_fails(self):
        for old in (Protection(locked=True), Protection(peak='NaN'),
                    Protection(h3_direction=1, peak='500', armed=False)):
            with self.assertRaises(ValueError):
                old.validate()


class AccountTests(unittest.TestCase):
    def setUp(self):
        simulation = patch.object(account, 'BROKER_SIMULATION', True)
        simulation.start()
        self.addCleanup(simulation.stop)
        unit_env = patch.dict(os.environ, H_UNIT_KEY2='1')
        unit_env.start()
        self.addCleanup(unit_env.stop)
        self.temp = tempfile.TemporaryDirectory(dir=BASE/'tests')
        self.addCleanup(self.temp.cleanup)
        self.ca = Path(self.temp.name)/'test-ca.pfx'
        self.ca.write_text('fake')
        self.env = dict(API_KEY2='account-two-key', SECRET_KEY2='account-two-secret',
            PERSON_ID2='fake-person', CA_PATH2=str(self.ca), H3_PROTECTION_ACCOUNT_ID='account-two')
        self.api = Mock()
        self.api.futopt_account.account_id = 'account-two'
        self.sdk = NS(Shioaji=Mock(return_value=self.api))
        account.close()
        self.addCleanup(account.close)

    def test_only_key2_and_configured_account_can_activate_ca(self):
        with patch.dict(os.environ, self.env, clear=True), patch.dict(sys.modules, shioaji=self.sdk):
            result, _ = account.initialize()
        self.assertIs(result, self.api)
        self.sdk.Shioaji.assert_called_once_with(simulation=True)
        self.api.login.assert_called_once_with('account-two-key', 'account-two-secret')
        self.api.activate_ca.assert_called_once()

    def test_wrong_account_refused_before_ca_activation(self):
        self.api.futopt_account.account_id = 'another-account'
        with patch.dict(os.environ, self.env, clear=True), patch.dict(sys.modules, shioaji=self.sdk):
            with self.assertRaisesRegex(ValueError, '帳號'):
                account.initialize()
        self.api.activate_ca.assert_not_called()
        self.api.logout.assert_called_once()

    def test_missing_account_binding_refused(self):
        del self.env['H3_PROTECTION_ACCOUNT_ID']
        with patch.dict(os.environ, self.env, clear=True), patch.dict(sys.modules, shioaji=self.sdk):
            with self.assertRaisesRegex(ValueError, 'H3_PROTECTION_ACCOUNT_ID'):
                account.initialize()
        self.api.activate_ca.assert_not_called()

    def test_missing_key2_never_falls_back_to_primary_key(self):
        del self.env['API_KEY2']
        self.env['API_KEY'] = 'primary-must-never-be-used'
        with patch.dict(os.environ, self.env, clear=True), patch.dict(sys.modules, shioaji=self.sdk):
            with self.assertRaisesRegex(ValueError, 'API_KEY2'):
                account.initialize()
        self.sdk.Shioaji.assert_not_called()

    def test_check_config_never_logs_in_or_sends_notifications(self):
        with patch.object(monitor, 'load_env'), patch.object(monitor, 'check_config'), \
             patch.object(account, 'initialize') as login, patch.object(monitor, 'Notifications') as notices, \
             patch('builtins.print'):
            monitor.main(['--check-config'])
        login.assert_not_called()
        notices.assert_not_called()

    def test_simulation_cannot_be_overridden_by_environment(self):
        self.env['H3_PROTECTION_BROKER_SIMULATION'] = 'false'
        with patch.dict(os.environ, self.env, clear=True), patch.dict(sys.modules, shioaji=self.sdk):
            account.initialize()
        self.sdk.Shioaji.assert_called_once_with(simulation=True)
        runtime, records, state = monitor.mode_paths(True)
        self.assertIn('simulation', str(state))
        self.assertIn('simulation', str(records))

    def test_live_mode_uses_explicit_account_and_separate_storage(self):
        with patch.object(account, 'BROKER_SIMULATION', False), \
             patch.dict(os.environ, self.env, clear=True), patch.dict(sys.modules, shioaji=self.sdk):
            account.initialize()
            runtime, records, state = monitor.mode_paths(account.BROKER_SIMULATION)
        self.sdk.Shioaji.assert_called_once_with(simulation=False)
        self.api.login.assert_called_once_with('account-two-key', 'account-two-secret')
        self.assertEqual(state.name, 'api_key2.live.sqlite')
        self.assertEqual(runtime.name, 'live')
        self.assertEqual(records.name, 'live')
        for live, simulated in zip(monitor.mode_paths(False), monitor.mode_paths(True)):
            self.assertNotEqual(live, simulated)

    def test_live_notice_cannot_claim_simulation(self):
        engine = Mock()
        engine.state.return_value.expected = 0
        engine.broker.unit = 1
        with patch.object(account, 'BROKER_SIMULATION', False):
            notice = monitor.startup_notice(engine)
        self.assertIn('simulation=False', notice)
        self.assertIn('真實下單', notice)
        self.assertNotIn('模擬委託', notice)


class RuntimeTests(unittest.TestCase):
    api_class = API

    def setUp(self):
        unit_env = patch.dict(os.environ, H_UNIT_KEY2='1')
        unit_env.start()
        self.addCleanup(unit_env.stop)
        self.temp = tempfile.TemporaryDirectory(dir=BASE/'tests')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = Store(self.base/'state.sqlite')
        self.addCleanup(self.store.db.close)
        self.api = self.api_class()
        self.sj = NS(constant=NS(Action=NS(Buy='Buy', Sell='Sell'),
            FuturesPriceType=NS(MKT='MKT'), OrderType=NS(IOC='IOC'), FuturesOCType=NS(Auto='Auto')))
        self.watchdog = Mock()
        self.watchdog.status.return_value = (False, False)
        self.notify = Mock()
        quiet = patch('builtins.print')
        quiet.start()
        self.addCleanup(quiet.stop)
        self.restart()
        timer = patch.object(account.shared.time, 'sleep')
        timer.start()
        self.addCleanup(timer.stop)
        self.tick = datetime(2026, 10, 28, 10)

    def restart(self):
        self.broker = ProtectionBroker(self.api, self.sj, self.store, self.watchdog,
                                       records=self.base/'records')
        from rollover import ledger_symbol
        symbol = ledger_symbol(self.store, 'TMFJ6')
        self.broker.contract_code = symbol
        self.engine = ProtectionEngine(self.store, self.broker, symbol, self.notify)

    def enter(self, side=1, mid=1):
        self.engine.on_signal(event(side, mid))
        with self.store.db:
            self.engine.save_protection(replace(self.engine.protection(), entered_at=None))

    def price(self, pnl, side=1, **kwargs):
        self.tick += timedelta(minutes=1)
        self.api.price = 22000+pnl*side
        return self.engine.on_price(self.api.price, self.tick.isoformat(), self.tick.isoformat(), **kwargs)

    def test_profit_retracement_and_loss_never_exit_or_add(self):
        for side in (1, -1):
            if self.api.current:
                self.engine.on_signal(event(0, 10))
            self.enter(side, mid=11 if side == -1 else 1)
            count = len(self.api.orders)
            for pnl in (499, 500, 1500, 1050, 350, -800):
                self.assertFalse(self.price(pnl, side))
                self.assertEqual(self.api.current, side)
                self.assertEqual(len(self.api.orders), count)
            self.assertFalse(self.engine.protection().armed)

    def test_same_direction_restart_and_reverse(self):
        self.enter()
        self.price(1500)
        self.restart()
        self.engine.on_signal(event(1, 2))
        self.assertEqual(len(self.api.orders), 1)
        self.engine.on_signal(event(-1, 3))
        self.assertEqual(self.api.current, -1)
        self.assertEqual([o.quantity for o in self.api.orders], [1, 1, 1])
        self.assertEqual(self.engine.state().expected, -1)

    def test_h3_flat_then_new_entry(self):
        self.enter()
        self.price(500)
        self.price(350)
        self.engine.on_signal(event(0, 2))
        self.assertEqual(self.api.current, 0)
        self.engine.on_signal(event(1, 3))
        self.assertEqual(self.api.current, 1)
        self.assertEqual(len(self.api.orders), 3)

    def test_legacy_armed_position_cannot_trigger_price_exit(self):
        self.enter()
        with self.store.db:
            self.engine.save_protection(replace(self.engine.protection(), peak='1500', armed=True))
        self.restart()
        for pnl in (1050, 350, -1000):
            self.assertFalse(self.price(pnl))
        self.assertEqual(len(self.api.orders), 1)
        self.engine.on_signal(event(0, 2))
        self.assertEqual(self.api.current, 0)

    def test_legacy_profit_lock_does_not_block_new_h3(self):
        with self.store.db:
            self.engine.save_protection(Protection(h3_direction=1, peak='500', armed=True, locked=True))
        self.restart()
        self.assertEqual(len(self.api.orders), 0)  # no startup order
        self.engine.on_signal(event(1, 2))
        self.assertEqual(self.api.current, 1)
        self.assertFalse(self.engine.protection().locked)

    def test_flat_order_and_ledger_commit_atomically(self):
        self.enter()
        with patch.object(self.engine, 'save_protection', side_effect=RuntimeError('commit interrupted')):
            with self.assertRaises(RuntimeError):
                self.engine.on_signal(event(0, 2))
        self.assertEqual(self.api.current, 0)
        self.assertEqual(self.engine.state().expected, 1)
        self.assertEqual(self.store.pending()['reason'], 'H3')
        with self.assertRaises(TradingError):
            self.engine.on_signal(event(-1, 3))
        self.assertEqual(len(self.api.orders), 2)

    def test_unknown_flat_order_never_resends_or_opens_reverse(self):
        self.enter()
        self.api.unknown = True
        with self.assertRaises(Exception):
            self.engine.on_signal(event(-1, 2))
        self.assertTrue(self.store.pending())
        with self.assertRaises(TradingError):
            self.engine.on_signal(event(-1, 3))
        self.assertEqual(len(self.api.orders), 2)

    def test_inventory_mismatch_prevents_h3_order(self):
        self.enter()
        self.api.current = 2
        with self.assertRaises(TradingError):
            self.engine.on_signal(event(0, 2))
        self.assertEqual(len(self.api.orders), 1)

    def test_stale_duplicate_and_untrusted_signals_do_not_order(self):
        self.engine.on_signal(event(1, 1, age=31))
        bad = event(1, 2)
        bad['sender_username'] = 'untrusted'
        self.engine.on_signal(bad)
        self.assertEqual(len(self.api.orders), 0)
        notice = event(1, 3)
        self.engine.on_signal(notice)
        self.engine.on_signal(notice)
        self.assertEqual(len(self.api.orders), 1)

    def test_notifications_describe_h3_following(self):
        self.assertEqual(monitor.WEBHOOK_ENV, 'DISCORD_H3_TP_WEBHOOK_URL')
        self.enter()
        self.price(500)
        self.price(350)
        messages = [c.args[0] for c in self.notify.call_args_list]
        self.assertTrue(any('跟隨H3進場1口' in m for m in messages))
        self.assertFalse(any('保護已啟動' in m for m in messages))
        self.assertIn('尚未設定日K均線出場', monitor.startup_notice(self.engine))


class RolloverTests(RuntimeTests):
    api_class = AutoAPI

    def setUp(self):
        super().setUp()
        self.now = datetime(2026,10,21,13,25)

    def roll(self):
        result = ProtectionRollover(self.engine, self.base, clock=lambda:self.now)
        self.broker.rollover = result
        return result

    def test_roll_preserves_single_contract_and_does_not_price_exit(self):
        self.enter()
        self.api.price = 23000
        self.roll().synchronize()
        self.assertEqual(self.engine.state().first_price, '23000.0')
        self.assertEqual(self.api.current, 1)
        stamp = '2026-10-28T11:00:00'
        self.assertFalse(self.engine.on_price(22350, stamp, stamp))
        self.assertEqual(len(self.api.orders), 3)
        self.engine.on_signal(event(0, 2))
        self.assertEqual(self.api.current, 0)

    def test_crash_between_roll_legs_does_not_duplicate_close(self):
        self.enter()
        self.api.price = 23000
        roll = self.roll()
        original = roll.recover_leg
        def crash(plan):
            original(plan)
            raise SystemExit()
        with patch.object(roll, 'recover_leg', side_effect=crash):
            with self.assertRaises(SystemExit):
                roll.synchronize()
        self.assertEqual(self.engine.state().expected, 0)
        self.restart()
        self.roll().synchronize()
        self.assertEqual(self.engine.state().expected, 1)
        self.assertEqual(len(self.api.orders), 3)


class UnitConfigTests(unittest.TestCase):
    def test_unit_is_required_positive_integer_and_never_uses_other_accounts(self):
        for value in ('', '0', '-1', '1.5', 'true', 'NaN'):
            with patch.dict(os.environ, dict(H_UNIT_KEY2=value, H_UNIT='9'), clear=True):
                with self.assertRaises(ValueError):
                    account.position_unit()
        for value in ('1', '2', '5'):
            with patch.dict(os.environ, H_UNIT_KEY2=value):
                self.assertEqual(account.position_unit(), int(value))


class MultiUnitTests(unittest.TestCase):
    api_class = AutoAPI
    setUp = RuntimeTests.setUp
    restart = RuntimeTests.restart
    enter = RuntimeTests.enter
    price = RuntimeTests.price

    def test_two_and_three_lot_entry_reverse_flat_and_no_add(self):
        for unit in (2, 3):
            with patch.dict(os.environ, H_UNIT_KEY2=str(unit)):
                self.restart()
                start = len(self.api.orders)
                self.enter(1, mid=unit*10)
                self.assertEqual(self.api.current, unit)
                self.price(1500)
                self.price(350)
                self.engine.on_signal(event(1, unit*10+1))
                self.assertEqual(len(self.api.orders), start+1)
                self.restart()
                self.assertEqual(self.engine.state().expected, unit)
                self.engine.on_signal(event(-1, unit*10+2))
                self.assertEqual(self.api.current, -unit)
                self.engine.on_signal(event(0, unit*10+3))
                self.assertEqual(self.api.current, 0)
                self.assertEqual([o.quantity for o in self.api.orders[start:]], [unit]*4)

    def test_changed_unit_preserves_old_position_then_uses_new_unit(self):
        with patch.dict(os.environ, H_UNIT_KEY2='3'):
            self.restart()
            self.enter()
        with patch.dict(os.environ, H_UNIT_KEY2='5'):
            self.restart()
            self.engine.on_signal(event(1, 2))
            self.assertEqual(self.api.current, 3)
            self.engine.on_signal(event(-1, 3))
            self.assertEqual(self.api.current, -5)
            self.assertEqual([o.quantity for o in self.api.orders], [3, 3, 5])

    def test_multi_lot_partial_reverse_close_does_not_open_new_position(self):
        with patch.dict(os.environ, H_UNIT_KEY2='3'):
            self.restart()
            self.enter()
            self.api.partial = True
            with self.assertRaises(account.shared.BrokerOrderError):
                self.engine.on_signal(event(-1, 2))
            self.assertTrue(self.store.pending())
            self.assertEqual(len(self.api.orders), 2)
            self.assertEqual(self.api.current, 1)

    def test_rollover_keeps_old_quantity_even_after_config_change_and_restart(self):
        with patch.dict(os.environ, H_UNIT_KEY2='3'):
            self.restart()
            self.enter(-1)
        with patch.dict(os.environ, H_UNIT_KEY2='5'):
            self.restart()
            roll = ProtectionRollover(self.engine, self.base,
                clock=lambda: datetime(2026,10,21,13,25))
            self.broker.rollover = roll
            original = roll.recover_leg
            def crash(plan):
                original(plan)
                raise SystemExit()
            with patch.object(roll, 'recover_leg', side_effect=crash):
                with self.assertRaises(SystemExit):
                    roll.synchronize()
            self.restart()
            self.broker.rollover = ProtectionRollover(self.engine, self.base,
                clock=lambda: datetime(2026,10,21,13,25))
            self.broker.rollover.synchronize()
            self.assertEqual(self.api.current, -3)
            self.assertEqual(self.engine.state().expected, -3)
            self.assertEqual([o.quantity for o in self.api.orders], [3, 3, 3])


if __name__ == '__main__':
    unittest.main()
