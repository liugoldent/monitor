from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auto_trade  # noqa: E402


class AutoTradeTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.futopt_account = "account"
        self.api.Contracts.Futures.TMF.TMFR1 = "TMFR1"
        self.api.Order.side_effect = lambda **values: values
        self.sj = SimpleNamespace(
            constant=SimpleNamespace(
                Action=SimpleNamespace(Buy="Buy", Sell="Sell"),
                FuturesPriceType=SimpleNamespace(MKT="MKT"),
                OrderType=SimpleNamespace(IOC="IOC"),
                FuturesOCType=SimpleNamespace(Auto="Auto"),
            )
        )

    def test_reversal_uses_difference_from_real_position(self):
        self.api.list_positions.return_value = [
            {"code": "TMFQ6", "direction": "Sell", "quantity": 1}
        ]

        result = auto_trade.execute_target_position(2, api=self.api, sj=self.sj)

        self.assertEqual(result.previous_position, -1)
        self.assertEqual(result.side, "buy")
        self.assertEqual(result.quantity, 3)
        self.api.Order.assert_called_once_with(
            action="Buy", price=0, quantity=3, price_type="MKT",
            order_type="IOC", octype="Auto", account="account",
        )
        placed_contract, placed_order = self.api.place_order.call_args.args
        self.assertEqual(placed_contract, "TMFR1")
        self.assertEqual(placed_order["quantity"], 3)
        self.assertEqual(self.api.place_order.call_args.kwargs, {"timeout": 0})

    def test_matching_position_does_not_place_order(self):
        self.api.list_positions.return_value = [
            {"code": "TMFQ6", "direction": "Buy", "quantity": 2},
            {"code": "MXFQ6", "direction": "Sell", "quantity": 9},
        ]

        result = auto_trade.execute_target_position(2, api=self.api, sj=self.sj)

        self.assertFalse(result.order_sent)
        self.api.place_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
