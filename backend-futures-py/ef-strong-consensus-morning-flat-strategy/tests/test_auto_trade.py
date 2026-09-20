from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auto_trade  # noqa: E402


class AutoTradeCredentialTests(unittest.TestCase):
    def test_login_uses_primary_api_credentials(self):
        api = Mock()
        sj = SimpleNamespace(Shioaji=Mock(return_value=api))
        with tempfile.TemporaryDirectory() as directory:
            ca_path = Path(directory) / "Sinopac.pfx"
            ca_path.touch()
            values = {
                "API_KEY": "primary-api-key",
                "SECRET_KEY": "primary-secret-key",
                "PERSON_ID": "A123456789",
                "CA_PATH": str(ca_path),
            }
            with patch.dict(os.environ, values, clear=True):
                result = auto_trade._login(sj)

        self.assertIs(result, api)
        api.login.assert_called_once_with("primary-api-key", "primary-secret-key")
        api.activate_ca.assert_called_once_with(
            ca_path=str(ca_path),
            ca_passwd="A123456789",
            person_id="A123456789",
        )

    def test_rejects_more_than_one_contract(self):
        with self.assertRaisesRegex(ValueError, "-1、0或1"):
            auto_trade.execute_target_position(2, api=Mock(), sj=Mock())

    def test_u_two_allows_two_contract_target(self):
        api = Mock()
        sj = Mock()
        contract = SimpleNamespace(code="TMFR1")
        trade = SimpleNamespace(
            status=SimpleNamespace(status="Submitted", msg="", deal_quantity=0),
            order=SimpleNamespace(id="order-1"),
        )
        api.place_order.return_value = trade
        with patch.dict(
            os.environ, {auto_trade.POSITION_UNIT_ENV: "2"}, clear=False
        ), patch.object(
            auto_trade, "current_tmf_position", return_value=1
        ), patch.object(
            auto_trade._shared, "_contract", return_value=contract
        ), patch.object(
            auto_trade._shared, "_build_order", return_value="order"
        ):
            result = auto_trade.execute_target_position(-2, api=api, sj=sj)
        self.assertEqual(result.previous_position, 1)
        self.assertEqual(result.target_position, -2)
        self.assertEqual(result.side, "sell")
        self.assertEqual(result.quantity, 3)
        self.assertFalse(result.confirmed)
        api.place_order.assert_called_once_with(contract, "order", timeout=0)

    def test_matching_inventory_needs_no_order_or_trade_scan(self):
        api = Mock()
        prepared = Mock()
        submitted = Mock()
        with patch.object(auto_trade, "current_tmf_position", return_value=-1), patch.object(
            auto_trade._shared, "_contract", return_value=SimpleNamespace(code="TMFR1")
        ), patch.object(auto_trade._shared, "execute_target_position") as legacy_execute:
            result = auto_trade.execute_target_position(
                -1, api=api, sj=Mock(), on_prepared=prepared, on_submitted=submitted
            )

        self.assertEqual(result.actual_position, -1)
        self.assertEqual(result.quantity, 0)
        prepared.assert_called_once()
        submitted.assert_called_once_with(result)
        api.place_order.assert_not_called()
        api.list_trades.assert_not_called()
        legacy_execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
