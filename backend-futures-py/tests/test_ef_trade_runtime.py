"""Offline regression tests: never log in or use a real webhook."""
import csv
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from ef_trade_runtime import Notifications, append_order, perform_order


class LifecycleTests(unittest.TestCase):
    def test_failure_blocks_changed_target_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = {}
            execute = Mock(side_effect=RuntimeError("secret broker response"))
            records = []
            def persist():
                path.write_text(json.dumps(state))
            with self.assertRaises(RuntimeError):
                perform_order(state, key="one", target=1, persist=persist,
                              execute=execute, record=lambda **r: records.append(r))
            state = json.loads(path.read_text())
            with self.assertRaisesRegex(RuntimeError, "未確認"):
                perform_order(state, key="two", target=-1, persist=persist,
                              execute=execute, record=lambda **r: records.append(r))
            self.assertEqual(execute.call_count, 1)
            self.assertEqual([r["event"] for r in records], ["attempt_started", "failed"])
            self.assertNotIn("secret", json.dumps(records))

    def test_persistence_failure_never_calls_broker(self):
        execute = Mock()
        with self.assertRaises(OSError):
            perform_order({}, key="one", target=1, persist=lambda: False,
                          execute=execute, record=lambda **r: None)
        execute.assert_not_called()

    def test_confirmation_failure_stays_locked(self):
        state = {}
        result = NS(previous_position=0, actual_position=1, quantity=1, side="buy")
        def record(**r):
            if r["event"] == "order_sent_confirmed":
                raise OSError("disk full")
        with self.assertRaises(OSError):
            perform_order(state, key="one", target=1, persist=lambda: True,
                          execute=lambda: result, record=record)
        self.assertEqual(state["attempt"]["status"], "failed")

    def test_both_accounts_write_same_schema_and_result_events(self):
        with tempfile.TemporaryDirectory() as folder:
            outputs = []
            for account in ("primary", "secondary"):
                path = Path(folder) / account / "live_order_attempts.csv"
                state = {}
                result = NS(previous_position=-1, actual_position=1, quantity=2, side="buy")
                perform_order(state, key="signal", target=1, persist=lambda: True,
                              execute=lambda: result, record=lambda **r: append_order(path, **r))
                with path.open(encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))
                outputs.append([(r["event"], r["quantity"], r["actual_position"]) for r in rows])
                self.assertEqual(state["attempt"]["status"], "done")
            self.assertEqual(outputs[0], outputs[1])


class NotificationTests(unittest.TestCase):
    def test_slow_webhook_does_not_block_and_full_message_is_audited(self):
        with tempfile.TemporaryDirectory() as folder:
            entered, release = threading.Event(), threading.Event()
            def post(*args, **kwargs):
                entered.set()
                release.wait(2)
                return Mock()
            send = Mock(side_effect=post)
            path = Path(folder) / "notifications.jsonl"
            notifier = Notifications(lambda: "https://example.invalid/test", path, post=send)
            try:
                self.assertTrue(notifier("測" * 2000))
                self.assertTrue(entered.wait(1))
                self.assertEqual(notifier.messages.unfinished_tasks, 1)
            finally:
                release.set()
                notifier.flush()
            self.assertEqual(send.call_count, 2)
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["content"]), 2000)

    def test_failed_webhook_does_not_expose_url(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notifications.jsonl"
            notifier = Notifications(lambda: "secret-url", path,
                                     post=Mock(side_effect=RuntimeError("secret-url")))
            notifier("test")
            notifier.flush()
            record = path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(record)["status"], "failed")
            self.assertNotIn("secret-url", record)


class AdapterParityTests(unittest.TestCase):
    def test_both_adapters_block_pending_other_month_and_hedged_inventory(self):
        for folder in ("ef-strong-consensus-morning-flat-strategy", "ef-morning-weekend-hedge-strategy"):
            spec = importlib.util.spec_from_file_location("adapter_" + folder, BACKEND / folder / "auto_trade.py")
            adapter = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(adapter)
            for case in ("pending", "other_month", "hedged", "expired"):
                with self.subTest(adapter=folder, case=case):
                    api = Mock()
                    api.Contracts = NS(Futures=NS(TMF=NS(TMFR1=NS(code="TMFI6"))))
                    api.list_trades.return_value = []
                    api.list_positions.return_value = []
                    if case == "pending":
                        api.list_trades.return_value = [NS(contract=NS(code="TMFI6"), status=NS(status="Submitted"))]
                    elif case == "other_month":
                        api.list_positions.return_value = [dict(code="TMFJ6", quantity=1, direction="Buy")]
                    elif case == "hedged":
                        api.list_positions.return_value = [dict(code="TMFI6", quantity=1, direction=side) for side in ("Buy", "Sell")]
                    now = datetime(2026, 9, 15, 4, 59, 41 if case == "expired" else 1)
                    with patch.dict("os.environ", {"EF_STRONG_MORNING_FLAT_POSITION_UNIT": "1"}):
                        with self.assertRaises(adapter.BrokerOrderError):
                            adapter.execute_target_position(1, api=api, sj=Mock(),
                                deadline=datetime(2026, 9, 15, 4, 59, 40), clock=lambda: now)
                    api.place_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
