import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from strategy import Calendar, STRATEGIES, hedge_target, signal_position, snapshot_position, pure_position
from monitor_and_trade import Monitor, webhook_url, signal_message
import auto_trade
from backtest import run


class StrategyTests(unittest.TestCase):
    def test_signal_description_covers_entries_exits_and_reversals(self):
        for previous, new, action in ((0, 1, "多單進場"), (0, -1, "空單進場"),
                                      (1, 0, "多單出場"), (-1, 0, "空單出場"),
                                      (1, -1, "多單出場 → 空單進場"),
                                      (-1, 1, "空單出場 → 多單進場")):
            message = signal_message({"strategy_code": "CFC07m", "strategy_name": "測試策略",
                                      "signal_previous_position": previous,
                                      "previous_position": 0, "new_position": new})
            self.assertIn("測試策略 (CFC07m)", message)
            self.assertIn(action, message)

    def setUp(self):
        self.calendar = Calendar.load(BASE / "config/calendar.json")

    def test_signed_targets_and_no_added_exposure(self):
        for net in range(-12, 13):
            for cap in (0, 1, 2, 12):
                target = hedge_target(net, cap)
                self.assertLessEqual(abs(net + target), cap)
                self.assertLessEqual(abs(target), abs(net))
                self.assertLessEqual(net * target, 0)
        self.assertEqual(hedge_target(7, 2), -5)
        self.assertEqual(hedge_target(-7, 1), 6)

    def test_invalid_quantity(self):
        for value in (True, 1.5, "NaN"):
            with self.assertRaises(ValueError):
                hedge_target(value, 2)
        with self.assertRaises(ValueError):
            hedge_target(3, -1)

    def test_weekday_and_weekend(self):
        weekday = self.calendar.closure(date(2026, 9, 15))
        self.assertEqual(weekday.reopen, datetime(2026, 9, 15, 8, 45))
        self.assertEqual(weekday.cap, 2)
        weekend = self.calendar.closure(date(2026, 9, 12))
        self.assertEqual(weekend.reopen, datetime(2026, 9, 14, 8, 45))
        self.assertEqual(weekend.cap, 1)
        self.assertIsNone(self.calendar.closure(date(2026, 9, 13)))

    def test_long_holidays_and_night_session(self):
        closure = self.calendar.closure(date(2026, 9, 25))
        self.assertEqual(closure.reopen, datetime(2026, 9, 29, 8, 45))
        self.assertEqual(closure.cap, 1)
        self.assertTrue(self.calendar.is_open(datetime(2026, 9, 25, 1, 0)))
        self.assertFalse(self.calendar.is_open(datetime(2026, 9, 25, 8, 45)))
        self.assertIsNone(self.calendar.closure(date(2026, 9, 26)))
        self.assertEqual(self.calendar.closure(date(2026, 2, 12)).reopen,
                         datetime(2026, 2, 23, 8, 45))

    def test_closed_and_open_boundaries(self):
        for stamp in ("2026-09-15T05:00", "2026-09-15T08:44", "2026-09-15T13:45"):
            self.assertFalse(self.calendar.is_open(datetime.fromisoformat(stamp)))
        for stamp in ("2026-09-15T08:45", "2026-09-15T13:44", "2026-09-15T15:00"):
            self.assertTrue(self.calendar.is_open(datetime.fromisoformat(stamp)))
        with self.assertRaises(ValueError):
            self.calendar.closure(date(2027, 1, 1))

    def test_special_calendar_overrides(self):
        calendar = Calendar({"valid_from": "2026-01-01", "valid_through": "2026-12-31",
                             "closed_dates": [], "open_dates": ["2026-09-12"],
                             "no_night_dates": ["2026-09-11"]})
        self.assertTrue(calendar.trading_day(date(2026, 9, 12)))
        self.assertIsNone(calendar.closure(date(2026, 9, 12)))


class SourceTests(unittest.TestCase):
    def test_only_new_individual_legs_and_no_old_restoration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signals.csv"
            path.write_text("received_at,strategy_code,new_position\n"
                "2026-09-14 23:00:00,CFC07m,1\n"
                "2026-09-15 05:30:00,CFCTX17m,1\n"
                "2026-09-15 08:45:00,CFCTX18m,-1\n"
                "2026-09-15 09:00:00,CFC07m,0\n")
            calendar = Calendar.load(BASE / "config/calendar.json")
            since = datetime(2026, 9, 15, 8, 45)
            self.assertEqual(pure_position(path, since, since, calendar)["net_position"], -1)
            result = pure_position(path, datetime(2026, 9, 15, 9), since, calendar)
            self.assertEqual(result["net_position"], -1)
            self.assertEqual(result["positions"]["CFCTX17m"], 0)


class MonitorTests(unittest.TestCase):
    def test_schedule_upgrade_keeps_current_session_positions(self):
        m = self.monitor()
        m.state["last_reset_cycle"] = "2026-09-12T04:59:00"
        m.state["positions"]["CFC07m"] = 1
        m.persist()
        restarted = self.monitor()
        restarted.flat_checker = Mock(side_effect=AssertionError("must not reset"))
        restarted.tick()
        self.assertEqual(restarted.state["positions"]["CFC07m"], 1)
        self.assertEqual(restarted.state["last_reset_cycle"], "2026-09-12T01:00:00")
        self.execute.assert_not_called()

    def test_position_limit_both_directions_and_flat_bypasses_limit(self):
        m = self.monitor()
        notices = []
        m.notify = notices.append
        codes = list(STRATEGIES)
        for direction in (1, -1):
            m.state["positions"] = dict.fromkeys(STRATEGIES, 0)
            for code in codes[:1]:
                m.state["positions"][code] = direction
            self.actual = 1 * direction
            self.orders.clear()
            self.now += timedelta(seconds=1)
            self.signal(0, direction, codes[1])
            m.tick()
            self.assertEqual(self.orders, [direction])
            self.assertEqual(self.actual, 2 * direction)
            self.now += timedelta(seconds=1)
            self.signal(0, direction, codes[2])
            m.tick()
            m.tick()
            self.assertEqual(self.orders, [direction])
            self.assertEqual(m.state["positions"][codes[2]], 0)
            self.assertIn("超過2口上限", notices[-1])
            self.now += timedelta(seconds=1)
            self.signal(direction, 0, codes[2])
            m.tick()
            self.assertEqual(self.orders, [direction])
            self.now += timedelta(seconds=1)
            self.signal(direction, 0, codes[1])
            m.tick()
            self.assertEqual(self.orders, [direction, -direction])
        self.actual = 8
        self.now = datetime(2026, 9, 15, 1, 0)
        m.tick()
        self.assertEqual(self.orders[-1], -8)
        self.assertEqual(self.actual, 0)
        self.assertIn("01:00清倉", notices[-1])

    def test_legacy_cleanup_preserves_positions_cursor_and_attempt(self):
        m = self.monitor()
        m.state["positions"]["CFCTX21m"] = 1
        m.state["day_signal_positions"] = dict.fromkeys(STRATEGIES, -1)
        m.state["source"] = {"positions": dict.fromkeys(STRATEGIES, 0),
                             "net_position": 0, "last_signal": "2026-09-14T22:00:00/0"}
        m.state["attempt"] = {"status": "operator_unblocked", "key": "old"}
        # Write a legacy-shaped fixture without invoking the new cleanup.
        m.path.write_text(json.dumps(m.state), encoding="utf-8")
        restarted = self.monitor()
        state = json.loads(restarted.path.read_text(encoding="utf-8"))
        self.assertEqual(state["positions"]["CFCTX21m"], 1)
        self.assertNotIn("day_signal_positions", state)
        self.assertEqual(state["source"], {"last_signal": "2026-09-14T22:00:00/0"})
        self.assertEqual(state["attempt"], m.state["attempt"])
        self.execute.assert_not_called()

    def test_legacy_migration_copies_positions_before_removing_snapshot(self):
        m = self.monitor()
        del m.state["positions"]
        m.state["day_signal_positions"] = dict.fromkeys(STRATEGIES, 0)
        m.state["day_signal_positions"]["CFCTX22m"] = 1
        m.path.write_text(json.dumps(m.state), encoding="utf-8")
        restarted = self.monitor()
        self.assertEqual(restarted.state["positions"]["CFCTX22m"], 1)
        self.assertNotIn("day_signal_positions", restarted.state)

    def test_new_signal_persists_checkpoint_without_position_snapshot(self):
        m = self.monitor()
        self.now += timedelta(seconds=1)
        self.signal(0, 1)
        m.tick()
        m.tick()
        state = json.loads(m.path.read_text(encoding="utf-8"))
        self.assertEqual(self.orders, [1])
        self.assertEqual(state["positions"]["CFC07m"], 1)
        self.assertIn("last_signal", state["source"])
        self.assertNotIn("positions", state["source"])
        self.assertNotIn("net_position", state["source"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 9, 14, 22, 30)
        self.signals = self.root / "signals.csv"
        self.signals.write_text("received_at,strategy_code,previous_position,new_position\n")
        env = patch.dict(os.environ, {"EF_HEDGE_SIGNAL_CSV": str(self.signals)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.actual = 1
        self.orders = []
        self.fail_after_fill = False
        self.execute = Mock(side_effect=self.broker)

    def broker(self, target, *, delta=None, on_target=None, **kwargs):
        previous = self.actual
        if delta is not None:
            target = previous + delta
        state = json.loads((self.root / "runtime/live_state.json").read_text())
        self.assertEqual(state["attempt"]["status"], "attempted")
        quantity = abs(target - previous)
        if quantity:
            self.orders.append(target - previous)
        self.actual = target
        if self.fail_after_fill:
            self.fail_after_fill = False
            raise auto_trade.BrokerOrderError("filled but response lost")
        return NS(quantity=quantity, submitted=bool(quantity),
                  side="buy" if target > previous else "sell")

    def monitor(self):
        return Monitor(root=self.root, live=True, executor=self.execute, clock=lambda: self.now,
                       flat_checker=lambda: self.actual == 0)

    def signal(self, previous, new, code="CFC07m", stamp=None):
        with self.signals.open("a") as f:
            f.write(f"{stamp or self.now.strftime('%Y-%m-%d %H:%M:%S')},{code},{previous},{new}\n")

    def test_start_and_restart_never_trade_old_targets_or_signals(self):
        self.signal(0, 1, stamp="2026-09-14 22:15:00")
        m = self.monitor()
        m.state.update(target=4, position=4, attempt={"status": "failed", "target": 4, "at": self.now.isoformat()})
        m.persist()
        self.now += timedelta(seconds=1)
        m = self.monitor()
        m.tick()
        self.now += timedelta(minutes=1)
        m.tick()
        self.execute.assert_not_called()
        self.assertEqual(self.actual, 1)

    def test_order_notification_identifies_source_entry_and_exit(self):
        m = self.monitor()
        m.notify = Mock()
        for previous, new, action in ((0, 1, "多單進場"), (1, 0, "多單出場")):
            self.signal(previous, new)
            m.tick()
            message = m.notify.call_args.args[0]
            self.assertIn("CFC07m", message)
            self.assertIn(action, message)
            self.assertIn("已送出", message)
        self.assertEqual(self.orders, [1, -1])

    def test_new_signal_in_startup_second_is_not_lost(self):
        self.now = self.now.replace(microsecond=100000)
        self.signal(0, 1, "CFCTX16m")  # Already in file at startup: ignore.
        m = self.monitor()
        self.now = self.now.replace(microsecond=200000)
        self.signal(0, 1)
        m.tick()
        self.assertEqual(self.orders, [1])

    def test_new_entry_adds_one_to_actual_not_old_theoretical_four(self):
        m = self.monitor()
        m.state["target"] = 4
        self.now += timedelta(seconds=1)
        self.signal(0, 1)
        m.tick()
        self.assertEqual(self.orders, [1])
        self.assertEqual(self.actual, 2)

    def test_six_transitions_and_batch_not_collapsed(self):
        m = self.monitor()
        for previous, new in ((0, 1), (1, 0), (0, -1), (-1, 0), (0, 1), (1, -1), (-1, 1)):
            self.now += timedelta(seconds=1)
            self.signal(previous, new)
        m.tick()
        self.assertEqual(self.orders, [1, -1, -1, 1, 1, -2, 2])
        m.tick()
        self.assertEqual(len(self.orders), 7)

    def test_first_daily_reverse_opens_only_one_contract(self):
        m = self.monitor()
        self.now += timedelta(seconds=1)
        self.signal(1, -1)
        m.tick()
        self.assertEqual(self.orders, [-1])

    def test_restart_at_1603_keeps_morning_entry_context(self):
        self.now = datetime(2026, 9, 15, 8, 45)
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 9)
        self.signal(0, 1)
        m.tick()
        self.now = datetime(2026, 9, 15, 16, 3)
        m = self.monitor()
        m.tick()
        self.assertEqual(self.orders, [1])

        self.now += timedelta(seconds=1)
        self.signal(1, -1)
        m.tick()
        self.assertEqual(self.orders, [1, -2])
        self.assertEqual(m.state["trading_day"], "2026-09-15")

    def test_blocked_signals_notify_once_without_orders_or_position_changes(self):
        m = self.monitor()
        m.notify = Mock()
        m.state["blocked_reason"] = "interrupted"
        m.state["attempt"] = {"status": "attempted", "key": "old"}
        m.state["positions"]["CFC07m"] = 1
        m.state["positions"]["CFCTX16m"] = 1
        before = m.state["positions"].copy()
        self.now += timedelta(seconds=1)
        self.signal(1, 0)
        self.signal(1, 0, "CFCTX16m")
        m.tick()
        m.tick()
        messages = [call.args[0] for call in m.notify.call_args_list]
        self.assertEqual(sum("收到EF訊號・暫停下單" in msg for msg in messages), 2)
        self.assertEqual(m.state["positions"], before)
        self.assertEqual(m.state["attempt"], {"status": "attempted", "key": "old"})
        self.execute.assert_not_called()
        del m.state["blocked_reason"]
        m.tick()
        self.execute.assert_not_called()  # Unblocking must not replay skipped exits.
        self.now += timedelta(seconds=1)
        self.signal(1, 0)
        m.tick()
        self.assertEqual(self.orders, [-1])

    def test_zero_delta_signal_notifies_once(self):
        m = self.monitor()
        m.notify = Mock()
        self.now += timedelta(seconds=1)
        self.signal(1, 0)
        m.tick()
        m.tick()
        m.notify.assert_called_once()
        self.assertIn("無需下單", m.notify.call_args.args[0])
        self.execute.assert_not_called()

    def test_failed_reset_at_reopen_allows_new_signal(self):
        m = self.monitor()
        m.notify = Mock()
        self.now = datetime(2026, 9, 15, 9)
        self.signal(0, 1)
        m.tick()
        m.tick()
        messages = [call.args[0] for call in m.notify.call_args_list]
        self.assertTrue(any("人工清倉待辦" in msg for msg in messages))
        self.assertEqual(self.orders, [1])
        self.assertEqual(m.state["reset_status"], "manual_flat_required")

    def test_restart_first_exit_without_daily_entry_does_not_sell(self):
        self.now = datetime(2026, 9, 15, 9)
        self.monitor()
        self.now = datetime(2026, 9, 15, 16, 3)
        m = self.monitor()
        self.signal(1, 0)
        m.tick()
        self.assertEqual(self.orders, [])

    def test_midnight_restart_keeps_previous_day_and_next_open_resets(self):
        self.now = datetime(2026, 9, 15, 15)
        m = self.monitor()
        self.signal(0, 1)
        m.tick()
        self.now = datetime(2026, 9, 16, 0, 3)
        m = self.monitor()
        self.signal(1, -1)
        m.tick()
        self.assertEqual(self.orders, [1, -2])
        self.assertEqual(m.state["trading_day"], "2026-09-15")
        self.now = datetime(2026, 9, 16, 8, 45)
        self.actual = 0  # Broker confirms the prior session was flattened.
        m = self.monitor()
        self.signal(-1, 0)
        m.tick()
        self.assertEqual(self.orders, [1, -2])
        self.assertEqual(m.state["trading_day"], "2026-09-16")

    def test_restart_after_submission_before_source_checkpoint_no_duplicate(self):
        m = self.monitor()
        self.now += timedelta(seconds=1)
        self.signal(0, 1)
        m.tick()
        m.state.pop("source")
        m.persist()
        attempt = m.state["attempt"].copy()
        m = self.monitor()
        m.tick()
        self.assertEqual(m.state["attempt"], attempt)
        self.assertEqual(self.orders, [1])

    def test_json_manual_edit_is_authoritative_after_restart(self):
        m = self.monitor()
        m.state["positions"]["CFC07m"] = 1
        m.persist()
        self.now += timedelta(seconds=1)
        m = self.monitor()
        self.signal(1, 0)
        m.tick()
        self.assertEqual(self.orders, [-1])
        self.assertEqual(m.state["positions"]["CFC07m"], 0)

    def test_0505_reset_and_restart_do_not_reset_twice(self):
        m = self.monitor()
        m.state["positions"]["CFC07m"] = 1
        m.persist()
        self.now = datetime(2026, 9, 15, 1, 0)
        m.tick()
        self.assertEqual(m.state["positions"]["CFC07m"], 1)
        self.now = datetime(2026, 9, 15, 5, 4, 59)
        m.tick()
        self.assertEqual(m.state["positions"]["CFC07m"], 1)
        self.now += timedelta(seconds=1)
        m.tick()
        self.assertTrue(all(v == 0 for v in m.state["positions"].values()))
        reset_at = m.state["reset_at"]
        m = self.monitor()
        m.flat_checker = Mock(side_effect=AssertionError("already reset"))
        m.tick()
        self.assertEqual(m.state["reset_at"], reset_at)

    def test_missed_reset_warns_and_does_not_reset_again_after_new_entry(self):
        m = self.monitor()
        m.state["positions"]["CFC07m"] = 1
        m.persist()
        self.now = datetime(2026, 9, 15, 9)
        m = self.monitor()
        self.signal(0, 1, "CFCTX16m")
        m.tick()
        self.assertEqual(m.state["reset_status"], "manual_flat_required")
        self.assertEqual(m.state["positions"]["CFC07m"], 0)
        self.assertEqual(self.orders, [1])
        self.actual = 0
        self.now += timedelta(minutes=1)
        m.tick()
        self.assertEqual(m.state["reset_status"], "manual_flat_required")
        self.assertEqual(m.state["positions"]["CFCTX16m"], 1)
        self.assertEqual(self.orders, [1])

    def test_reset_query_error_keeps_positions_and_blocks(self):
        m = self.monitor()
        m.state["positions"]["CFC07m"] = 1
        self.now = datetime(2026, 9, 15, 5, 5)
        m.flat_checker = Mock(side_effect=TimeoutError())
        m.tick()
        self.assertEqual(m.state["positions"]["CFC07m"], 1)
        self.assertEqual(m.state["reset_status"], "blocked")

    def test_failed_flat_alert_and_next_open_trade_without_retry(self):
        m = self.monitor()
        m.notify = Mock()
        m.state["positions"]["CFC07m"] = 1
        self.now = datetime(2026, 9, 15, 1, 0)
        self.execute.side_effect = TimeoutError("unknown")
        m.tick()
        self.assertTrue(any("手動清倉" in c.args[0] for c in m.notify.call_args_list))
        self.now = datetime(2026, 9, 15, 5, 5)
        m.tick()
        self.assertEqual(self.execute.call_count, 1)
        self.now = datetime(2026, 9, 15, 8, 45)
        self.execute.side_effect = self.broker
        self.signal(0, 1, "CFCTX16m")
        m.tick()
        m.tick()
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual(self.orders, [1])
        self.assertEqual(m.state["positions"]["CFC07m"], 0)

    def test_interrupted_flat_restart_does_not_lock_next_open(self):
        m = self.monitor()
        m.state["attempt"] = {"key": "2026-09-15T01:00:00/flat", "status": "attempted"}
        m.persist()
        self.now = datetime(2026, 9, 15, 8, 44)
        m = self.monitor()
        m.tick()
        self.now = datetime(2026, 9, 15, 8, 45)
        self.signal(0, 1)
        m.tick()
        self.assertNotIn("blocked_reason", m.state)
        self.assertEqual(self.orders, [1])

    def test_interrupted_submission_blocks_restart(self):
        m = self.monitor()
        m.state["attempt"] = {"status": "attempted", "key": "uncertain"}
        m.persist()
        m = self.monitor()
        self.signal(0, 1)
        m.tick()
        self.assertIn("blocked_reason", m.state)
        self.assertEqual(self.orders, [])

    def test_invalid_manual_position_rejected(self):
        m = self.monitor()
        m.state["positions"]["CFC07m"] = 2
        m.persist()
        with self.assertRaises(ValueError):
            self.monitor()

    def test_multiple_same_second_signals_each_execute(self):
        m = self.monitor()
        self.now += timedelta(seconds=1)
        for code in ("CFC07m", "CFCTX16m", "CFCTX21m"):
            self.signal(0, 1, code)
        m.tick()
        self.assertEqual(self.orders, [1, 1, 1])

    def test_restart_ignores_signals_received_while_stopped(self):
        self.monitor()
        self.now += timedelta(seconds=1)
        self.signal(0, 1)
        self.now += timedelta(seconds=1)
        m = self.monitor()
        m.tick()
        self.execute.assert_not_called()
        self.now += timedelta(seconds=1)
        self.signal(0, 1, "CFCTX16m")
        m.tick()
        self.assertEqual(self.orders, [1])

    def test_failure_is_not_retried_and_next_signal_still_submits(self):
        m = self.monitor()
        self.now += timedelta(seconds=1)
        self.signal(0, 1)
        self.fail_after_fill = True
        m.tick()
        self.now += timedelta(seconds=2)
        self.signal(0, 1, "CFCTX16m")
        m.tick()
        self.assertEqual(self.orders, [1, 1])
        self.now += timedelta(seconds=8)
        m.tick()
        self.assertEqual(self.orders, [1, 1])
        self.assertEqual(self.actual, 3)
        self.assertEqual(self.execute.call_count, 2)

    def test_flat_on_clock_then_reopen_only_new_signals(self):
        m = self.monitor()
        self.actual = 4
        self.now = datetime(2026, 9, 15, 1, 0)
        with patch.object(m, "source", side_effect=AssertionError("flat cannot read CSV")):
            m.tick()
        self.assertEqual(self.orders, [-4])
        for hour, minute in ((1, 0), (2, 0), (4, 59), (5, 0), (8, 44)):
            self.now = datetime(2026, 9, 15, hour, minute, 10)
            self.signal(0, 1, "CFCTX18m")
            m.tick()
            self.assertEqual(self.orders, [-4])
        self.now = datetime(2026, 9, 15, 8, 45)
        m.tick()
        self.assertEqual(self.orders, [-4])
        # Overnight leg already flattened: exit must not open a short.
        self.signal(1, 0)
        m.tick()
        self.assertEqual(self.orders, [-4])
        self.now += timedelta(seconds=1)
        self.signal(0, 1, "CFCTX16m")
        m.tick()
        self.assertEqual(self.orders, [-4, 1])

    def test_start_at_flat_time_liquidates(self):
        self.now = datetime(2026, 9, 15, 1, 0)
        self.monitor().tick()
        self.assertEqual(self.orders, [-1])

    def test_flat_overrides_failed_entry_wait(self):
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 0, 59, 59)
        self.signal(0, 1)
        self.fail_after_fill = True
        m.tick()
        self.now = datetime(2026, 9, 15, 1, 0)
        m.tick()
        self.assertEqual(self.actual, 0)
        self.assertEqual(self.orders, [1, -2])

    def test_failed_flat_is_not_retried(self):
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 1, 0)
        self.fail_after_fill = True
        m.tick()
        self.now += timedelta(seconds=10)
        m.tick()
        self.assertEqual(self.orders, [-1])
        self.assertEqual(m.state["attempt"]["status"], "failed_no_retry")

    def test_flat_restart_in_same_minute_does_not_resubmit(self):
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 1, 0)
        self.fail_after_fill = True
        m.tick()
        self.now += timedelta(seconds=5)
        self.monitor().tick()
        self.assertEqual(self.execute.call_count, 1)

    def test_rejected_signal_does_not_block_next_in_same_batch(self):
        m = self.monitor()
        self.now += timedelta(seconds=1)
        self.signal(0, 1)
        self.signal(0, -1, "CFCTX16m")
        self.execute.side_effect = [auto_trade.BrokerOrderError("reject"), NS(submitted=True, side="sell", quantity=1)]
        m.tick()
        self.assertEqual(self.execute.call_count, 2)
        self.now += timedelta(minutes=5)
        m.tick()
        self.assertEqual(self.execute.call_count, 2)

    def test_batch_does_not_cross_flat_boundary(self):
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 0, 59, 59)
        for code in ("CFC07m", "CFCTX16m"):
            self.signal(0, 1, code)
        def slow(*args, **kwargs):
            result = self.broker(*args, **kwargs)
            self.now = datetime(2026, 9, 15, 1, 0)
            return result
        self.execute.side_effect = slow
        m.tick()
        self.assertEqual(self.orders, [1])
        m.tick()
        self.assertEqual(self.orders, [1, -2])



class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 15, 1, 0)
        self.contract = NS(code="TMFI6")
        self.api = Mock()
        self.api.Contracts = NS(Futures=NS(TMF=NS(TMFR1=self.contract)))
        timeout = patch.object(auto_trade._shared, "ORDER_CALLBACK_TIMEOUT_SECONDS", 0)
        timeout.start()
        self.addCleanup(timeout.stop)
        self.api.list_trades.return_value = []
        self.api.list_positions.return_value = []
        self.api.place_order.return_value = NS(status=NS(status="Filled"))
        self.sj = NS(constant=NS(Action=NS(Buy="Buy", Sell="Sell"),
                               FuturesPriceType=NS(MKT="MKT"), OrderType=NS(IOC="IOC"),
                               FuturesOCType=NS(Auto="Auto")))

    def execute(self, target=-5, **kwargs):
        return auto_trade.execute_target_position(target,
            deadline=self.now + timedelta(seconds=40), clock=lambda: self.now,
            api=self.api, sj=self.sj, **kwargs)

    def test_six_transitions_submit_exactly_once_without_inventory_or_fill_query(self):
        for delta, side, quantity in ((1, "Buy", 1), (-1, "Sell", 1), (2, "Buy", 2), (-2, "Sell", 2)):
            with self.subTest(delta=delta):
                self.api.reset_mock()
                self.api.place_order.return_value = NS(status=NS(status="PendingSubmit"))
                result = self.execute(None, delta=delta)
                self.assertTrue(result.submitted)
                self.api.place_order.assert_called_once()
                self.api.list_positions.assert_not_called()
                self.api.update_status.assert_not_called()
                self.api.list_trades.assert_not_called()
                self.assertEqual(self.api.Order.call_args.kwargs["quantity"], quantity)
                self.assertEqual(self.api.Order.call_args.kwargs["action"], side)

    def test_flat_queries_inventory_and_submits_once_without_verification(self):
        self.api.list_positions.return_value = [{"code": "TMFI6", "quantity": 4, "direction": "Buy"}]
        result = self.execute(0)
        self.assertEqual((result.side, result.quantity), ("sell", 4))
        self.api.place_order.assert_called_once()
        self.api.update_status.assert_not_called()

    def test_flat_empty_account_no_order(self):
        self.assertFalse(self.execute(0).submitted)
        self.api.place_order.assert_not_called()

    def test_reset_confirmation_is_read_only_and_rejects_unknown_inventory(self):
        self.assertTrue(auto_trade.confirm_flat(api=self.api))
        self.api.list_positions.return_value = [{"code": "TMFI6", "quantity": 1, "direction": "Buy"}]
        self.assertFalse(auto_trade.confirm_flat(api=self.api))
        self.api.list_positions.return_value = None
        with self.assertRaises(auto_trade.BrokerOrderError):
            auto_trade.confirm_flat(api=self.api)
        self.api.place_order.assert_not_called()

    def test_reset_confirmation_rejects_open_orders_and_offset_inventory(self):
        self.api.list_trades.return_value = [NS(contract=self.contract, status=NS(status="PendingSubmit"))]
        with self.assertRaises(auto_trade.BrokerOrderError):
            auto_trade.confirm_flat(api=self.api)
        self.api.list_trades.return_value = []
        self.api.list_positions.return_value = [
            {"code": "TMFI6", "quantity": 1, "direction": "Buy"},
            {"code": "TMFI6", "quantity": 1, "direction": "Sell"}]
        with self.assertRaises(auto_trade.BrokerOrderError):
            auto_trade.confirm_flat(api=self.api)
        self.api.place_order.assert_not_called()

    def test_immediate_rejection_is_reported_without_retry(self):
        self.api.place_order.return_value = NS(status=NS(status="Failed"))
        with self.assertRaises(auto_trade.BrokerOrderError):
            self.execute(None, delta=1)
        self.api.place_order.assert_called_once()
        self.api.update_status.assert_not_called()

    def test_timeout_is_not_retried(self):
        self.api.place_order.side_effect = TimeoutError("unknown")
        with self.assertRaises(TimeoutError):
            self.execute(None, delta=1)
        self.api.place_order.assert_called_once()

    def test_past_deadline_never_submits(self):
        with self.assertRaises(auto_trade.BrokerOrderError):
            auto_trade.execute_target_position(None, delta=1, deadline=self.now,
                clock=lambda: self.now, api=self.api, sj=self.sj)
        self.api.place_order.assert_not_called()

    def test_credentials_pair2_and_account_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            ca = Path(folder) / "test.pfx"
            ca.touch()
            env = {"API_KEY": "never", "SECRET_KEY": "never", "API_KEY2": "key2",
                   "SECRET_KEY2": "secret2", "PERSON_ID": "person", "CA_PATH": str(ca),
                   "EF_HEDGE_ACCOUNT_ID": "account2"}
            sj = Mock()
            api = sj.Shioaji.return_value
            api.futopt_account.account_id = "account2"
            with patch.dict(os.environ, env, clear=True):
                auto_trade.login(sj)
                api.login.assert_called_once_with("key2", "secret2")
                api.futopt_account.account_id = "account1"
                with self.assertRaises(auto_trade.BrokerOrderError):
                    auto_trade.login(sj)
                del os.environ["EF_HEDGE_ACCOUNT_ID"]
                auto_trade.login(sj)  # Optional account check, same default account as account 1.
                del os.environ["API_KEY2"]
                with self.assertRaisesRegex(ValueError, "API_KEY2"):
                    auto_trade.login(sj)


class BacktestTests(unittest.TestCase):
    def test_morning_flat_and_only_new_leg_reentry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            signals, prices = root / "signals.csv", root / "prices.csv"
            signals.write_text("received_at,strategy_code,new_position\n"
                "2026-09-14 23:00:00,CFC07m,1\n"
                "2026-09-15 05:30:00,CFCTX17m,1\n"
                "2026-09-15 08:46:00,CFCTX18m,-1\n")
            prices.write_text("Symbol,TradingView Time,Record Time,Open\n"
                "MXF1!,2026-09-14 23:01:00,2026-09-14 23:02:00,10000\n"
                "MXF1!,2026-09-15 01:00:00,2026-09-15 05:00:00,10100\n"
                "MXF1!,2026-09-15 08:47:00,2026-09-15 08:48:00,10200\n")
            args = (signals, prices, Calendar.load(BASE / "config/calendar.json"),
                    datetime(2026, 9, 14, 22), datetime(2026, 9, 15, 9))
            result = run(*args)
            self.assertEqual([row["target"] for row in result["ledger"]], [1, 0, -1])
            self.assertEqual(result["net_twd"], 940)
            prices.write_text(prices.read_text().replace("01:00:00", "00:59:00"))
            with self.assertRaisesRegex(ValueError, "缺少精確"):
                run(*args)


if __name__ == "__main__":
    unittest.main()
