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
from strategy import Calendar, STRATEGIES, hedge_target, signal_position, snapshot_position
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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.csv"
        self.rows = [{"received_at": "2026-09-14 10:00:00", "strategy_code": code,
                      "previous_position": 0, "new_position": 1} for code in STRATEGIES]

    def write(self):
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.rows[0].keys())
            writer.writeheader()
            writer.writerows(self.rows)

    def test_all_legs_and_future_exclusion(self):
        self.rows += [dict(self.rows[0], received_at="2026-09-15 05:00:00", new_position=-1)]
        self.write()
        self.assertEqual(signal_position(self.path, datetime(2026, 9, 15, 4, 59))["net_position"], 12)

    def test_missing_and_undated_do_not_default_flat(self):
        self.rows[0]["received_at"] = ""
        self.write()
        with self.assertRaisesRegex(ValueError, "缺少"):
            signal_position(self.path, datetime(2026, 9, 15))

    def test_alias_and_mismatch_audit(self):
        self.rows[6]["strategy_code"] = "CFCWN01m"
        self.rows += [dict(self.rows[0], received_at="2026-09-14 11:00:00", new_position=-1)]
        self.write()
        result = signal_position(self.path, datetime(2026, 9, 15))
        self.assertEqual(result["net_position"], 10)
        self.assertEqual(result["mismatches"], 1)

    def test_snapshot_freshness_and_timezone(self):
        self.path.write_text(json.dumps({"source": "capital_pure_ef", "observed_at": "2026-09-14T20:58:00Z",
                                         "net_position": -7, "contract": "TMFI6"}))
        self.assertEqual(snapshot_position(self.path, datetime(2026, 9, 15, 4, 59))["net_position"], -7)
        for now in (datetime(2026, 9, 15, 4, 57), datetime(2026, 9, 15, 5, 1)):
            with self.assertRaises(ValueError):
                snapshot_position(self.path, now)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = datetime(2026, 9, 12, 4, 59)
        self.source = Mock(return_value={"net_position": 7, "source": "capital_pure_ef", "contract": "TMFI6"})
        self.orders = []
        self.notify = Mock()

        def execute(target, **kwargs):
            # Verify intent is durably recorded before calling the broker.
            state = json.loads((self.root / "runtime/live_state.json").read_text())
            self.assertEqual(state["attempt"]["status"], "pending")
            self.orders.append((target, kwargs))
            return NS(actual_position=target, quantity=abs(target))
        self.execute = Mock(side_effect=execute)

    def monitor(self, live=True):
        return Monitor(root=self.root, live=live, source=self.source, executor=self.execute,
                       notify=self.notify, clock=lambda: self.now)

    def test_weekend_latch_restart_release(self):
        monitor = self.monitor()
        monitor.tick()
        self.assertEqual(self.orders[0][0], -6)
        self.now = datetime(2026, 9, 12, 4, 59, 20)
        monitor.tick()
        restarted = self.monitor()
        restarted.tick()
        self.now = datetime(2026, 9, 13, 8, 45)
        restarted.tick()
        self.assertEqual(len(self.orders), 1)
        self.now = datetime(2026, 9, 14, 8, 45)
        restarted.tick()
        self.assertEqual([x[0] for x in self.orders], [-6, 0])
        self.assertEqual(self.source.call_count, 1)

    def test_weekday_target_and_latched_contract(self):
        self.now = datetime(2026, 9, 15, 4, 59)
        monitor = self.monitor()
        monitor.tick()
        self.assertEqual(self.orders[0][0], -5)
        os.environ["EF_HEDGE_CONTRACT"] = "TMFJ6"
        self.now = datetime(2026, 9, 15, 8, 45)
        self.monitor().tick()
        self.assertEqual(self.orders[-1][1]["contract_code"], "TMFI6")

    def test_late_start_never_opens(self):
        for stamp in (datetime(2026, 9, 12, 4, 59, 40), datetime(2026, 9, 12, 5, 0)):
            self.now = stamp
            self.monitor().tick()
        self.execute.assert_not_called()

    def test_failed_entry_no_retries_but_exit_attempt(self):
        self.execute.side_effect = RuntimeError("uncertain")
        monitor = self.monitor()
        monitor.tick()
        monitor.tick()
        self.monitor().tick()
        self.assertEqual(self.execute.call_count, 1)
        self.now = datetime(2026, 9, 14, 8, 45)
        monitor.tick()
        monitor.tick()
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual(monitor.state["attempt"]["status"], "failed")

    def test_crash_after_intent_never_repeats(self):
        monitor = self.monitor()
        self.execute.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            monitor.tick()
        self.monitor().tick()
        self.assertEqual(self.execute.call_count, 1)

    def test_persist_failure_prevents_orders(self):
        monitor = self.monitor()
        with patch("monitor_and_trade.save", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                monitor.tick()
        self.execute.assert_not_called()

    def test_shadow_does_not_execute_and_has_separate_state(self):
        self.monitor(live=False).tick()
        self.execute.assert_not_called()
        self.assertTrue((self.root / "runtime/shadow_state.json").exists())
        self.monitor(live=True).tick()
        self.assertEqual(self.execute.call_count, 1)

    def test_noon_does_not_create_hedge(self):
        self.now = datetime(2026, 9, 15, 13, 44)
        self.monitor().tick()
        self.source.assert_not_called()
        self.assertEqual(self.orders[0][0], 0)

    def test_cap_breach_does_not_send_truncated_hedge(self):
        self.source.return_value["net_position"] = 30
        with self.assertRaises(ValueError):
            self.monitor().tick()
        self.execute.assert_not_called()

    def test_calendar_update_postpones_exit(self):
        monitor = self.monitor()
        monitor.tick()
        config = json.loads((BASE / "config/calendar.json").read_text())
        config["closed_dates"].append("2026-09-14")
        calendar_path = self.root / "calendar.json"
        calendar_path.write_text(json.dumps(config))
        monitor.calendar_path = calendar_path
        self.now = datetime(2026, 9, 14, 8, 45)
        monitor.tick()
        self.assertEqual(len(self.orders), 1)
        self.now = datetime(2026, 9, 15, 8, 45)
        monitor.tick()
        self.assertEqual(self.orders[-1][0], 0)

    def test_csv_live_requires_explicit_source_acceptance(self):
        with self.assertRaisesRegex(ValueError, "尚未確認"):
            self.monitor().read_source(self.now)

    def test_webhook_exact_name_no_fallback(self):
        os.environ["DISCORD_WEBHOOK_URL"] = "wrong"
        self.assertEqual(webhook_url(), "")
        os.environ["DISCORD_EF_hedge_WEBHOOK_URL"] = "correct"
        self.assertEqual(webhook_url(), "correct")


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 15, 4, 59)
        self.contract = NS(code="TMFI6")
        self.api = Mock()
        self.api.Contracts = NS(Futures=NS(TMF={"TMFI6": self.contract}))
        self.api.list_trades.return_value = []
        self.api.list_positions.return_value = []
        self.api.place_order.return_value = NS(status=NS(status="Filled"))
        self.sj = NS(constant=NS(Action=NS(Buy="Buy", Sell="Sell"),
                               FuturesPriceType=NS(MKT="MKT"), OrderType=NS(IOC="IOC"),
                               FuturesOCType=NS(Auto="Auto")))

    def execute(self, target=-5, **kwargs):
        return auto_trade.execute_target_position(target, contract_code="TMFI6",
            deadline=self.now + timedelta(seconds=40), clock=lambda: self.now,
            api=self.api, sj=self.sj, **kwargs)

    def test_delta_based_on_real_position(self):
        self.api.list_positions.side_effect = [
            [{"code": "TMFI6", "quantity": 2, "direction": "Sell"}],
            [{"code": "TMFI6", "quantity": 5, "direction": "Sell"}]]
        result = self.execute()
        self.assertEqual(result.quantity, 3)
        self.assertEqual(result.actual_position, -5)
        self.assertEqual(self.api.Order.call_args.kwargs["order_type"], "IOC")
        self.assertIs(self.api.place_order.call_args.args[0], self.contract)

    def test_flat_uses_actual_quantity(self):
        self.api.list_positions.side_effect = [
            [{"code": "TMFI6", "quantity": 6, "direction": "Sell"}], []]
        result = self.execute(0)
        self.assertEqual(result.quantity, 6)
        self.assertEqual(result.side, "buy")

    def test_existing_target_no_order(self):
        self.api.list_positions.return_value = [{"code": "TMFI6", "quantity": 5, "direction": "Sell"}]
        self.assertEqual(self.execute().quantity, 0)
        self.api.place_order.assert_not_called()

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
                del os.environ["API_KEY2"]
                with self.assertRaisesRegex(ValueError, "API_KEY2"):
                    auto_trade.login(sj)


class BacktestTests(unittest.TestCase):
    def test_exact_prices_and_missing_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            prices = Path(folder) / "prices.csv"
            prices.write_text("Symbol,TradingView Time,Record Time,Open\n"
                              "MXF1!,2026-09-12 04:59:00,2026-09-12 05:00:00,10000\n"
                              "MXF1!,2026-09-14 08:45:00,2026-09-14 08:46:00,9000\n")
            calendar = Calendar.load(BASE / "config/calendar.json")
            with patch("backtest.signal_position", return_value={"net_position": 7, "mismatches": 0}):
                result = run(Path("unused"), prices, calendar, datetime(2026, 9, 12, 4, 59),
                             datetime(2026, 9, 14, 9))
                self.assertEqual(result["ledger"][0]["hedge"], -6)
                self.assertEqual(result["net_twd"], (6000 - 24) * 10)
                prices.write_text(prices.read_text().replace("08:45:00", "08:46:00"))
                result = run(Path("unused"), prices, calendar, datetime(2026, 9, 12, 4, 59),
                             datetime(2026, 9, 14, 9))
                self.assertEqual(len(result["ledger"]), 0)
                self.assertEqual(len(result["skipped"]), 1)


if __name__ == "__main__":
    unittest.main()
