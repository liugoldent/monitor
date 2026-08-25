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
        self.api.set_order_callback.side_effect = self._set_order_callback
        self.api.place_order.side_effect = self._place_order
        self.sj = SimpleNamespace(
            constant=SimpleNamespace(
                Action=SimpleNamespace(Buy="Buy", Sell="Sell"),
                FuturesPriceType=SimpleNamespace(MKT="MKT"),
                OrderType=SimpleNamespace(IOC="IOC"),
                FuturesOCType=SimpleNamespace(Auto="Auto"),
            )
        )

    def _set_order_callback(self, callback):
        self.order_callback = callback

    def _place_order(self, _contract, order, timeout):
        self.order_callback(
            "FuturesOrder",
            {"operation": {"op_type": "New", "op_code": "00", "op_msg": ""}},
        )
        side = 1 if order["action"] == "Buy" else -1
        self.api.list_positions.return_value = [
            {"code": "TMFQ6", "direction": order["action"], "quantity": 2}
        ]
        return SimpleNamespace(
            status=SimpleNamespace(status="Filled", msg="", deal_quantity=order["quantity"])
        )

    def test_reversal_uses_difference_from_real_position(self):
        self.api.list_positions.return_value = [
            {"code": "TMFQ6", "direction": "Sell", "quantity": 1}
        ]

        result = auto_trade.execute_target_position(2, api=self.api, sj=self.sj)

        self.assertEqual(result.previous_position, -1)
        self.assertEqual(result.actual_position, 2)
        self.assertEqual(result.side, "buy")
        self.assertEqual(result.quantity, 3)
        self.api.Order.assert_called_once_with(
            action="Buy", price=0, quantity=3, price_type="MKT",
            order_type="IOC", octype="Auto", account="account",
        )
        placed_contract, placed_order = self.api.place_order.call_args.args
        self.assertEqual(placed_contract, "TMFR1")
        self.assertEqual(placed_order["quantity"], 3)
        self.assertEqual(
            self.api.place_order.call_args.kwargs,
            {"timeout": auto_trade.ORDER_TIMEOUT_MS},
        )

    def test_matching_position_does_not_place_order(self):
        self.api.list_positions.return_value = [
            {"code": "TMFQ6", "direction": "Buy", "quantity": 2},
            {"code": "MXFQ6", "direction": "Sell", "quantity": 9},
        ]

        result = auto_trade.execute_target_position(2, api=self.api, sj=self.sj)

        self.assertFalse(result.order_sent)
        self.api.place_order.assert_not_called()

    def test_action_enum_style_direction_is_supported(self):
        self.api.list_positions.return_value = [
            {"code": "TMFQ6", "direction": "Action.Sell", "quantity": 2}
        ]

        self.assertEqual(auto_trade.current_tmf_position(self.api), -2)

    def test_broker_rejection_raises_and_does_not_report_success(self):
        self.api.list_positions.return_value = []

        def rejected_order(_contract, _order, timeout):
            self.order_callback(
                "FuturesOrder",
                {
                    "operation": {
                        "op_type": "New",
                        "op_code": "99Q9",
                        "op_msg": "可委託金額不足",
                    }
                },
            )
            return SimpleNamespace(
                status=SimpleNamespace(status="Inactive", msg="", deal_quantity=0)
            )

        self.api.place_order.side_effect = rejected_order

        with self.assertRaisesRegex(auto_trade.BrokerOrderError, "99Q9.*可委託金額不足"):
            auto_trade.execute_target_position(-2, api=self.api, sj=self.sj)


if __name__ == "__main__":
    unittest.main()
