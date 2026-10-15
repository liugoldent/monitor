"""Short-exit-long integration tests: mocked broker and temporary state only."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import test_hedge as existing
from strategy import Calendar, POSITION_POLICY, STRATEGIES, signal_tracked_position
from entry_policy import VERSION
from backtest import run


class ShortExitLongMonitorTests(unittest.TestCase):
    setUp = existing.MonitorTests.setUp
    broker = existing.MonitorTests.broker
    monitor = existing.MonitorTests.monitor
    signal = existing.MonitorTests.signal

    def test_raw_short_exit_opens_long_from_zero_and_checkpoints_once(self):
        m = self.monitor()
        m.notify = Mock()
        self.now += timedelta(seconds=1)
        self.signal(-1, 0)
        m.tick()
        m.tick()
        state = json.loads(m.path.read_text())
        self.assertEqual(self.orders, [1])
        self.assertEqual(self.actual, 1)
        self.assertEqual(state['positions']['CFC07m'], 1)
        self.assertEqual(state['source']['previous_position'], 0)
        self.assertEqual(state['source']['signal_previous_position'], -1)
        self.assertEqual(state['source']['new_position'], 0)
        self.assertEqual(state['source']['tracked_position'], 1)
        self.assertTrue(state['attempt']['short_exit_to_long_signal'])
        self.assertEqual(state['short_veto_strategies'], ['CFC07m'])
        self.assertIn('轉多規則：原始-1→0', m.notify.call_args.args[0])

    def test_other_events_and_restart_retain_direction_until_next_own_event(self):
        m = self.monitor()
        self.signal(-1, 0)
        m.tick()
        self.now += timedelta(minutes=1)
        m = self.monitor()
        m.tick()  # Restart alone never resubmits or clears the mapped direction.
        self.signal(0, 0, 'CFCTX17m')
        m.tick()
        self.assertEqual(m.state['positions']['CFC07m'], 1)
        self.assertEqual(self.orders, [1])
        self.now += timedelta(seconds=1)
        self.signal(0, 1)
        m.tick()
        self.assertEqual(self.orders, [1])
        self.now += timedelta(seconds=1)
        self.signal(1, 0)
        m.tick()
        self.assertEqual(self.orders, [1, -1])
        self.assertEqual(m.state['positions']['CFC07m'], 0)
        self.assertEqual(m.state['short_veto_strategies'], [])

    def test_short_exit_is_bullish_veto_and_keeps_other_short_opposition(self):
        m = self.monitor()
        m.state['positions']['CFC07m'] = -1
        m.state['positions']['CFCTX17m'] = -1
        self.actual = -1
        self.now += timedelta(seconds=1)
        self.signal(-1, 0)
        m.tick()
        self.assertEqual(self.orders, [1])  # Close short; remaining -1 forbids long.
        self.assertEqual(self.actual, 0)
        self.assertEqual(m.state['positions']['CFC07m'], 1)
        self.assertEqual(m.state['long_veto_strategies'], ['CFCTX17m'])
        self.assertEqual(m.state['short_veto_strategies'], ['CFC07m'])
        self.now += timedelta(minutes=16)
        self.signal(-1, 0, 'CFCTX17m')
        m.tick()
        self.assertEqual(self.orders, [1, 1])
        self.assertEqual(self.actual, 1)

    def test_cutoff_updates_direction_without_opening(self):
        self.now = self.now.replace(hour=22)
        m = self.monitor()
        self.signal(-1, 0)
        m.tick()
        self.assertEqual(m.state['positions']['CFC07m'], 1)
        self.assertEqual(m.state['attempt']['raw_target_position'], 1)
        self.assertEqual(m.state['attempt']['target_position'], 0)
        self.assertEqual(self.orders, [])

    def test_cooldown_keeps_direction_and_requires_new_event_after_expiry(self):
        m = self.monitor()
        closed = self.now - timedelta(minutes=14)
        m.state['entry_policy']['last_exit_at'] = closed.isoformat()
        self.signal(-1, 0)
        m.tick()
        self.assertEqual(self.orders, [])
        self.assertEqual(m.state['positions']['CFC07m'], 1)
        self.now = closed + timedelta(minutes=15)
        m.tick()
        self.assertEqual(self.orders, [])
        self.signal(0, 0, 'CFCTX17m')
        m.tick()
        self.assertEqual(self.orders, [1])

    def test_v9_upgrade_preserves_state_and_ignores_old_short_exit(self):
        m = self.monitor()
        self.signal(-1, 0)
        m.state['schema_version'] = 9
        m.state.pop('position_policy')
        m.state['source'] = {'last_signal': self.now.isoformat() + '/0',
                             'signal_previous_position': -1, 'new_position': 0}
        exited = self.now - timedelta(minutes=20)
        m.state['entry_policy']['last_exit_at'] = exited.isoformat()
        m.state['entry_policy']['last_exit_basis'] = 'broker_fill'
        m.persist()
        old_policy = m.state['entry_policy'].copy()
        restarted = self.monitor()
        self.assertEqual(restarted.state['schema_version'], 12)
        self.assertEqual(restarted.state['position_policy'], POSITION_POLICY)
        self.assertEqual(restarted.state['entry_policy'], old_policy)
        self.assertTrue(all(p == 0 for p in restarted.state['positions'].values()))
        restarted.tick()
        self.execute.assert_not_called()
        self.assertEqual(self.orders, [])

    def test_0100_flat_and_daily_reset_clear_mapped_direction(self):
        m = self.monitor()
        self.signal(-1, 0)
        m.tick()
        self.now = datetime(2026, 9, 15, 1)
        m.tick()
        self.assertEqual(self.orders, [1, -1])
        self.now = datetime(2026, 9, 15, 5, 5)
        m.tick()
        self.assertTrue(all(p == 0 for p in m.state['positions'].values()))
        self.assertEqual(m.state['short_veto_strategies'], [])
        self.signal(-1, 0)
        m.tick()  # Overnight exits cannot create today's tracked direction.
        self.now = datetime(2026, 9, 15, 8, 45)
        m.tick()
        self.assertEqual(self.orders, [1, -1])
        self.signal(-1, 0)
        m.tick()
        self.assertEqual(self.orders, [1, -1, 1])

    def test_unit_is_applied_once_to_short_exit_direction(self):
        with patch.dict(os.environ, {'EF_MORNING_WEEKEND_HEDGE_UNIT': '2'}):
            m = self.monitor()
            self.signal(-1, 0)
            m.tick()
            self.now += timedelta(seconds=1)
            self.signal(-1, 0, 'CFCTX17m')
            m.tick()
            self.assertEqual(self.orders, [2])
            self.assertEqual(self.actual, 2)
            self.assertEqual(sum(m.state['positions'].values()), 2)


class ShortExitLongBacktestTests(unittest.TestCase):
    def test_backtest_maps_raw_exit_and_next_real_entry_does_not_add_units(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            signals, prices = root / 'signals.csv', root / 'prices.csv'
            signals.write_text('received_at,strategy_code,previous_position,new_position\n'
                '2026-09-14 09:00:00,CFC07m,-1,0\n'
                '2026-09-14 09:01:00,CFC07m,0,1\n'
                '2026-09-14 09:02:00,CFC07m,1,0\n')
            prices.write_text('Symbol,TradingView Time,Record Time,Open\n'
                'MXF1!,2026-09-14 09:01:00,2026-09-14 09:02:00,100\n'
                'MXF1!,2026-09-14 09:02:00,2026-09-14 09:03:00,105\n'
                'MXF1!,2026-09-14 09:03:00,2026-09-14 09:04:00,110\n')
            args = (signals, prices, Calendar.load(existing.BASE / 'config/calendar.json'),
                    datetime(2026, 9, 14, 8, 45), datetime(2026, 9, 14, 10))
            new = run(*args, unit=2)
            old = run(*args, unit=2, short_exit_to_long=False)
            self.assertEqual([r['target'] for r in new['ledger']], [2, 0])
            self.assertEqual(new['ledger'][0]['fill_time'], '2026-09-14T09:01:00')
            self.assertEqual(old['ledger'][0]['fill_time'], '2026-09-14T09:02:00')
            self.assertEqual(new['net_twd'], 120)
            self.assertEqual(old['net_twd'], 20)
            self.assertEqual(new['short_exit_policy'], POSITION_POLICY)
            self.assertEqual(new['entry_policy'], VERSION)

    def test_other_transitions_and_invalid_values(self):
        for old, new, expected in ((-1,0,1),(0,1,1),(1,0,0),(0,-1,-1),
                                   (1,-1,-1),(-1,1,1),(0,0,0)):
            self.assertEqual(signal_tracked_position(old,new), expected)
        for old,new in ((True,0),(-2,0),(-1,2)):
            with self.assertRaises(ValueError):signal_tracked_position(old,new)


if __name__ == '__main__':
    unittest.main()
