"""Entry-policy integration tests: mocked broker, temporary state, no login."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import test_hedge as existing
import auto_trade
from entry_policy import entry_target, full_close_time


def trade_with_fills(stamps_and_quantities, status="Filled", trade_id="close"):
    return NS(order=NS(id=trade_id), status=NS(status=status,
        deal_quantity=sum(quantity for _, quantity in stamps_and_quantities),
        deals=[NS(seq=str(index), quantity=quantity,
                  ts=stamp.replace(tzinfo=ZoneInfo("Asia/Taipei")).timestamp())
               for index, (stamp, quantity) in enumerate(stamps_and_quantities)]))


class EntryGateTests(unittest.TestCase):
    def test_cutoff_boundary_both_sides_and_midnight(self):
        for direction in (-1, 1):
            for stamp, expected in (("2026-09-14T21:59:59", direction),
                                    ("2026-09-14T22:00:00", 0),
                                    ("2026-09-15T00:00:00", 0),
                                    ("2026-09-15T08:45:00", direction)):
                with self.subTest(direction=direction, stamp=stamp):
                    self.assertEqual(entry_target(direction, 0, datetime.fromisoformat(stamp), None)[0], expected)

    def test_holding_reducing_exiting_and_blocked_reversal(self):
        late = datetime(2026, 9, 14, 22, 10)
        for side in (-1, 1):
            self.assertEqual(entry_target(side, side, late, late, True)[0], side)
            self.assertEqual(entry_target(side, 2 * side, late, late, True)[0], side)
            self.assertEqual(entry_target(2 * side, side, late, None)[0], side)
            self.assertEqual(entry_target(0, side, late, late, True)[0], 0)
            self.assertEqual(entry_target(-side, side, late, None)[0], 0)

    def test_cooldown_boundary_shared_by_long_and_short(self):
        exit_at = datetime(2026, 9, 14, 19, 20, 8)
        for side in (-1, 1):
            self.assertEqual(entry_target(side, 0, exit_at + timedelta(minutes=15, seconds=-1), exit_at)[0], 0)
            self.assertEqual(entry_target(side, 0, exit_at + timedelta(minutes=15), exit_at)[0], side)
        self.assertEqual(entry_target(-1, 1, exit_at, None)[0], -1)

    def test_full_close_requires_enough_actual_fills_and_valid_times(self):
        stamp = datetime(2026, 9, 14, 19, 20)
        watch = dict(before=2, at=stamp.isoformat())
        self.assertIsNone(full_close_time(trade_with_fills([(stamp, 1)]), watch, stamp))
        later = stamp + timedelta(seconds=12)
        trade = trade_with_fills([(stamp, 1), (later, 1), (later + timedelta(seconds=1), 1)])
        self.assertEqual(full_close_time(trade, watch, later), later)
        self.assertIsNone(full_close_time(trade_with_fills([(later, 2)]), watch, stamp))
        self.assertIsNone(full_close_time(trade_with_fills([(stamp - timedelta(seconds=1), 2)]), watch, later))


class EntryPolicyMonitorTests(unittest.TestCase):
    setUp = existing.MonitorTests.setUp
    broker = existing.MonitorTests.broker
    monitor = existing.MonitorTests.monitor
    signal = existing.MonitorTests.signal

    def test_received_time_before_cutoff_can_execute_after_cutoff(self):
        self.now = datetime(2026, 9, 14, 21, 59)
        m = self.monitor()
        self.signal(0, 1, stamp="2026-09-14 21:59:40")
        self.now = datetime(2026, 9, 14, 22)
        m.tick()
        self.assertEqual(self.orders, [1])
        self.now += timedelta(minutes=10)
        self.signal(1, 1)
        m.tick()
        self.assertEqual(self.orders, [1])  # Cutoff never liquidates a valid holding.

    def test_after_cutoff_updates_json_and_notices_but_never_opens(self):
        self.now = datetime(2026, 9, 14, 22)
        m = self.monitor()
        m.notify = Mock()
        self.signal(0, -1)
        m.tick()
        self.assertEqual(self.orders, [])
        self.assertEqual(m.state["positions"]["CFC07m"], -1)
        self.assertEqual(m.state["attempt"]["raw_target_position"], -1)
        self.assertEqual(m.state["attempt"]["target_position"], 0)
        notice = m.notify.call_args.args[0]
        self.assertIn("帳戶目標口數：空手 0 口", notice)
        self.assertIn("22:00截止", notice)
        self.assertIn("Clamp目標：空手", notice)

    def test_actual_flat_after_cutoff_does_not_restore_json_target(self):
        m = self.monitor()
        self.signal(0, 1)
        m.tick()
        self.actual = 0  # Manual/broker exit, JSON still says long.
        self.now = datetime(2026, 9, 14, 22, 1)
        self.signal(0, 1, "CFCTX16m")
        m.tick()
        self.assertEqual(self.orders, [1])
        self.assertEqual(self.actual, 0)

    def test_after_cutoff_reversal_only_closes_and_0100_is_retained(self):
        m = self.monitor()
        self.signal(0, 1)
        m.tick()
        self.now = datetime(2026, 9, 14, 22, 10)
        self.signal(1, -1)
        m.tick()
        self.assertEqual(self.orders, [1, -1])
        self.assertEqual(self.actual, 0)
        self.actual = -2  # Independent abnormal inventory must still be flattened.
        self.now = datetime(2026, 9, 15, 1)
        m.tick()
        self.assertEqual(self.orders, [1, -1, 2])
        self.assertEqual(self.actual, 0)

    def test_cooldown_survives_restart_exact_boundary_and_no_auto_entry(self):
        m = self.monitor()
        self.signal(0, 1)
        m.tick()
        self.now += timedelta(seconds=10)
        self.signal(1, 0)
        m.tick()
        closed_at = self.now
        self.now += timedelta(minutes=14)
        m = self.monitor()
        self.signal(0, -1)
        m.tick()
        self.assertEqual(self.orders, [1, -1])
        self.assertEqual(m.state["entry_policy"]["last_exit_at"], closed_at.isoformat())
        self.now = closed_at + timedelta(minutes=15)
        m.tick()
        self.assertEqual(self.orders, [1, -1])  # Expiration has no side effects.
        self.signal(-1, -1)
        m.tick()
        self.assertEqual(self.orders, [1, -1, -1])

    def test_v9_cooldown_upgrade_preserves_exit_time_and_positions_without_new_wait(self):
        m = self.monitor()
        exited_at = self.now - timedelta(minutes=16)
        m.state["entry_policy"]["last_exit_at"] = exited_at.isoformat()
        m.state["entry_policy"]["last_exit_basis"] = "broker_fill"
        m.state["positions"]["CFC07m"] = 1
        m.persist()
        restarted = self.monitor()
        self.assertEqual(restarted.state["schema_version"], 9)
        self.assertEqual(restarted.state["entry_policy"]["last_exit_at"], exited_at.isoformat())
        self.assertEqual(restarted.state["positions"]["CFC07m"], 1)
        self.execute.assert_not_called()
        self.now += timedelta(seconds=1)
        self.signal(0, 1, "CFCTX17m")
        restarted.tick()
        self.assertEqual(self.orders, [1])

    def test_atomic_reversal_kept_but_following_reversal_obeys_cooldown(self):
        m = self.monitor()
        self.signal(0, 1)
        m.tick()
        self.now += timedelta(seconds=1)
        self.signal(1, -1)
        m.tick()
        self.assertEqual(self.orders, [1, -2])
        self.now += timedelta(seconds=1)
        self.signal(-1, 1)
        m.tick()
        self.assertEqual(self.orders, [1, -2, 1])
        self.assertEqual(self.actual, 0)

    def pending_exit(self, m):
        self.actual = 1
        m.state["positions"]["CFC07m"] = 1
        self.now += timedelta(seconds=1)
        self.signal(1, 0)
        def pending(target, **kwargs):
            target = kwargs["target_filter"](self.actual, self.now)
            kwargs["on_prepared"](dict(broker_before_position=self.actual,
                target_position=target, broker_quantity=1, broker_side="sell",
                broker_request_at=self.now.isoformat()))
            result = NS(submitted=True, side="sell", quantity=1,
                        previous_position=self.actual, target_position=target,
                        broker_status="PendingSubmit", broker_trade_id="pending")
            kwargs["on_submitted"](result)
            return result
        self.execute.side_effect = pending
        m.tick()

    def test_delayed_fill_uses_execution_time_not_submission_or_confirmation(self):
        m = self.monitor()
        self.pending_exit(m)
        submitted_at = self.now
        self.assertNotIn("last_exit_at", m.state["entry_policy"])
        self.assertEqual(len(m.state["entry_policy"]["pending_exits"]), 1)
        self.now += timedelta(minutes=2)
        filled_at = self.now
        self.actual = 0
        self.now += timedelta(minutes=3)
        m = self.monitor()
        m.exit_reader = lambda watches: [dict(id=w["id"], resolved=True,
            closed_at=filled_at.isoformat(), basis="broker_fill", actual_position=0,
            observed_at=self.now.isoformat()) for w in watches]
        self.execute.side_effect = self.broker
        m.tick()
        self.assertEqual(m.state["entry_policy"]["last_exit_at"], filled_at.isoformat())
        self.assertNotEqual(submitted_at, filled_at)
        self.now = submitted_at + timedelta(minutes=15)
        self.signal(0, -1)
        m.tick()
        self.assertEqual(self.orders, [])
        self.now = filled_at + timedelta(minutes=15)
        self.signal(-1, -1)
        m.tick()
        self.assertEqual(self.orders, [-1])

    def test_unconfirmed_close_survives_restart_and_blocks_opening(self):
        m = self.monitor()
        self.pending_exit(m)
        self.actual = 0
        self.now += timedelta(minutes=20)
        m = self.monitor()
        m.exit_reader = Mock(side_effect=TimeoutError())
        self.execute.side_effect = self.broker
        self.signal(0, -1)
        m.tick()
        self.assertEqual(self.orders, [])
        self.assertEqual(len(m.state["entry_policy"]["pending_exits"]), 1)
        self.assertIn("尚未確認", m.state["attempt"]["entry_block_reason"])

    def test_legacy_flat_without_fill_time_gets_one_conservative_cooldown(self):
        m = self.monitor()
        m.state.pop("entry_policy")
        m.state["schema_version"] = 8
        m.persist()
        self.now += timedelta(seconds=1)
        m = self.monitor()
        self.signal(0, 1)
        m.tick()
        self.assertEqual(self.orders, [])
        closed_at = m.state["entry_policy"]["last_exit_at"]
        m = self.monitor()
        self.assertEqual(m.state["entry_policy"]["last_exit_at"], closed_at)
        self.assertNotIn("initial_inventory_check", m.state["entry_policy"])

    def test_confirmed_new_cycle_resets_cooldown_and_waits_for_new_signal(self):
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 8, 44)
        m.state["entry_policy"]["last_exit_at"] = self.now.isoformat()
        m.persist()
        m.tick()  # Missed 05:05 reset is confirmed flat.
        self.assertNotIn("last_exit_at", m.state["entry_policy"])
        self.assertEqual(self.orders, [])
        self.now = datetime(2026, 9, 15, 8, 45)
        self.signal(0, 1)
        m.tick()
        self.assertEqual(self.orders, [1])

    def test_0100_still_closes_with_unconfirmed_exit_and_active_cooldown(self):
        m = self.monitor()
        self.pending_exit(m)
        m.exit_reader = Mock(side_effect=TimeoutError())
        m.state["entry_policy"]["last_exit_at"] = "2026-09-15T00:50:00"
        self.execute.side_effect = self.broker
        self.now = datetime(2026, 9, 15, 1)
        m.tick()
        self.assertEqual(self.orders, [-1])
        self.assertEqual(self.actual, 0)


class ExitEvidenceBrokerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 14, 19, 20)
        self.api = Mock()
        self.api.Contracts = NS(Futures=NS(TMF=NS(TMFR1=NS(code="TMFI6"))))
        self.api.list_positions.return_value = []
        self.watch = dict(id="watch", before=2, side="sell", trade_id="close", at=self.now.isoformat())

    def probe(self):
        return auto_trade.read_exit_status([self.watch], clock=lambda: self.now, api=self.api)[0]

    def test_partial_exit_does_not_start_cooldown_and_fill_time_closes_old_side(self):
        self.api.list_trades.return_value = [trade_with_fills([(self.now, 1)], "PartFilled")]
        self.api.list_positions.return_value = [dict(code="TMFI6", quantity=1, direction="Buy")]
        self.assertIsNone(self.probe()["closed_at"])
        initial = self.now
        self.now += timedelta(seconds=12)
        self.api.list_trades.return_value = [trade_with_fills([(initial, 1), (self.now, 1)])]
        filled_at = self.now
        self.api.list_positions.return_value = []
        self.now += timedelta(seconds=20)
        result = self.probe()
        self.assertEqual(result["closed_at"], filled_at.isoformat())
        self.assertTrue(result["resolved"])
        self.assertEqual(result["basis"], "broker_fill")
        self.api.place_order.assert_not_called()

    def test_executor_filters_against_actual_inventory_before_computing_order(self):
        self.api.list_positions.return_value = [dict(code="TMFI6", quantity=1, direction="Buy")]
        self.api.place_order.return_value = trade_with_fills([])
        sj = NS(constant=NS(Action=NS(Buy="Buy", Sell="Sell"),
            FuturesPriceType=NS(MKT="MKT"), OrderType=NS(IOC="IOC"), FuturesOCType=NS(Auto="Auto")))
        gate = Mock(return_value=0)
        result = auto_trade.execute_target_position(-1, deadline=self.now + timedelta(seconds=40),
            clock=lambda: self.now, api=self.api, sj=sj, target_filter=gate)
        gate.assert_called_once_with(1, self.now)
        self.assertEqual(result.target_position, 0)
        self.assertEqual(result.quantity, 1)  # Close only, no new opposite side.
        self.api.place_order.assert_called_once()

    def test_rejected_exit_does_not_create_cooldown(self):
        self.api.list_trades.return_value = [trade_with_fills([], "Failed")]
        self.api.list_positions.return_value = [dict(code="TMFI6", quantity=2, direction="Buy")]
        result = self.probe()
        self.assertTrue(result["resolved"])
        self.assertIsNone(result["closed_at"])

    def test_missing_time_uses_flat_confirmation_and_working_order_stays_unconfirmed(self):
        self.api.list_trades.return_value = [trade_with_fills([], "Cancelled")]
        self.assertEqual(self.probe()["basis"], "inventory_confirmation")
        self.api.list_trades.return_value = [trade_with_fills([], "PendingSubmit")]
        self.api.list_trades.return_value[0].contract = NS(code="TMFI6")
        with self.assertRaises(auto_trade.BrokerOrderError):
            self.probe()
        self.api.place_order.assert_not_called()

    def test_unknown_inventory_never_counts_as_flat(self):
        self.api.list_trades.return_value = []
        self.api.list_positions.return_value = None
        with self.assertRaises(auto_trade.BrokerOrderError):
            self.probe()


if __name__ == "__main__":
    unittest.main()
