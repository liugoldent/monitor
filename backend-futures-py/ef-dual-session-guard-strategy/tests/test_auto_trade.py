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


class DedicatedCredentialTests(unittest.TestCase):
    def test_login_requires_dedicated_strategy_credentials(self):
        api = Mock()
        sj = SimpleNamespace(Shioaji=Mock(return_value=api))
        with tempfile.TemporaryDirectory() as directory:
            ca_path = Path(directory) / "Sinopac.pfx"
            ca_path.touch()
            env = {
                "EF_DUAL_SESSION_API_KEY": "dual-api",
                "EF_DUAL_SESSION_SECRET_KEY": "dual-secret",
                "PERSON_ID": "A123456789",
                "CA_PATH": str(ca_path),
                "API_KEY": "must-not-be-used",
                "API_KEY2": "must-not-be-used-either",
            }
            with patch.dict(os.environ, env, clear=True):
                result = auto_trade._login(sj)

        self.assertIs(result, api)
        api.login.assert_called_once_with("dual-api", "dual-secret")

    def test_target_is_limited_to_ten_contracts(self):
        with self.assertRaises(ValueError):
            auto_trade.validate_target(11)
        self.assertEqual(auto_trade.validate_target(-10), -10)

    def test_reconciliation_uses_full_net_target(self):
        api = Mock()
        sj = Mock()
        expected = Mock()
        with patch.object(
            auto_trade._shared, "execute_target_position", return_value=expected
        ) as execute:
            result = auto_trade.execute_target_position(-7, api=api, sj=sj)
        self.assertIs(result, expected)
        execute.assert_called_once_with(-7, api=api, sj=sj)


if __name__ == "__main__":
    unittest.main()
