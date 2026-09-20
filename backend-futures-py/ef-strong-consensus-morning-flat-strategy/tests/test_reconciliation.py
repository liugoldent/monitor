"""Offline broker scenarios: no credentials, network, or real orders."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_trade

adapter = auto_trade._shared


class Broker:
    def __init__(self, position=0):
        self.position = position
        self.trades = []
        self.orders = []
        self.futopt_account = object()
        self.Contracts = NS(Futures=NS(TMF=NS(TMFR1=NS(code="TMFI6"))))
        self.Order = lambda **kw: NS(**kw)
        self.status = "Filled"
        self.lag = False
        self.lost = False

    def list_positions(self, account):
        return [] if self.position == 0 else [dict(code="TMFI6", quantity=abs(self.position),
                                                   direction="Buy" if self.position > 0 else "Sell")]

    def list_trades(self):
        return self.trades

    def update_status(self, *args, **kwargs):
        pass

    def place_order(self, contract, order, **kwargs):
        self.orders.append(order)
        if self.lost:
            raise TimeoutError("response lost")
        filled = order.quantity if self.status == "Filled" else 0
        trade = NS(contract=contract, order=NS(id=str(len(self.orders))),
                   status=NS(status=self.status, deal_quantity=filled, msg="test rejection"))
        self.trades.append(trade)
        if not self.lag:
            self.position += filled * (1 if order.action == "Buy" else -1)
        return trade


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.api = Broker()
        self.guard = {}
        self.saved = []
        self.sj = NS(constant=NS(Action=NS(Buy="Buy", Sell="Sell"),
                                FuturesPriceType=NS(MKT="MKT"), OrderType=NS(IOC="IOC"),
                                FuturesOCType=NS(Auto="Auto")))
        self.sleep = patch.object(adapter.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def execute(self, target):
        return adapter.execute_target_position(target, api=self.api, sj=self.sj,
                    strict_tmf=True, guard=self.guard,
                    persist_guard=lambda: self.saved.append(json.loads(json.dumps(self.guard))))

    def test_sequential_entry_exit_queries_actual_and_confirms(self):
        first = self.execute(1)
        second = self.execute(0)
        self.assertEqual((first.previous_position, first.actual_position), (0, 1))
        self.assertEqual((second.previous_position, second.actual_position), (1, 0))
        self.assertEqual([(o.action, o.quantity, o.octype) for o in self.api.orders],
                         [("Buy", 1, "Auto"), ("Sell", 1, "Auto")])
        self.assertNotIn("pending", self.guard)
        self.assertEqual(self.saved[0]["pending"]["trade_id"], "")

    def test_same_target_does_not_add_but_external_inventory_change_is_corrected(self):
        self.execute(1)
        self.assertEqual(self.execute(1).quantity, 0)
        self.api.position = 0
        self.assertEqual(self.execute(1).quantity, 1)
        self.assertEqual(len(self.api.orders), 2)

    def test_reverse_uses_actual_difference(self):
        self.api.position = -1
        result = self.execute(1)
        self.assertEqual((result.side, result.quantity, result.actual_position), ("buy", 2, 1))

    def test_none_inventory_never_counts_as_flat(self):
        self.api.list_positions = Mock(return_value=None)
        with self.assertRaises(adapter.BrokerOrderError):
            self.execute(0)
        self.assertEqual(self.api.orders, [])

    def test_pending_order_blocks_even_when_inventory_equals_target(self):
        self.api.trades = [NS(contract=NS(code="TMFI6"), status=NS(status="Submitted"))]
        with self.assertRaises(adapter.BrokerOrderError):
            self.execute(0)
        self.assertEqual(self.api.orders, [])

    def test_fill_lag_survives_restart_and_cannot_report_false_flat(self):
        self.api.lag = True
        with self.assertRaises(adapter.BrokerOrderError):
            self.execute(1)
        self.guard = json.loads(json.dumps(self.guard))
        with self.assertRaisesRegex(adapter.BrokerOrderError, "尚未一致"):
            self.execute(0)
        self.assertEqual(len(self.api.orders), 1)
        self.api.position = 1
        self.api.lag = False
        self.assertEqual(self.execute(0).actual_position, 0)
        self.assertEqual(len(self.api.orders), 2)

    def test_lost_submission_response_blocks_next_signal_without_resubmission(self):
        self.api.lost = True
        with self.assertRaises(TimeoutError):
            self.execute(1)
        with self.assertRaisesRegex(adapter.BrokerOrderError, "人工核對"):
            self.execute(0)
        self.assertEqual(len(self.api.orders), 1)

    def test_rejection_can_be_resolved_before_new_signal(self):
        self.api.status = "Failed"
        with self.assertRaisesRegex(adapter.BrokerOrderError, "test rejection"):
            self.execute(1)
        self.api.status = "Filled"
        self.assertEqual(self.execute(1).actual_position, 1)

    def test_target_inventory_without_terminal_order_is_not_success(self):
        self.api.status = "Submitted"
        self.api.place_order = Mock(return_value=NS(order=NS(id="x"), status=NS(status="Submitted")))
        with patch.object(adapter, "current_tmf_position", return_value=1):
            with self.assertRaises(adapter.BrokerOrderError):
                self.execute(1)

    def test_unpersisted_intent_prevents_submission(self):
        with self.assertRaises(OSError):
            adapter.execute_target_position(1, api=self.api, sj=self.sj, strict_tmf=True,
                guard=self.guard, persist_guard=Mock(side_effect=OSError("disk")))
        self.assertEqual(self.api.orders, [])

    def test_partial_cancelled_fill_is_reconciled_before_next_target(self):
        self.guard["pending"] = dict(previous_position=-1, side="buy", quantity=2,
                                    target_position=1, trade_id="partial")
        self.api.trades = [NS(contract=NS(code="TMFI6"), order=NS(id="partial"),
                             status=NS(status="Cancelled", deal_quantity=1))]
        result = self.execute(1)
        self.assertEqual((result.previous_position, result.quantity, result.actual_position), (0, 1, 1))

    def test_deadline_after_persistence_prevents_order_without_unknown_submission(self):
        deadline = Mock(side_effect=[None, adapter.BrokerOrderError("deadline")])
        with self.assertRaisesRegex(adapter.BrokerOrderError, "deadline"):
            adapter.execute_target_position(1, api=self.api, sj=self.sj, strict_tmf=True,
                guard=self.guard, persist_guard=lambda: None, before_order=deadline)
        self.assertNotIn("pending", self.guard)
        self.assertEqual(self.api.orders, [])

    def test_hysteresis_holds_at_one_vote_then_exits_at_zero(self):
        import tempfile
        from datetime import datetime
        import monitor_and_trade as monitor
        from strategy import ALL_STRATEGIES, PORTFOLIO_E, PORTFOLIO_F
        positions = dict.fromkeys(ALL_STRATEGIES, 0)
        for code in PORTFOLIO_E[:2] + PORTFOLIO_F[:1]:
            positions[code] = 1
        state = dict(live_source_row_count=0, live_raw_positions=positions, live_target_position=0)
        rows = [dict(received_at="2026-09-15 22:30:16", strategy_code=PORTFOLIO_F[1],
                     previous_position="0", new_position="1"),
                dict(received_at="2026-09-15 22:30:36", strategy_code=PORTFOLIO_F[0],
                     previous_position="1", new_position="0"),
                dict(received_at="2026-09-15 22:30:56", strategy_code=PORTFOLIO_F[1],
                     previous_position="1", new_position="0")]
        def execute(target, **kwargs):
            checkpoint = kwargs.pop("on_submitted")
            prepared = kwargs.pop("on_prepared")
            delta = target - self.api.position
            prepared({"broker_before_position": self.api.position,
                      "broker_side": "buy" if delta > 0 else "sell" if delta < 0 else None,
                      "broker_quantity": abs(delta), "target_position": target})
            result = adapter.execute_target_position(target, api=self.api, sj=self.sj,
                                                     strict_tmf=True, submission_only=True, **kwargs)
            checkpoint(result)
            return result
        with tempfile.TemporaryDirectory() as directory, patch.object(
            monitor, "STATE_PATH", Path(directory) / "state.json"
        ), patch.object(monitor, "ORDER_ATTEMPT_PATH", Path(directory) / "orders.csv"), patch.object(
            monitor, "execute_target_position", side_effect=execute
        ), patch.object(monitor, "now_local", return_value=datetime(2026, 9, 15, 22, 31)), patch.object(
            monitor, "env_flag", return_value=True
        ), patch.object(monitor, "send_discord"), patch("builtins.print"):
            monitor.process_live_rows(state, rows, 2)
            persisted = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))
        self.assertEqual([(o.action, o.quantity) for o in self.api.orders], [("Buy", 1), ("Sell", 1)])
        self.assertEqual(persisted["live_source_row_count"], 3)
        self.assertEqual(persisted["live_target_position"], 0)
        # The middle hold signal performs a no-order reconciliation at +1.
        # The final sell is submission-only, so +1 remains the last confirmed inventory.
        self.assertEqual(persisted["last_confirmed_broker_position"], 1)
        self.assertEqual(persisted["attempt"]["status"], "submitted")

    def test_demo_submission_does_not_poll_or_claim_fill(self):
        self.api.status = "Submitted"
        self.api.update_status = Mock()
        original = self.api.place_order
        self.api.place_order = Mock(side_effect=original)
        result = adapter.execute_target_position(1, api=self.api, sj=self.sj,
            strict_tmf=True, submission_only=True, guard=self.guard)
        self.assertFalse(result.confirmed)
        self.assertIsNone(result.actual_position)
        self.assertEqual(self.api.place_order.call_args.kwargs["timeout"], 0)
        self.api.update_status.assert_called_once()  # Pre-order only.
        self.assertNotIn("pending", self.guard)

    def test_logout_termination_preserves_monitor_submission_without_false_fill(self):
        import tempfile
        from datetime import datetime
        import monitor_and_trade as monitor
        real_execute = auto_trade.execute_target_position
        self.api.logout = Mock(side_effect=SystemExit("native termination"))
        self.api.status = "Submitted"
        now = datetime(2026, 9, 18, 10)
        def execute(target, **kwargs):
            return real_execute(target, sj=self.sj, clock=lambda: now, **kwargs)
        state = {}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            monitor, "STATE_PATH", Path(folder) / "state.json"
        ), patch.object(monitor, "ORDER_ATTEMPT_PATH", Path(folder) / "orders.csv"), patch.object(
            monitor, "now_local", return_value=now
        ), patch.object(monitor, "env_flag", return_value=True), patch.object(
            monitor, "execute_target_position", side_effect=execute
        ), patch.object(auto_trade, "_login", return_value=self.api):
            with self.assertRaises(SystemExit):
                monitor.execute_live_target(state, 1, trigger="test-signal")
            restored = json.loads((Path(folder) / "state.json").read_text())
            self.assertEqual(restored["attempt"]["status"], "submitted")
            self.assertNotIn("last_confirmed_broker_position", restored)
            self.assertIn("不重送", monitor.execute_live_target(restored, 1, trigger="test-signal"))
            self.assertEqual(len(self.api.orders), 1)


if __name__ == "__main__":
    unittest.main()
