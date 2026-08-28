from __future__ import annotations

import csv
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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
                datetime(2026, 8, 28, 4, 59),
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

        execute.assert_called_once_with(2)
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
            "收到時間：2026-08-28 09:01:15\n收到訊號後【最終口數】：空2口",
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

    def test_same_failed_or_successful_target_is_not_resent(self):
        state = {"last_order_attempt_target": -1}
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

    def test_0459_clock_flattens_without_waiting_for_bar_file(self):
        state = {"live_target_position": 1}
        current = datetime(2026, 8, 28, 5, 0, 2)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            monitor.os.environ,
            {monitor.ENABLE_ORDERS_ENV: "true"},
            clear=False,
        ), patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(
            monitor, "execute_live_target", return_value="ok"
        ) as execute, patch.object(monitor, "send_discord"), patch("builtins.print"):
            monitor.apply_live_clock_flatten(state, current)

        execute.assert_called_once_with(
            state,
            0,
            trigger="04:59_live_clock_flat",
            force_reconcile=True,
        )
        self.assertEqual(state["live_target_position"], 0)
        self.assertEqual(state["last_live_flat_time"], "2026-08-28 04:59:00")

    def test_0459_shadow_mode_is_still_audited(self):
        state = {"position": 1}
        current = datetime(2026, 8, 28, 4, 59, 2)
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
