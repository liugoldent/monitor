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
    def test_login_uses_second_api_credentials(self):
        api = Mock()
        sj = SimpleNamespace(Shioaji=Mock(return_value=api))
        with tempfile.TemporaryDirectory() as directory:
            ca_path = Path(directory) / "Sinopac.pfx"
            ca_path.touch()
            values = {
                "API_KEY2": "second-api-key",
                "SECRET_KEY2": "second-secret-key",
                "PERSON_ID": "A123456789",
                "CA_PATH": str(ca_path),
            }
            with patch.dict(os.environ, values, clear=True):
                result = auto_trade._login(sj)

        self.assertIs(result, api)
        sj.Shioaji.assert_called_once_with(simulation=False)
        api.login.assert_called_once_with("second-api-key", "second-secret-key")
        api.activate_ca.assert_called_once_with(
            ca_path=str(ca_path),
            ca_passwd="A123456789",
            person_id="A123456789",
        )

    def test_reconciliation_uses_full_target(self):
        api = Mock()
        sj = Mock()
        expected = Mock()
        with patch.object(
            auto_trade._shared,
            "execute_target_position",
            return_value=expected,
        ) as execute:
            result = auto_trade.execute_target_position(7, api=api, sj=sj)

        self.assertIs(result, expected)
        execute.assert_called_once_with(7, api=api, sj=sj)


if __name__ == "__main__":
    unittest.main()
