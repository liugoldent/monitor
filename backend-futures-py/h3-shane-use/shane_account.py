"""Shane's isolated Shioaji login and configured-unit broker."""
import os
from pathlib import Path

import shane_bootstrap
from shane_bootstrap import BASE, BACKEND
from broker import PyramidBroker
import shioaji_tmf_target as shared

_api = None
BROKER_SIMULATION = True


def required(name):
    value = os.getenv(name, '').strip()
    if not value:
        raise ValueError('h3-shane-use 缺少設定：' + name)
    return value


def credentials():
    key, secret = required('API_KEY'), required('SECRET_KEY')
    if len(key) < 10:
        raise ValueError('h3-shane-use API_KEY 格式無效')
    person = required('PERSON_ID')
    account_id = os.getenv('H3_SHANE_ACCOUNT_ID', '').strip()
    ca = Path(required('CA_PATH'))
    if not ca.is_absolute():
        ca = BASE / ca
    if not ca.is_file():
        raise ValueError('h3-shane-use 找不到永豐憑證')
    return key, secret, person, account_id, ca


def position_unit():
    value = required('SHANE_UNIT')
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise ValueError('h3-shane-use SHANE_UNIT 必須是正整數')
    return int(value)


def initialize():
    global _api
    if _api is not None:
        return _api
    key, secret, person, account_id, ca = credentials()
    import shioaji as sj
    api = sj.Shioaji(simulation=BROKER_SIMULATION)
    try:
        api.login(key, secret)
        actual = shared._position_value(api.futopt_account, 'account_id')
        if not actual:
            raise ValueError('h3-shane-use 登入後沒有可用的預設期貨帳戶')
        if account_id and str(actual) != account_id:
            raise ValueError('h3-shane-use 期貨帳號與 H3_SHANE_ACCOUNT_ID 不符')
        api.activate_ca(ca_path=str(ca), ca_passwd=person, person_id=person)
    except Exception:
        try:
            api.logout()
        except Exception:
            pass
        raise
    _api = api
    return api


def close():
    global _api
    api, _api = _api, None
    if api is not None:
        try:
            api.logout()
        except Exception:
            pass


class ShaneBroker(PyramidBroker):
    account_label = 'H3_SHANE'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unit = position_unit()
        self.entry_quantity = self.unit

    def valid_transition(self, previous, delta, phase, target):
        return ((phase == 'flatten' and previous != 0 and target == 0 and delta == -previous)
                or (phase == 'entry' and previous == 0
                    and abs(target) == self.unit and delta == target))
