from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shane_account as account


class DefaultAccountTests(unittest.TestCase):
    def initialize(self, actual, expected=''):
        api = Mock()
        api.futopt_account = (SimpleNamespace(account_id=actual) if actual else None)
        sdk = SimpleNamespace(Shioaji=Mock(return_value=api))
        with patch.object(account, '_api', None), patch.object(
            account, 'credentials', return_value=('key', 'secret', 'person', expected, Path('test.pfx'))
        ), patch.dict(sys.modules, {'shioaji': sdk}):
            result = account.initialize()
            self.assertIs(result, api)
            sdk.Shioaji.assert_called_once_with(simulation=True)
            api.activate_ca.assert_called_once()

    def test_blank_expected_account_uses_default(self):
        self.initialize('simulation-account')

    def test_explicit_matching_account(self):
        self.initialize('expected', 'expected')

    def test_explicit_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '不符'):
            self.initialize('actual', 'expected')

    def test_missing_default_futures_account_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '沒有可用'):
            self.initialize(None)
