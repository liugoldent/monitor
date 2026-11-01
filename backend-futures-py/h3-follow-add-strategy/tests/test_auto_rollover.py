"""Automatic monthly rollover tests use only a fake broker and fake clocks."""
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor_and_trade as monitor
import auto_trade
from auto_rollover import AutomaticRollover, SETTLEMENT_DATES
from broker import PyramidBroker
from h_core import Store
from rollover import ledger_symbol
from runtime import Engine, LedgerError, RolloverPause
from strategy import State
from test_rollover import RollAPI
from test_pyramid import event


class AutoAPI(RollAPI):
    def place_order(self, contract, order, **kwargs):
        if self.current and self.current_code != contract.code:
            raise AssertionError('Opening new month before old inventory is flat')
        if not self.current:
            self.current_code = contract.code
        return super().place_order(contract, order, **kwargs)


class AutomaticRolloverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = Store(self.base / 'state.sqlite')
        self.addCleanup(self.store.db.close)
        self.api = AutoAPI()
        self.sj = NS(constant=NS(Action=NS(Buy='Buy', Sell='Sell'),
            FuturesPriceType=NS(MKT='MKT'), OrderType=NS(IOC='IOC'), FuturesOCType=NS(Auto='Auto')))
        self.watchdog = Mock()
        self.watchdog.status.return_value = (False, False)
        self.now = datetime(2026, 10, 21, 13, 25)
        self.notify = Mock()
        self.broker = self.new_broker()
        self.engine = Engine(self.store, self.broker, 'TMFJ6', self.notify)
        self.broker.contract_code = 'TMFJ6'
        self.roll = self.new_roll(self.engine)
        for timer in [patch('builtins.print'), patch.object(auto_trade.shared.time, 'sleep')]:
            timer.start()
            self.addCleanup(timer.stop)

    def new_broker(self, allow_pending=False):
        return PyramidBroker(self.api, self.sj, self.store, self.watchdog,
                             records=self.base / 'records', allow_pending=allow_pending)

    def new_roll(self, engine):
        roll = AutomaticRollover(engine, self.base, clock=lambda: self.now)
        engine.broker.rollover = roll
        return roll

    def position(self, quantity):
        self.api.current = quantity
        with self.store.db:
            self.engine.save(State(expected=quantity, first_price='22000' if quantity else None,
                added=abs(quantity) == 2, round_key='initial', previous_guerrilla={'old': '1'}))

    def restart(self):
        self.broker = self.new_broker(allow_pending=True)
        self.broker.contract_code = ledger_symbol(self.store, 'TMFJ6')
        self.engine = Engine(self.store, self.broker, self.broker.contract_code, self.notify)
        self.roll = self.new_roll(self.engine)

    def test_1325_rolls_long_one_then_normal_reverse_and_flat_use_next_month(self):
        self.position(1)
        self.now = datetime(2026, 10, 21, 13, 24, 59)
        self.assertTrue(self.roll.synchronize())
        self.assertEqual(self.api.orders, [])
        self.now = datetime(2026, 10, 21, 13, 25)
        self.assertTrue(self.roll.synchronize())
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6'])
        self.assertEqual([(o.action, o.quantity) for o in self.api.orders], [('Sell', 1), ('Buy', 1)])
        self.assertEqual(self.engine.symbol, 'TMFK6')
        self.assertEqual(self.engine.state().expected, 1)
        self.assertEqual(self.engine.state().first_price, '22000.0')
        self.assertEqual(self.engine.state().previous_guerrilla, {})
        self.engine.on_signal(event(1, 1))  # Same direction maintains inventory.
        self.assertEqual(len(self.api.orders), 2)
        self.engine.on_signal(event(-1, 2))
        self.engine.on_signal(event(0, 3))
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6', 'TMFK6', 'TMFK6', 'TMFK6'])
        self.assertEqual(self.engine.state().expected, 0)
        self.assertFalse(self.store.pending())

    def test_short_two_rolls_same_direction_and_size_without_granting_another_add(self):
        self.position(-2)
        self.roll.synchronize()
        self.assertEqual([(o.action, o.quantity) for o in self.api.orders], [('Buy', 2), ('Sell', 2)])
        self.assertEqual(self.engine.state().expected, -2)
        self.assertTrue(self.engine.state().added)
        self.assertEqual(self.roll.plan()['stage'], 'done')

    def test_flat_switches_contract_without_orders_and_later_signal_enters_new_month(self):
        self.roll.synchronize()
        self.assertEqual(self.api.orders, [])
        self.assertEqual(self.engine.symbol, 'TMFK6')
        self.engine.on_signal(event(1))
        self.assertEqual(self.api.sent_codes, ['TMFK6'])

    def test_idempotent_after_restart_and_next_day_daily_restart(self):
        self.position(1)
        self.roll.synchronize()
        self.restart()
        self.roll.synchronize()
        self.now = datetime(2026, 10, 22, 9)
        self.restart()
        self.roll.synchronize()
        self.engine.validate_position()
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6'])

    def test_restart_after_confirmed_close_opens_only_next_leg(self):
        self.position(1)
        original = self.roll.recover_leg
        def stop_after_close(plan):
            original(plan)
            raise SystemExit('process stopped between legs')
        with patch.object(self.roll, 'recover_leg', side_effect=stop_after_close):
            with self.assertRaises(SystemExit):
                self.roll.synchronize()
        self.assertEqual(self.api.sent_codes, ['TMFJ6'])
        self.assertEqual(self.roll.plan()['stage'], 'open')
        self.assertEqual(self.engine.state().expected, 0)
        self.restart()
        self.roll.synchronize()
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6'])

    def test_filled_close_or_open_before_commit_is_reconciled_without_duplicate_order(self):
        for crash_phase in ('close', 'open'):
            with self.subTest(crash_phase=crash_phase):
                self.position(1)
                original = self.roll.recover_leg
                def fail_before_commit(plan):
                    if plan['stage'] == crash_phase:
                        raise SystemExit('filled but ledger not committed')
                    return original(plan)
                with patch.object(self.roll, 'recover_leg', side_effect=fail_before_commit):
                    with self.assertRaises(SystemExit):
                        self.roll.synchronize()
                order_count = len(self.api.orders)
                self.restart()
                self.roll.synchronize()
                self.assertEqual(len(self.api.orders), order_count + (crash_phase == 'close'))
                self.assertEqual(self.roll.plan()['stage'], 'done')
                if crash_phase == 'close':
                    # Reset the test's isolated ledger to an earlier month.
                    with self.store.db:
                        self.store.db.execute('UPDATE pyramid_state SET symbol=? WHERE id=1', ('TMFJ6',))
                    self.api.current_code = 'TMFJ6'
                    self.engine.symbol = self.broker.contract_code = 'TMFJ6'

    def test_filled_close_with_pending_broker_guard_recovers_then_opens_once(self):
        self.position(1)
        with patch.object(auto_trade.shared, '_verify_target_position', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                self.roll.synchronize()
        self.assertTrue(self.broker.guard.get('pending'))
        self.restart()
        self.roll.synchronize()
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6'])

    def test_unknown_submission_is_never_resent_or_followed_by_open(self):
        self.position(1)
        original = self.api.place_order
        def lost_response(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError('response lost')
        with patch.object(self.api, 'place_order', side_effect=lost_response):
            with self.assertRaises(TimeoutError):
                self.roll.synchronize()
        self.assertEqual(self.api.sent_codes, ['TMFJ6'])
        self.restart()
        with self.assertRaisesRegex(LedgerError, '不重送'):
            self.roll.synchronize()
        self.assertEqual(self.api.sent_codes, ['TMFJ6'])
        self.assertTrue(self.store.pending())

    def test_partial_or_rejected_close_never_opens_next_month(self):
        self.position(2)
        self.api.partial = True
        with self.assertRaises(auto_trade.shared.BrokerOrderError):
            self.roll.synchronize()
        self.restart()
        with self.assertRaises(LedgerError):
            self.roll.synchronize()
        self.assertEqual(self.api.sent_codes, ['TMFJ6'])
        self.assertTrue(self.store.pending())

    def test_partial_open_is_not_resent_on_restart(self):
        self.position(2)
        original = self.api.place_order
        def partial_open(contract, *args, **kwargs):
            self.api.partial = contract.code == 'TMFK6'
            return original(contract, *args, **kwargs)
        with patch.object(self.api, 'place_order', side_effect=partial_open):
            with self.assertRaises(auto_trade.shared.BrokerOrderError):
                self.roll.synchronize()
        self.restart()
        with self.assertRaises(LedgerError):
            self.roll.synchronize()
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6'])

    def test_missed_window_does_not_submit_expired_contract(self):
        self.position(1)
        self.now = datetime(2026, 10, 21, 13, 30)
        with self.assertRaisesRegex(LedgerError, '錯過'):
            self.roll.synchronize()
        self.assertEqual(self.api.orders, [])

    def test_existing_pending_order_other_month_or_inventory_mismatch_blocks_roll(self):
        self.position(1)
        self.api.current = -1
        with self.assertRaises(LedgerError):
            self.roll.synchronize()
        self.api.current = 1
        self.api.extra = [dict(code='TMFK6', direction='Buy', quantity=1)]
        with self.assertRaises(auto_trade.shared.BrokerOrderError):
            self.roll.synchronize()
        self.api.extra = []
        self.store.begin(dict(phase='entry'))
        with self.assertRaises(LedgerError):
            self.roll.synchronize()
        self.assertEqual(self.api.orders, [])

    def test_corrupt_plan_or_changed_ledger_cannot_change_order_size(self):
        self.position(1)
        plan = self.roll.start('TMFK6')
        with self.store.db:
            self.roll.save_plan(dict(plan, quantity=3))
        with self.assertRaisesRegex(LedgerError, '無效'):
            self.roll.synchronize()
        with self.store.db:
            self.roll.save_plan(plan)
            self.engine.save(State(expected=-1, first_price='22000'))
        with self.assertRaises(LedgerError):
            self.roll.synchronize()
        self.assertEqual(self.api.orders, [])

    def test_full_close_can_be_recorded_after_deadline_without_opening_late(self):
        self.position(1)
        with patch.object(self.roll, 'recover_leg', side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.roll.synchronize()
        self.now = datetime(2026, 10, 21, 13, 31)
        self.restart()
        with self.assertRaisesRegex(LedgerError, '錯過'):
            self.roll.synchronize()
        self.assertFalse(self.store.pending())
        self.assertEqual(self.roll.plan()['stage'], 'open')
        self.assertEqual(self.engine.state().expected, 0)
        self.assertEqual(self.api.sent_codes, ['TMFJ6'])

    def test_new_signal_at_cutoff_is_kept_until_rollover_completed(self):
        self.position(1)
        message = event(-1)
        with self.assertRaises(RolloverPause):
            self.engine.on_signal(message)
        self.roll.synchronize()
        self.assertTrue(self.engine.on_signal(message))
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6', 'TMFK6', 'TMFK6'])

    def test_notification_failure_does_not_interrupt_confirmed_roll(self):
        self.position(1)
        self.notify.side_effect = OSError('notification down')
        self.assertTrue(self.roll.synchronize())
        self.assertEqual(self.roll.plan()['stage'], 'done')
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6'])

    def test_schedule_2026_and_2027_and_changed_broker_date(self):
        self.assertEqual(len(SETTLEMENT_DATES), 15)
        self.assertEqual(SETTLEMENT_DATES['202709'], '2027-09-16')
        with self.store.db:
            self.store.db.execute("UPDATE h3_contract_metadata SET expiry='2026-10-22' WHERE code='TMFJ6'")
        with self.assertRaisesRegex(LedgerError, '日期'):
            self.roll.synchronize()
        self.assertEqual(self.api.orders, [])

    def test_all_supplied_dates_trigger_at_1325_including_year_boundary(self):
        for month, expiry in SETTLEMENT_DATES.items():
            with self.subTest(month=month):
                year, month_number = divmod(int(month), 100)
                next_month = f'{year + (month_number == 12):04d}{month_number % 12 + 1:02d}'
                old_code, next_code = 'TMF-' + month, 'TMF-' + next_month
                with self.store.db:
                    self.store.db.execute('DELETE FROM h3_contract_metadata')
                    self.store.db.execute('INSERT INTO h3_contract_metadata VALUES (?,?,?)', (old_code, month, expiry))
                    self.store.db.execute('INSERT INTO h3_contract_metadata VALUES (?,?,?)',
                        (next_code, next_month, SETTLEMENT_DATES.get(next_month, '2028-01-19')))
                self.engine.symbol = old_code
                self.now = datetime.fromisoformat(expiry + 'T13:24:59')
                self.assertIsNone(self.roll.due())
                self.now = datetime.fromisoformat(expiry + 'T13:25:00')
                self.assertEqual(self.roll.due(), next_code)

    def test_old_month_price_during_1325_window_cannot_trigger_add(self):
        self.position(1)
        self.roll.synchronize()
        source = self.base / 'events.jsonl'
        source.touch()
        prices = NS(latest={'Record Time': '2026-10-21 13:25:00',
                            'TradingView Time': '2026-10-21 13:25:00', 'Close': '23000'})
        bar = int(self.now.replace(tzinfo=monitor.ZoneInfo('Asia/Taipei')).timestamp() * 1000)
        with patch.object(monitor, 'SOURCE', source), patch.object(monitor, 'now_local', side_effect=lambda: self.now):
            monitor.poll_guerrilla(self.engine, 'near', {bar: {'high': -1300}}, prices, 0)
            monitor.poll_guerrilla(self.engine, 'near', {bar: {'high': -1200}}, prices, 0)
            self.assertEqual(len(self.api.orders), 2)
            self.assertEqual(self.engine.state().previous_guerrilla, {})
            self.now = datetime(2026, 10, 21, 13, 30)
            prices.latest.update({'Record Time': '2026-10-21 13:30:00', 'TradingView Time': '2026-10-21 13:30:00'})
            bar += 300000
            self.api.price = 23000
            monitor.poll_guerrilla(self.engine, 'near', {bar: {'high': -1300}}, prices, 0)
            monitor.poll_guerrilla(self.engine, 'near', {bar: {'high': -1200}}, prices, 0)
        self.assertEqual(self.api.sent_codes, ['TMFJ6', 'TMFK6', 'TMFK6'])


if __name__ == '__main__':
    unittest.main()
