from __future__ import annotations

import csv
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = RuntimeError
    requests_stub.post = lambda *args, **kwargs: None
    sys.modules["requests"] = requests_stub

try:
    import filelock  # noqa: F401
except ModuleNotFoundError:
    filelock_stub = types.ModuleType("filelock")
    filelock_stub.FileLock = object
    filelock_stub.Timeout = RuntimeError
    sys.modules["filelock"] = filelock_stub

import monitor_and_trade as monitor  # noqa: E402
from strategy import (  # noqa: E402
    Action,
    BreakRetestEngine,
    Config,
    EngineState,
    PriceBar,
)


class MonitorSafetyTests(unittest.TestCase):
    def test_requested_webhook_name_has_priority(self) -> None:
        with patch.dict(
            monitor.os.environ,
            {
                "DISCORD_OPENING_RETEST": "https://requested.example",
                "DISCORD_OPENING_RETEST_WEBHOOK_URL": "https://legacy.example",
                "DISCORD_MXF_ALERT_WEBHOOK_URL": "https://fallback.example",
            },
            clear=False,
        ):
            self.assertEqual(monitor.webhook_url(), "https://requested.example")

    def test_first_start_does_not_replay_old_orders_or_enter_mid_session(self) -> None:
        bars = [
            PriceBar(
                datetime(2026, 8, 3, 9, 0),
                datetime(2026, 8, 3, 9, 1),
                100,
                101,
                99,
                100,
            )
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(
            monitor, "POSITION_PATH", Path(directory) / "position.json"
        ):
            engine, _ = monitor.initialize_runtime(bars, Config())
        self.assertEqual(engine.state.last_bar_time, bars[-1].bar_time)
        self.assertEqual(engine.state.session_date, date(2026, 8, 3))
        self.assertTrue(engine.state.signal_used_today)
        self.assertEqual(engine.state.position, 0)

    def test_live_mode_does_not_resend_same_target(self) -> None:
        runtime = {"last_order_attempt_target": 1}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}
        ), patch.object(
            monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"
        ), patch.object(monitor, "execute_target_position") as execute:
            text = monitor.execute_live_target(runtime, 1, trigger="test")
        execute.assert_not_called()
        self.assertIn("不重送", text)

    def test_failed_broker_attempt_is_persisted(self) -> None:
        runtime = {}
        with tempfile.TemporaryDirectory() as directory:
            order_path = Path(directory) / "orders.csv"
            with patch.dict(
                monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}
            ), patch.object(
                monitor, "STATE_PATH", Path(directory) / "state.json"
            ), patch.object(
                monitor, "ORDER_ATTEMPT_PATH", order_path
            ), patch.object(
                monitor, "execute_target_position", side_effect=RuntimeError("broker down")
            ):
                text = monitor.execute_live_target(runtime, -1, trigger="test_failure")
            with order_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertIn("下單失敗", text)
        self.assertEqual([row["event"] for row in rows], ["attempt_started", "failed"])
        self.assertEqual(rows[-1]["trigger"], "test_failure")

    def test_u_two_scales_target_and_notification_quantity(self) -> None:
        runtime = {}
        result = SimpleNamespace(
            order_sent=True,
            side="buy",
            quantity=2,
            previous_position=0,
            actual_position=2,
        )
        action = Action(
            datetime(2026, 8, 28, 9, 1),
            "enter",
            1,
            46000,
            "test",
            target_price=46100,
            stop_price=45900,
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true", monitor.POSITION_UNIT_ENV: "2"},
            clear=False,
        ), patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(
            monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"
        ), patch.object(
            monitor, "execute_target_position", return_value=result
        ) as execute:
            live_result = monitor.execute_live_target(runtime, 1, trigger="test_u")
            message = monitor.action_message(action, live_result)
        execute.assert_called_once_with(2)
        self.assertEqual(runtime["last_executed_target"], 2)
        self.assertIn("收到訊號後【最終口數】：多2口", message)

    def test_restart_catchup_never_sends_historical_exit(self) -> None:
        state = EngineState(
            position=1,
            entry_price=100,
            target_price=110,
            stop_price=90,
            session_date=date(2026, 8, 3),
            signal_used_today=True,
            last_bar_time=datetime(2026, 8, 3, 10, 59),
        )
        engine = BreakRetestEngine(Config(force_flat_time=time(11, 0)), state)
        bars = [
            PriceBar(
                datetime(2026, 8, 3, 11, 0),
                datetime(2026, 8, 3, 11, 1),
                105,
                106,
                104,
                105,
            )
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            monitor, "DAILY_WARMUP_PATH", Path(directory) / "daily.csv"
        ), patch.object(
            monitor, "append_action"
        ), patch.object(
            monitor, "write_position"
        ), patch.object(
            monitor, "execute_live_target"
        ) as execute:
            monitor.process_new_bars(
                engine, {}, bars, Config(), execute_orders=False, notify=False
            )
        execute.assert_not_called()
        self.assertEqual(engine.state.position, 0)

    def test_same_bar_entry_and_stop_reconciles_only_final_flat_position(self) -> None:
        engine = Mock()
        engine.state = EngineState(
            position=0,
            last_bar_time=datetime(2026, 8, 3, 9, 16),
        )
        actions = [
            Action(datetime(2026, 8, 3, 9, 17), "enter", 1, 100, "opening_break_retest"),
            Action(datetime(2026, 8, 3, 9, 17), "exit", 1, 90, "entry_bar_stop", -10),
        ]
        engine.process_bar.return_value = (actions, [])
        bars = [
            PriceBar(
                datetime(2026, 8, 3, 9, 17),
                datetime(2026, 8, 3, 9, 18),
                100,
                111,
                89,
                100,
            )
        ]
        with patch.object(monitor, "_regimes", return_value={}), patch.object(
            monitor, "append_action"
        ), patch.object(
            monitor, "write_position"
        ), patch.object(
            monitor, "safe_print"
        ), patch.object(
            monitor, "execute_live_target", return_value="flat"
        ) as execute:
            monitor.process_new_bars(engine, {}, bars, Config(), notify=False)
        execute.assert_called_once_with({}, 0, trigger="entry_bar_stop")


if __name__ == "__main__":
    unittest.main()
