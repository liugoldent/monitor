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
from monitor_and_trade import Monitor, webhook_url
import auto_trade
from backtest import run


class StrategyTests(unittest.TestCase):
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
        self.assertTrue(self.calendar.is_open(datetime(2026, 9, 25, 4, 59)))
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
            on_target(target)
        state = json.loads((self.root / "runtime/live_state.json").read_text())
        self.assertEqual(state["attempt"]["target"], target)
        self.assertEqual(state["attempt"]["status"], "pending")
        quantity = abs(target - previous)
        if quantity:
            self.orders.append(target - previous)
        self.actual = target
        if self.fail_after_fill:
            self.fail_after_fill = False
            raise auto_trade.BrokerOrderError("filled but response lost")
        return NS(previous_position=previous, actual_position=target, quantity=quantity,
                  side="buy" if target > previous else "sell")

    def monitor(self):
        return Monitor(root=self.root, live=True, executor=self.execute, clock=lambda: self.now)

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

    def test_first_post_start_reverse_uses_two_contracts(self):
        m = self.monitor()
        self.now += timedelta(seconds=1)
        self.signal(1, -1)
        m.tick()
        self.assertEqual(self.orders, [-2])

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

    def test_failure_retries_fixed_target_without_duplicate_fill(self):
        m = self.monitor()
        self.now += timedelta(seconds=1)
        self.signal(0, 1)
        self.fail_after_fill = True
        m.tick()
        self.now += timedelta(seconds=2)
        self.signal(0, 1, "CFCTX16m")
        m.tick()
        self.assertEqual(self.orders, [1])
        self.now += timedelta(seconds=8)
        m.tick()
        self.assertEqual(self.orders, [1, 1])
        self.assertEqual(self.actual, 3)
        self.assertEqual(self.execute.call_count, 3)

    def test_flat_on_clock_then_reopen_only_new_signals(self):
        m = self.monitor()
        self.actual = 4
        self.now = datetime(2026, 9, 15, 4, 59)
        with patch.object(m, "source", side_effect=AssertionError("flat cannot read CSV")):
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
        self.now = datetime(2026, 9, 15, 4, 59)
        self.monitor().tick()
        self.assertEqual(self.orders, [-1])

    def test_flat_overrides_failed_entry_wait(self):
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 4, 58, 59)
        self.signal(0, 1)
        self.fail_after_fill = True
        m.tick()
        self.now = datetime(2026, 9, 15, 4, 59)
        m.tick()
        self.assertEqual(self.actual, 0)
        self.assertEqual(self.orders, [1, -2])

    def test_failed_flat_retries_automatically(self):
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 4, 59)
        self.fail_after_fill = True
        m.tick()
        self.now += timedelta(seconds=10)
        m.tick()
        self.assertEqual(self.orders, [-1])
        self.assertEqual(m.state["attempt"]["status"], "done")

    def test_batch_does_not_cross_flat_boundary(self):
        m = self.monitor()
        self.now = datetime(2026, 9, 15, 4, 58, 59)
        for code in ("CFC07m", "CFCTX16m"):
            self.signal(0, 1, code)
        def slow(*args, **kwargs):
            result = self.broker(*args, **kwargs)
            self.now = datetime(2026, 9, 15, 4, 59)
            return result
        self.execute.side_effect = slow
        m.tick()
        self.assertEqual(self.orders, [1])
        m.tick()
        self.assertEqual(self.orders, [1, -2])



class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 15, 4, 59)
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

    def test_delta_based_on_real_position(self):
        self.api.list_positions.side_effect = [
            [{"code": "TMFI6", "quantity": 2, "direction": "Sell"}],
            [{"code": "TMFI6", "quantity": 2, "direction": "Sell"}],
            [{"code": "TMFI6", "quantity": 5, "direction": "Sell"}]]
        result = self.execute()
        self.assertEqual(result.quantity, 3)
        self.assertEqual(result.actual_position, -5)
        self.assertEqual(self.api.Order.call_args.kwargs["order_type"], "IOC")
        self.assertIs(self.api.place_order.call_args.args[0], self.contract)

    def test_six_transitions_order_side_and_quantity(self):
        def position(value):
            return ([{"code": "TMFI6", "quantity": abs(value),
                      "direction": "Buy" if value > 0 else "Sell"}] if value else [])
        for previous, target, side, quantity in (
            (-1, 0, "buy", 1), (1, 0, "sell", 1),
            (0, 1, "buy", 1), (0, -1, "sell", 1),
            (1, -1, "sell", 2), (-1, 1, "buy", 2),
        ):
            with self.subTest(previous=previous, target=target):
                self.api.list_positions.side_effect = [position(previous), position(previous), position(target)]
                result = self.execute(target)
                self.assertEqual((result.side, result.quantity), (side, quantity))
                self.assertEqual(self.api.Order.call_args.kwargs["quantity"], quantity)

    def test_signal_delta_resolves_from_real_inventory_and_persists_before_order(self):
        previous = [{"code": "TMFI6", "quantity": 1, "direction": "Buy"}]
        final = [{"code": "TMFI6", "quantity": 2, "direction": "Buy"}]
        self.api.list_positions.side_effect = [previous, previous, previous, previous, final]
        resolved = []
        self.api.place_order.side_effect = lambda *a, **k: (
            self.assertEqual(resolved, [2]) or NS(status=NS(status="Filled")))
        result = auto_trade.execute_target_position(None, delta=1, on_target=resolved.append,
            deadline=self.now + timedelta(seconds=40), clock=lambda: self.now,
            api=self.api, sj=self.sj)
        self.assertEqual(result.quantity, 1)
        self.assertEqual(result.actual_position, 2)

    def test_signal_target_save_failure_never_sends_order(self):
        with self.assertRaises(OSError):
            auto_trade.execute_target_position(None, delta=1,
                on_target=Mock(side_effect=OSError("disk full")),
                deadline=self.now + timedelta(seconds=40), clock=lambda: self.now,
                api=self.api, sj=self.sj)
        self.api.place_order.assert_not_called()

    def test_flat_uses_actual_quantity(self):
        self.api.list_positions.side_effect = [
            [{"code": "TMFI6", "quantity": 6, "direction": "Sell"}],
            [{"code": "TMFI6", "quantity": 6, "direction": "Sell"}], []]
        result = self.execute(0)
        self.assertEqual(result.quantity, 6)
        self.assertEqual(result.side, "buy")

    def test_existing_target_no_order(self):
        self.api.list_positions.return_value = [{"code": "TMFI6", "quantity": 5, "direction": "Sell"}]
        self.assertEqual(self.execute().quantity, 0)
        self.api.place_order.assert_not_called()

    def test_uses_shared_executor_and_near_month(self):
        with patch.object(auto_trade._shared, "execute_target_position", return_value=NS(actual_position=3)) as shared:
            result = self.execute(3)
            self.assertEqual(result.actual_position, 3)
            self.assertEqual(shared.call_args.args, (3,))
            self.assertIs(shared.call_args.kwargs["api"], self.api)
            self.assertTrue(callable(shared.call_args.kwargs["before_order"]))

    def test_callback_rejection_is_not_success(self):
        def install(callback):
            callback(None, {"operation": {"op_type": "New", "op_code": "99", "op_msg": "rejected"}})
        self.api.set_order_callback.side_effect = install
        with self.assertRaisesRegex(auto_trade.BrokerOrderError, "拒絕委託"):
            self.execute()

    def test_other_month_blocked(self):
        self.api.list_positions.return_value = [{"code": "TMFJ6", "quantity": 1, "direction": "Buy"}]
        with self.assertRaises(auto_trade.BrokerOrderError):
            self.execute()
        self.api.place_order.assert_not_called()

    def test_pending_order_blocked(self):
        self.api.list_trades.return_value = [NS(contract=self.contract, status=NS(status="Submitted"))]
        with self.assertRaises(auto_trade.BrokerOrderError):
            self.execute()
        self.api.place_order.assert_not_called()

    def test_gross_long_short_is_not_treated_as_flat(self):
        self.api.list_positions.return_value = [
            {"code": "TMFI6", "quantity": 1, "direction": "Buy"},
            {"code": "TMFI6", "quantity": 1, "direction": "Sell"}]
        with self.assertRaises(auto_trade.BrokerOrderError):
            self.execute(0)
        self.api.place_order.assert_not_called()

    def test_query_latency_cannot_place_after_deadline(self):
        def slow_query(*args):
            self.now += timedelta(seconds=45)
            return []
        self.api.list_positions.side_effect = slow_query
        with self.assertRaises(auto_trade.BrokerOrderError):
            self.execute()
        self.api.place_order.assert_not_called()

    def test_partial_fill_is_not_success(self):
        self.api.list_positions.return_value = []
        with patch.object(auto_trade._shared, "POSITION_VERIFY_ATTEMPTS", 1):
            with self.assertRaises(auto_trade.BrokerOrderError):
                self.execute()
        self.assertEqual(self.api.place_order.call_count, 1)

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
                "MXF1!,2026-09-15 04:59:00,2026-09-15 05:00:00,10100\n"
                "MXF1!,2026-09-15 08:47:00,2026-09-15 08:48:00,10200\n")
            args = (signals, prices, Calendar.load(BASE / "config/calendar.json"),
                    datetime(2026, 9, 14, 22), datetime(2026, 9, 15, 9))
            result = run(*args)
            self.assertEqual([row["target"] for row in result["ledger"]], [1, 0, -1])
            self.assertEqual(result["net_twd"], 940)
            prices.write_text(prices.read_text().replace("04:59:00", "04:58:00"))
            with self.assertRaisesRegex(ValueError, "缺少精確"):
                run(*args)


if __name__ == "__main__":
    unittest.main()
