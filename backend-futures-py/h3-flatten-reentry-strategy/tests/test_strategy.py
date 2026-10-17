import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_trade
from h_signal import parse_h_direction, parse_h_event_direction
# The offline runtime may lack filelock; these tests do not run main or locking.
try:
    import filelock
except ImportError:
    with patch.dict(sys.modules, {"filelock": NS(FileLock=Mock())}):
        import monitor_and_trade as monitor
else:
    import monitor_and_trade as monitor
from strategy import parse_signal
from trade_records import entry_record, append_entry


class Broker:
    def __init__(self, position):
        self.position = position
        self.orders = []
        self.trades = []
        self.fail = False
        self.futopt_account = object()
        self.Contracts = NS(Futures=NS(TMF=NS(TMFR1=NS(code="TMFI6"))))
        self.Order = lambda **kw: NS(**kw)

    def list_positions(self, account):
        return [] if not self.position else [dict(code="TMFI6", quantity=abs(self.position),
                     direction="Buy" if self.position > 0 else "Sell")]

    def list_trades(self):
        return self.trades

    def update_status(self, *args, **kwargs):
        pass

    def place_order(self, contract, order, **kwargs):
        self.orders.append(order)
        filled = 0 if self.fail else order.quantity
        self.position += filled * (1 if order.action == "Buy" else -1)
        trade = NS(contract=contract, order=NS(id=str(len(self.orders))),
                   status=NS(status="Cancelled" if self.fail else "Filled", deal_quantity=filled))
        self.trades.append(trade)
        return trade


class Tests(unittest.TestCase):
    def test_entry_uses_first_contract_price_without_averaging(self):
        result = NS(side="buy", quantity=3, trade=NS(order=NS(id="order-1"),
                    status=NS(deals=[NS(price=22000, quantity=1, ts=1791075600),
                                     dict(price=22003, quantity=2, ts=1791075601)])))
        row = entry_record(result, "1:2", "recorded")
        self.assertEqual(row["entry_price"], 22000)
        self.assertEqual(row["fill_quantity"], 3)
        self.assertEqual(row["fill_status"], "complete")
        self.assertTrue(row["entry_time"].endswith("+08:00"))
        self.assertLess(row["entry_time"], row["last_fill_time"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "entries.csv"
            append_entry(path, row)
            self.assertIn("22000", path.read_text(encoding="utf-8"))

    def test_missing_fills_do_not_use_order_price_or_record_time(self):
        result = NS(side="sell", quantity=1, trade=NS(order=NS(price=0), status=NS(deals=[])))
        row = entry_record(result, "1:2", "recorded")
        self.assertEqual(row["entry_time"], "")
        self.assertEqual(row["entry_price"], "")
        self.assertEqual(row["fill_status"], "missing_fill_details")

    def test_partial_fill_details_do_not_claim_complete_price(self):
        result = NS(side="sell", quantity=3, trade=NS(status=NS(
                    deals=[NS(price=22000, quantity=1, ts=1791075600)])))
        row = entry_record(result, "1:2", "recorded")
        self.assertEqual(row["entry_price"], "")
        self.assertEqual(row["fill_quantity"], 1)

    def setUp(self):
        self.env = patch.dict(os.environ, {"H_UNIT": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sj = NS(constant=NS(Action=NS(Buy="Buy", Sell="Sell"),
                     FuturesPriceType=NS(MKT="MKT"), OrderType=NS(IOC="IOC"),
                     FuturesOCType=NS(Auto="Auto")))

    def execute(self, broker, direction):
        return auto_trade.execute_signal(direction, api=broker, sj=self.sj,
                    guard={}, persist=lambda: None, record=lambda *args: None)

    def test_parser(self):
        text = "期權醫生-浩克3\n浩克3V3訊號通知\n小型台指近一訊號部位為: 空1口\nFrom:"
        self.assertEqual(parse_signal(text), -1)
        self.assertEqual(parse_signal(text.replace("空1", "多 1")), 1)
        self.assertEqual(parse_signal(text.replace("空1", "空2")), -1)
        self.assertEqual(parse_signal("浩克3\n訊號通知\n多3口"), 1)
        self.assertIsNone(parse_signal("浩克3\n訊號通知\n多0口"))
        self.assertIsNone(parse_signal(text.replace("浩克3", "小H1")))

    def test_close_three_then_short_one(self):
        api = Broker(3)
        self.execute(api, -1)
        self.assertEqual([(o.action, o.quantity) for o in api.orders], [("Sell", 3), ("Sell", 1)])
        self.assertEqual(api.position, -1)

    def test_same_direction_and_unit(self):
        os.environ["H_UNIT"] = "2"
        api = Broker(3)
        self.execute(api, 1)
        self.assertEqual([(o.action, o.quantity) for o in api.orders], [("Sell", 3), ("Buy", 2)])

    def test_notification_quantity_does_not_multiply_unit(self):
        os.environ["H_UNIT"] = "2"
        api = Broker(3)
        self.execute(api, parse_signal("浩克3\n訊號通知\n空3口"))
        self.assertEqual([(o.action, o.quantity) for o in api.orders], [("Sell", 3), ("Sell", 2)])

    def test_bs_direction_uses_shared_parser_and_account_unit(self):
        self.assertIs(parse_signal, parse_h_direction)
        os.environ["H_UNIT"] = "2"
        for text, direction in (("(B=0 S=8)", -1), ("(B=9 S=0)", 1)):
            with self.subTest(text=text):
                api = Broker(3)
                self.execute(api, parse_signal(f"浩克3V3 交易訊號通知 {text}"))
                self.assertEqual([(o.action, o.quantity) for o in api.orders],
                                 [("Sell", 3), ("Buy" if direction > 0 else "Sell", 2)])


    def test_flat_account_only_enters(self):
        api = Broker(0)
        self.execute(api, 1)
        self.assertEqual([(o.action, o.quantity) for o in api.orders], [("Buy", 1)])

    def test_failed_flatten_never_enters(self):
        api = Broker(3)
        api.fail = True
        with patch.object(auto_trade.shared.time, "sleep"):
            with self.assertRaises(auto_trade.shared.BrokerOrderError):
                self.execute(api, -1)
        self.assertEqual(len(api.orders), 1)
        self.assertEqual(api.position, 3)

    def test_invalid_unit_does_not_flatten(self):
        api = Broker(3)
        os.environ["H_UNIT"] = "0"
        with self.assertRaises(ValueError):
            self.execute(api, -1)
        self.assertEqual(api.orders, [])








if __name__ == "__main__":
    unittest.main()
