"""Offline checks for the API_KEY handoff and Again order decisions."""
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import filelock  # noqa: F401
except ImportError:
    fake_filelock = types.ModuleType("filelock")
    fake_filelock.FileLock = object
    fake_filelock.Timeout = TimeoutError
    sys.modules["filelock"] = fake_filelock

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
SPEC = importlib.util.spec_from_file_location("again_live_monitor_under_test", BASE / "monitor_and_trade.py")
monitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)
# The production monitor intentionally imports the shared source strategy.
# Let the separate rule tests import this directory's own strategy module.
sys.modules.pop("strategy", None)


class LiveRoutingTests(unittest.TestCase):
    def test_startup_inventory_is_read_only_and_logged(self):
        api, notify = Mock(), Mock()
        with patch.object(monitor.auto_trade, "initialize_broker_session", return_value=api), \
             patch.object(monitor.auto_trade, "check_startup_broker", return_value=3) as check:
            self.assertEqual(monitor.open_broker_for_monitor(notify), (api, 3))
        check.assert_called_once_with(api)
        api.place_order.assert_not_called()

    def test_startup_login_timeout_exits_with_backoff_without_consuming_signals(self):
        notify, sleep = Mock(), Mock()
        with patch.object(monitor.auto_trade, "initialize_broker_session",
                          side_effect=TimeoutError("token_login")), \
             patch.object(monitor.auto_trade, "check_startup_broker") as check:
            with self.assertRaises(SystemExit) as exit_info:
                monitor.open_broker_for_monitor(notify, sleep=sleep)
        self.assertEqual(exit_info.exception.code, 1)
        check.assert_not_called()
        sleep.assert_called_once_with(30)
        self.assertIn("啟動查倉失敗", notify.call_args.args[0])

    def test_daily_inventory_is_read_only_and_once_per_day(self):
        state = {}
        api, notify = Mock(), Mock()
        with patch.object(monitor, "persist"), patch.object(
            monitor.auto_trade, "check_startup_broker", return_value=2
        ) as check:
            monitor.check_daily_inventory(state, datetime(2026, 10, 1, 8, 34), api, notify)
            monitor.check_daily_inventory(state, datetime(2026, 10, 1, 8, 35), api, notify)
            monitor.check_daily_inventory(state, datetime(2026, 10, 1, 8, 36), api, notify)
        check.assert_called_once_with(api)
        self.assertEqual(state["daily_inventory_position"], 2)
        self.assertIn("僅查詢，未送單", notify.call_args.args[0])
        api.place_order.assert_not_called()

    def test_reconnect_watchdog_waits_then_requests_restart(self):
        now = [10.0]
        watchdog = monitor.BrokerReconnectWatchdog(120, clock=lambda: now[0])
        watchdog.on_event(0, 12, "", "Session reconnecting")
        self.assertEqual(watchdog.status(), (True, False))
        now[0] = 129.0
        self.assertEqual(watchdog.status(), (True, False))
        now[0] = 130.0
        self.assertEqual(watchdog.status(), (True, True))
        watchdog.on_event(0, 13, "", "Session reconnected")
        self.assertEqual(watchdog.status(), (False, False))
        self.assertTrue(watchdog.consume_recovery())
        self.assertFalse(watchdog.consume_recovery())

    def test_repeated_reconnecting_notice_does_not_extend_deadline(self):
        now = [0.0]
        watchdog = monitor.BrokerReconnectWatchdog(120, clock=lambda: now[0])
        watchdog.on_event(0, 12, "", "Session reconnecting")
        now[0] = 100.0
        watchdog.on_event(0, 12, "", "Session reconnecting")
        now[0] = 120.0
        self.assertEqual(watchdog.status(), (True, True))

    def test_old_and_again_adapters_are_simulated(self):
        self.assertTrue(monitor.auto_trade._adapter is not None)
        self.assertTrue(monitor.auto_trade.BROKER_SIMULATION)
        self.assertTrue(monitor.auto_trade._adapter.BROKER_SIMULATION)
        old_path = BASE.parent / "ef-strong-consensus-morning-flat-strategy/auto_trade.py"
        self.assertIn("BROKER_SIMULATION = True", old_path.read_text(encoding="utf-8"))
        old_spec = importlib.util.spec_from_file_location("old_sim_adapter_under_test", old_path)
        old_adapter = importlib.util.module_from_spec(old_spec)
        sys.modules[old_spec.name] = old_adapter
        old_spec.loader.exec_module(old_adapter)
        self.assertTrue(old_adapter.BROKER_SIMULATION)
        with tempfile.TemporaryDirectory() as folder:
            ca_path = Path(folder) / "Sinopac.pfx"
            ca_path.touch()
            env = {"API_KEY": "fake", "SECRET_KEY": "fake", "PERSON_ID": "fake",
                   "CA_PATH": str(ca_path)}
            with patch.dict(os.environ, env, clear=True):
                live_sj, sim_sj = Mock(), Mock()
                monitor.auto_trade._adapter._login(live_sj)
                old_adapter._login(sim_sj)
        live_sj.Shioaji.assert_called_once_with(simulation=True)
        sim_sj.Shioaji.assert_called_once_with(simulation=True)

    def test_new_short_exits_long_and_orders_flat(self):
        e = monitor.PORTFOLIO_E
        f = monitor.PORTFOLIO_F
        positions = {code: 0 for code in monitor.ALL_STRATEGIES}
        for code in e[:3] + f[:2]:
            positions[code] = 1
        state = {"raw_positions": positions, "source_row_count": 0, "target": 1,
                 "long_locked": False, "short_locked": False,
                 "lock_initialized_date": "2026-09-30"}
        row = {"received_at": "2026-09-30 20:30:00", "strategy_code": e[3],
               "previous_position": "0", "new_position": "-1"}
        with patch.object(monitor, "persist"), patch.object(monitor, "append_decision"), patch.object(
            monitor, "execute_target", return_value="fake submission"
        ) as order:
            monitor.process_rows(state, [row], lambda _: None)
        self.assertEqual(state["target"], 0)
        self.assertEqual(order.call_args.args[:2], (state, 0))

    def test_startup_rebuild_does_not_send_historical_orders(self):
        code = monitor.PORTFOLIO_E[0]
        rows = [{"received_at": "2026-09-30 09:00:00", "strategy_code": code,
                 "previous_position": "0", "new_position": "1"}]
        state = {"attempt": {"key": "prior", "status": "done"}}
        with patch.object(monitor, "persist"), patch.object(monitor, "execute_target") as order:
            monitor.rebuild_startup_state(state, rows, datetime(2026, 9, 30, 20, 0))
        order.assert_not_called()
        self.assertEqual(state["source_row_count"], 1)
        self.assertEqual(state["attempt"]["key"], "prior")

    def test_morning_clock_flatten_reconciles_once(self):
        state = {"target": 1, "last_flat_date": None, "long_locked": True,
                 "short_locked": True, "lock_initialized_date": "2026-09-30"}
        now = datetime(2026, 10, 1, 1, 1)
        with patch.object(monitor, "persist"), patch.object(
            monitor, "execute_target", return_value="fake flat"
        ) as order:
            monitor.clock_flatten(state, now, lambda _: None)
            monitor.clock_flatten(state, now, lambda _: None)
        order.assert_called_once_with(state, 0, "01:00/2026-10-01")
        self.assertEqual(state["target"], 0)
        self.assertEqual(state["last_flat_date"], "2026-10-01")

    def test_one_broker_call_per_new_signal(self):
        state = {}
        sent = monitor.auto_trade._adapter.OrderResult(0, 1, None, "buy", 1,
                                                       confirmed=False)

        def fake_execute(target, *, on_prepared, on_submitted, **kwargs):
            on_prepared({"broker_before_position": 0, "broker_quantity": 1,
                         "broker_side": "buy"})
            on_submitted(sent)
            return sent

        with patch.object(monitor, "save_state", return_value=True), patch.object(
            monitor, "append_order"
        ), patch.object(monitor.auto_trade, "execute_target_position", side_effect=fake_execute) as order:
            first = monitor.execute_target(state, 1, "signal/one")
            second = monitor.execute_target(state, 1, "signal/one")
        self.assertIn("已呼叫買進 TMF 1口", first)
        self.assertIn("不重送", second)
        self.assertEqual(order.call_count, 1)


if __name__ == "__main__":
    unittest.main()
