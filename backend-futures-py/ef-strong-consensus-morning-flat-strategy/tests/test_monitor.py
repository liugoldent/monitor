from __future__ import annotations

import csv
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, ANY


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
from strategy import ALL_STRATEGIES, PORTFOLIO_E, PORTFOLIO_F, PriceBar  # noqa: E402


class PortfolioTradeTests(unittest.TestCase):
    def test_main_forces_live_even_with_legacy_disabled_setting(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "false"}
        ), patch.object(sys, "argv", ["monitor_and_trade.py"]), patch.object(
            monitor, "load_env_file"
        ), patch.object(monitor, "RUNTIME_DIR", Path(directory)), patch.object(
            monitor, "LOCK_PATH", Path(directory) / "monitor.lock"
        ), patch.object(monitor, "load_signal_rows", side_effect=RuntimeError("test stop")):
            with self.assertRaisesRegex(RuntimeError, "test stop"):
                monitor.main()
            self.assertTrue(monitor.env_flag(monitor.ENABLE_ORDERS_ENV))

    def test_live_bookkeeping_does_not_send_shadow_notifications(self):
        state = {"position": 0, "source_row_count": 0,
                 "raw_positions": dict.fromkeys(ALL_STRATEGIES, 0)}
        rows = [{"received_at": "2026-09-15 09:00:15", "strategy_code": PORTFOLIO_E[0],
                 "previous_position": "0", "new_position": "1"}]
        bar = PriceBar(datetime(2026, 9, 15, 9, 1), datetime(2026, 9, 15, 9, 1), 100, 100)
        with patch.dict(monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}), patch.object(
            monitor, "send_discord"
        ) as notify, patch.object(monitor, "append_csv"), patch.object(
            monitor, "save_json_atomic"
        ), patch.object(monitor, "write_position"), patch.object(monitor, "execute_target_position") as broker:
            monitor.process_new_rows(state, rows, [bar], bar.record_time, 2)
            monitor.apply_flatten_bar(state, PriceBar(datetime(2026, 9, 16, 1),
                                                     datetime(2026, 9, 16, 1), 100, 100))
        self.assertEqual(state["source_row_count"], 1)
        notify.assert_not_called()
        broker.assert_not_called()

    def test_wall_clock_blocks_delayed_entry_until_reopen(self):
        state = {}
        with patch.object(monitor, "env_flag", return_value=True), patch.object(
            monitor, "execute_target_position"
        ) as broker:
            for hour, minute in ((1, 0), (2, 0), (4, 59), (5, 0), (8, 44)):
                with patch.object(monitor, "now_local", return_value=datetime(2026, 9, 15, hour, minute)):
                    result = monitor.execute_live_target(state, 1, trigger="delayed_signal")
                    self.assertIn("禁止進場", result)
            broker.assert_not_called()

    def test_flat_bar_uses_0100_even_when_later_night_bars_exist(self):
        bars = [PriceBar(datetime(2026, 9, 15, h, m), datetime(2026, 9, 15, h, m), p, p)
                for h, m, p in ((0, 59, 100), (1, 0, 101), (4, 59, 200))]
        boundaries = monitor.morning_boundaries(bars)
        self.assertEqual(len(boundaries), 1)
        self.assertEqual(boundaries[0].bar_time, datetime(2026, 9, 15, 1))
        self.assertEqual(boundaries[0].open, 101)

    def test_flatten_closes_one_portfolio_leg_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            records = Path(directory)
            state = {
                "raw_positions": {code: 0 for code in ALL_STRATEGIES},
                "position": 1,
                "entry_price": 45000,
                "threshold": 2,
            }
            boundary = PriceBar(
                datetime(2026, 8, 28, 1, 0),
                datetime(2026, 8, 28, 5, 0),
                45020,
                45030,
            )
            with (
                patch.object(monitor, "TRADE_PATH", records / "trades.csv"),
                patch.object(monitor, "DECISION_PATH", records / "decisions.csv"),
                patch.object(monitor, "POSITION_PATH", records / "position.json"),
            ):
                first = monitor.apply_flatten_bar(
                    state, boundary, persist=True, notify=False
                )
                second = monitor.apply_flatten_bar(
                    state, boundary, persist=False, notify=False
                )
            self.assertTrue(first)
            self.assertFalse(second)
            with (records / "trades.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["action"], "exiting")
            self.assertEqual(rows[0]["pnl_points"], "20.0")
            self.assertEqual(rows[0]["pnl_twd"], "200.0")
            self.assertEqual(state["position"], 0)


class WebhookTests(unittest.TestCase):
    def test_requested_webhook_name_has_priority(self):
        with patch.dict(
            monitor.os.environ,
            {
                "DISCORD_EFSTRONG_MORNING_FLAT_WEBHOOK_URL": "https://requested.example",
                "DISCORD_EF_STRONG_MORNING_FLAT_WEBHOOK_URL": "https://legacy.example",
                "DISCORD_MXF_ALERT_WEBHOOK_URL": "https://fallback.example",
            },
            clear=True,
        ):
            self.assertEqual(monitor.webhook_url(), "https://requested.example")


class LiveOrderTests(unittest.TestCase):
    def test_failed_signal_does_not_block_same_target_new_event_or_flat(self):
        state = {}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}
        ), patch.object(monitor, "STATE_PATH", Path(directory) / "state.json"), patch.object(
            monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"
        ), patch.object(monitor, "execute_target_position", side_effect=TimeoutError()) as execute:
            first = monitor.execute_live_target(state, 1, trigger="signal_1")
            self.assertIn("下一筆先核對未確認委託與庫存", first)
            self.assertEqual(state["attempt"]["status"], "failed_no_retry")
            monitor.execute_live_target(state, 1, trigger="signal_1")
            self.assertEqual(execute.call_count, 1)
            monitor.execute_live_target(state, 1, trigger="signal_2")
            self.assertEqual(execute.call_count, 2)
            monitor.execute_live_target(state, 0, trigger="01:00_live_clock_flat", force_reconcile=True)
            self.assertEqual(execute.call_count, 3)
            monitor.execute_live_target(state, 0, trigger="01:00_live_clock_flat", force_reconcile=True)
            self.assertEqual(execute.call_count, 3)

    def test_restart_releases_legacy_failed_attempt_without_order(self):
        state = {"attempt": {"key": "old", "status": "failed"}, "last_order_error": "BrokerOrderError"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}
        ), patch.object(monitor, "STATE_PATH", Path(directory) / "state.json"), patch.object(
            monitor, "execute_target_position"
        ) as execute:
            text = monitor.execute_live_target(state, 1, trigger="startup_reconcile")
            execute.assert_not_called()
            self.assertIn("啟動不補單", text)
            self.assertEqual(state["attempt"]["status"], "failed_no_retry")
            self.assertEqual(state["last_unconfirmed_attempt"]["status"], "failed")

    def test_failed_flat_notifies_and_new_signal_after_reopen_is_allowed(self):
        state = {"live_target_position": 1}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}
        ), patch.object(monitor, "STATE_PATH", Path(directory) / "state.json"), patch.object(
            monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"
        ), patch.object(monitor, "CLOCK_EVENT_PATH", Path(directory) / "clock.csv"), patch.object(
            monitor, "now_local", return_value=datetime(2026, 9, 15, 1, 0, 2)
        ) as clock, patch.object(monitor, "execute_target_position", side_effect=TimeoutError()) as execute, patch.object(
            monitor, "send_discord"
        ) as notify, patch("builtins.print"):
            monitor.apply_live_clock_flatten(state, clock.return_value)
            self.assertIn("手動清倉", notify.call_args.args[0])
            self.assertEqual(state["attempt"]["status"], "failed_no_retry")
            monitor.apply_live_clock_flatten(state, clock.return_value)
            self.assertEqual(execute.call_count, 1)
            clock.return_value = datetime(2026, 9, 15, 8, 45)
            execute.side_effect = None
            execute.return_value = SimpleNamespace(actual_position=1, previous_position=0,
                                                  quantity=1, side="buy")
            monitor.execute_live_target(state, 1, trigger="immediate_ef_signal_row_2")
            self.assertEqual(execute.call_count, 2)
            self.assertEqual(state["attempt"]["status"], "done")
            self.assertEqual(state["manual_flat_required"]["status"], "failed")

    def test_interrupted_flat_releases_duplicate_zero_target_after_reopen(self):
        state = {"attempt": {"key": "01:00_live_clock_flat", "status": "pending",
                             "at": "2026-09-15T01:00:00"}, "last_order_attempt_target": 0}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}
        ), patch.object(monitor, "STATE_PATH", Path(directory) / "state.json"), patch.object(
            monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"
        ), patch.object(monitor, "now_local", return_value=datetime(2026, 9, 15, 8, 45)), patch.object(
            monitor, "execute_target_position", return_value=SimpleNamespace(actual_position=0,
                previous_position=0, quantity=0, side=None)
        ) as execute, patch.object(monitor, "send_discord"):
            monitor.execute_live_target(state, 0, trigger="startup_reconcile")
            execute.assert_not_called()  # Restart must not replay the failed flat.
            monitor.execute_live_target(state, 0, trigger="immediate_ef_signal_row_2")
            execute.assert_called_once_with(0, guard=ANY, persist_guard=ANY)
            self.assertEqual(state["manual_flat_required"]["status"], "pending")

    def test_pending_attempt_allows_changed_target_and_forced_flat(self):
        state = {"attempt": {"status": "pending", "target": 1}}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"), patch.dict(
            monitor.os.environ, {monitor.ENABLE_ORDERS_ENV: "true"}
        ), patch.object(
            monitor, "execute_target_position"
        ) as execute:
            for target, trigger in ((-1, "new_signal"), (0, "01:00_live_clock_flat")):
                execute.return_value = SimpleNamespace(actual_position=target, previous_position=1,
                                                        quantity=1-target, side="sell")
                monitor.execute_live_target(state, target, trigger=trigger, force_reconcile=True)
        self.assertEqual(execute.call_count, 2)

    def test_history_rebuild_preserves_unresolved_order(self):
        previous = {"attempt": {"status": "failed", "target": 1}, "last_order_attempt_target": 1}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(monitor, "write_position"):
            state = monitor.initialize_state([], [], datetime(2026, 9, 14, 9), 2,
                                             previous_state=previous)
        self.assertEqual(state["attempt"], previous["attempt"])
        self.assertEqual(state["last_order_attempt_target"], 1)

    def test_atomic_json_save_retries_transient_replace_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original_replace = Path.replace
            calls = 0

            def replace_after_one_lock(source, target):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("temporarily locked")
                return original_replace(source, target)

            with patch.object(Path, "replace", replace_after_one_lock), patch.object(
                monitor.time, "sleep"
            ):
                saved = monitor.save_json_atomic(path, {"position": 1})

            self.assertTrue(saved)
            self.assertEqual(calls, 2)
            self.assertEqual(monitor.load_json(path, {}), {"position": 1})

    def test_live_target_uses_verified_reconciliation(self):
        state = {}
        result = SimpleNamespace(
            order_sent=True,
            side="buy",
            quantity=2,
            previous_position=0,
            actual_position=2,
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
            text = monitor.execute_live_target(state, 1, trigger="test")

        execute.assert_called_once_with(2, guard=ANY, persist_guard=ANY)
        self.assertEqual(state["last_executed_target"], 2)
        self.assertIn("已回查確認", text)

    def test_webhook_places_scaled_final_quantity_below_time(self):
        decision = SimpleNamespace(
            previous_position=0,
            target_position=-1,
            event=SimpleNamespace(
                timestamp=datetime(2026, 8, 28, 9, 1, 15),
                strategy_name="test",
                strategy_code="CFC07m",
                previous_position=0,
                new_position=-1,
            ),
            execution_time=datetime(2026, 8, 28, 9, 2),
            execution_price=46000,
            e_net=-2,
            f_net=-2,
            reason="test consensus",
        )
        with patch.dict(
            monitor.os.environ, {monitor.POSITION_UNIT_ENV: "2"}, clear=False
        ):
            message = monitor.immediate_live_message(decision, "ok")
        self.assertIn(
            "策略目標部位：空2口",
            message,
        )

    def test_webhook_lists_each_strategy_behind_group_net(self):
        e_positions = tuple((code, 1 if index < 3 else 0) for index, code in enumerate(PORTFOLIO_E))
        f_positions = tuple((code, -1 if index == 0 else 0) for index, code in enumerate(PORTFOLIO_F))
        decision = SimpleNamespace(
            previous_position=0,
            target_position=0,
            event=SimpleNamespace(
                timestamp=datetime(2026, 8, 28, 9, 1, 15),
                strategy_name="test",
                strategy_code=PORTFOLIO_E[0],
                previous_position=0,
                new_position=1,
            ),
            e_net=3,
            f_net=-1,
            e_positions=e_positions,
            f_positions=f_positions,
            reason="未形成雙組同向強共識",
        )
        message = monitor.immediate_live_message(decision, "ok")
        self.assertIn(f"E明細：{PORTFOLIO_E[0]}(+1)", message)
        self.assertIn("= +3", message)
        self.assertIn(f"F明細：{PORTFOLIO_F[0]}(-1)", message)
        self.assertIn("= -1", message)

    def test_same_signal_is_not_resent(self):
        state = {"attempt": {"key": "test", "status": "done"}, "last_order_attempt_target": -1}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true"},
            clear=False,
        ), patch.object(
            monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"
        ), patch.object(monitor, "execute_target_position") as execute:
            text = monitor.execute_live_target(state, -1, trigger="test")

        execute.assert_not_called()
        self.assertIn("不重送", text)

    def test_failed_broker_attempt_is_persisted(self):
        state = {}
        with tempfile.TemporaryDirectory() as directory:
            order_path = Path(directory) / "orders.csv"
            with patch.dict(
                monitor.os.environ,
                {monitor.ENABLE_ORDERS_ENV: "true"},
                clear=False,
            ), patch.object(
                monitor, "STATE_PATH", Path(directory) / "state.json"
            ), patch.object(
                monitor, "ORDER_ATTEMPT_PATH", order_path
            ), patch.object(
                monitor, "execute_target_position", side_effect=RuntimeError("broker down")
            ):
                text = monitor.execute_live_target(state, 1, trigger="test_failure")
            with order_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        self.assertIn("下單失敗", text)
        self.assertEqual([row["event"] for row in rows], ["attempt_started", "failed"])
        self.assertEqual(rows[-1]["trigger"], "test_failure")

    def test_order_is_blocked_when_duplicate_guard_cannot_be_persisted(self):
        state = {}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true"},
            clear=False,
        ), patch.object(
            monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"
        ), patch.object(
            monitor, "save_json_atomic", return_value=False
        ), patch.object(monitor, "execute_target_position") as execute:
            text = monitor.execute_live_target(state, 1, trigger="test")

        execute.assert_not_called()
        self.assertIn("不自動重送", text)

    def test_idle_poll_does_not_rewrite_state(self):
        positions = {code: 0 for code in ALL_STRATEGIES}
        state = {
            "live_source_row_count": 0,
            "live_raw_positions": positions,
            "live_target_position": 0,
            "source_row_count": 0,
            "raw_positions": positions,
            "position": 0,
        }
        with patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true"},
            clear=False,
        ), patch.object(monitor, "save_json_atomic") as save:
            monitor.process_live_rows(state, [], threshold=2)
            monitor.process_new_rows(
                state,
                [],
                [],
                datetime(2026, 9, 9, 11, 0),
                threshold=2,
            )

        save.assert_not_called()

    def test_new_signal_executes_immediately_without_waiting_for_price_bar(self):
        positions = {code: 0 for code in ALL_STRATEGIES}
        positions[PORTFOLIO_E[1]] = 1
        positions[PORTFOLIO_F[0]] = 1
        positions[PORTFOLIO_F[1]] = 1
        state = {
            "live_source_row_count": 0,
            "live_raw_positions": positions,
            "live_target_position": 0,
        }
        rows = [
            {
                "received_at": "2026-08-27 09:00:15",
                "strategy_code": PORTFOLIO_E[0],
                "strategy_name": "test",
                "previous_position": "0",
                "new_position": "1",
            }
        ]
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true"},
            clear=False,
        ), patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(
            monitor, "execute_live_target", return_value="ok"
        ) as execute, patch.object(monitor, "send_discord"), patch("builtins.print"):
            monitor.process_live_rows(state, rows, threshold=2)

        execute.assert_called_once_with(
            state,
            1,
            trigger="immediate_ef_signal_row_1",
        )
        self.assertEqual(state["live_target_position"], 1)
        self.assertEqual(state["live_source_row_count"], 1)

    def test_0100_clock_flattens_without_waiting_for_bar_file(self):
        state = {"live_target_position": 1}
        current = datetime(2026, 8, 28, 5, 0, 2)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true"},
            clear=False,
        ), patch.object(
            monitor, "CLOCK_EVENT_PATH", Path(directory) / "clock.csv"
        ), patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(
            monitor, "execute_live_target", return_value="ok"
        ) as execute, patch.object(monitor, "send_discord"), patch("builtins.print"):
            monitor.apply_live_clock_flatten(state, current)

        execute.assert_called_once_with(
            state,
            0,
            trigger="01:00_live_clock_flat",
            force_reconcile=True,
        )
        self.assertEqual(state["live_target_position"], 0)
        self.assertEqual(state["last_live_flat_time"], "2026-08-28 01:00:00")

    def test_0100_shadow_mode_is_still_audited(self):
        state = {"position": 1}
        current = datetime(2026, 8, 28, 1, 0, 2)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "false"},
            clear=False,
        ), patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(
            monitor, "CLOCK_EVENT_PATH", Path(directory) / "clock.csv"
        ), patch.object(
            monitor, "execute_live_target"
        ) as execute, patch.object(monitor, "send_discord"), patch("builtins.print"):
            applied = monitor.apply_live_clock_flatten(state, current)
            self.assertTrue(applied)
            execute.assert_not_called()
            with (Path(directory) / "clock.csv").open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["mode"], "shadow_only")
            self.assertEqual(row["target_position"], "0")


if __name__ == "__main__":
    unittest.main()
