"""Explicit API_KEY2 session. Importing or checking config never logs in."""
import os
from pathlib import Path

import protection_bootstrap
from protection_bootstrap import BACKEND
import shioaji_tmf_target as shared
from broker import PyramidBroker
from h_core import TradingError

# Explicit broker mode; environment flags do not override this selection.
BROKER_SIMULATION = False
_api = _sj = None


def required(name):
    value = os.getenv(name, '').strip()
    if not value:
        raise ValueError('缺少設定：'+name)
    return value


def position_unit():
    value = required('H_UNIT_KEY2')
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise ValueError('H_UNIT_KEY2 必須是正整數')
    return int(value)


def credentials():
    key, secret = required('API_KEY2'), required('SECRET_KEY2')
    if len(key) < 10:
        raise ValueError('API_KEY2 格式無效')
    # Same person's CA may be reused, never another account's API credentials.
    person = os.getenv('PERSON_ID2') or required('PERSON_ID')
    ca = Path(os.getenv('CA_PATH2') or os.getenv('CA_PATH') or BACKEND / 'Sinopac.pfx')
    if not ca.is_file():
        raise ValueError('找不到永豐2憑證')
    return key, secret, person, ca


def initialize():
    global _api, _sj
    if _api is not None:
        return _api, _sj
    key, secret, person, ca = credentials()
    import shioaji as sj
    api = sj.Shioaji(simulation=BROKER_SIMULATION)
    try:
        api.login(key, secret)
        expected_account = required('H3_PROTECTION_ACCOUNT_ID')
        actual_account = str(shared._position_value(api.futopt_account, 'account_id'))
        if actual_account != expected_account:
            raise ValueError('API_KEY2 期貨帳號與 H3_PROTECTION_ACCOUNT_ID 不符')
        api.activate_ca(ca_path=str(ca), ca_passwd=person, person_id=person)
    except Exception:
        try:
            api.logout()
        except Exception:
            pass
        raise
    _api, _sj = api, sj
    return api, sj


def close():
    global _api, _sj
    api, _api, _sj = _api, None, None
    if api is not None:
        try:
            api.logout()
        except Exception:
            print('永豐2舊連線登出失敗，已清除快取', flush=True)


class ProtectionBroker(PyramidBroker):
    account_label = 'API_KEY2'

    def __init__(self, *args, **kwargs):
        self.unit = position_unit()
        super().__init__(*args, **kwargs)

    def valid_transition(self, previous, delta, phase, target):
        return ((phase == 'entry' and previous == 0 and abs(target) == abs(delta) == self.unit)
                or (phase == 'flatten' and target == 0 and previous != 0 and delta == -previous))
