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


class AutoTradeTests(unittest.TestCase):
    def test_login_uses_second_account_credentials(self):
        api = Mock()
        sj = SimpleNamespace(Shioaji=Mock(return_value=api))
        with tempfile.TemporaryDirectory() as directory:
            ca_path = Path(directory) / "Sinopac.pfx"
            ca_path.touch()
            with patch.dict(os.environ, {
                "API_KEY2": "second-key",
                "SECRET_KEY2": "second-secret",
                "PERSON_ID": "A123456789",
                "CA_PATH": str(ca_path),
            }, clear=True):
                result = auto_trade._login(sj)
        self.assertIs(result, api)
        api.login.assert_called_once_with("second-key", "second-secret")

    def test_rejects_more_than_one_contract(self):
        with self.assertRaisesRegex(ValueError, "-1、0或1"):
            auto_trade.execute_target_position(2, api=Mock(), sj=Mock())

    def test_u_two_allows_two_contract_target(self):
        api = Mock()
        sj = Mock()
        expected = Mock()
        with patch.dict(
            os.environ, {auto_trade.POSITION_UNIT_ENV: "2"}, clear=False
        ), patch.object(
            auto_trade._shared, "execute_target_position", return_value=expected
        ) as execute:
            result = auto_trade.execute_target_position(2, api=api, sj=sj)
        self.assertIs(result, expected)
        execute.assert_called_once_with(2, api=api, sj=sj)


if __name__ == "__main__":
    unittest.main()
