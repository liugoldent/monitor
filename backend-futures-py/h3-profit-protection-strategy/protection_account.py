"""Explicit API_KEY2 session. Importing or checking config never logs in."""
import os
from pathlib import Path

import protection_bootstrap
from protection_bootstrap import BACKEND
import shioaji_tmf_target as shared
from broker import PyramidBroker
from h_core import TradingError
from trade_records import entry_record
from protection_policy import number

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
        self.entry_quantity = self.unit
        self.confirmed_price = None
        super().__init__(*args, **kwargs)

    def valid_transition(self, previous, delta, phase, target):
        return ((phase == 'entry' and previous == 0
                 and self.entry_quantity in (self.unit, self.unit + 1)
                 and abs(target) == abs(delta) == self.entry_quantity)
                or (phase == 'flatten' and target == 0 and previous != 0 and delta == -previous))

    def send_and_confirm(self, delta, phase, target):
        self.confirmed_price = None
        actual = super().send_and_confirm(delta, phase, target)
        # Resolve only the exact confirmed order, never a quote or latest trade.
        evidence = self.guard.get('last_resolved', {})
        if (type(evidence.get('previous_position')) is not int
                or evidence['previous_position'] + delta != target
                or evidence.get('target_position') != target
                or evidence.get('quantity') != abs(delta)):
            raise TradingError('永豐2成交證據與連輸帳本委託不符，保留 guard')
        shared._refresh_status(self.api)
        trades = self.api.list_trades()
        matches = [t for t in trades if shared._trade_id(t) == evidence.get('trade_id')] if isinstance(trades, list) else []
        if len(matches) != 1:
            raise TradingError('永豐2缺少唯一成交委託，不能更新連輸帳本')
        trade = matches[0]
        shared._refresh_status(self.api, trade=trade)
        side = 'buy' if delta > 0 else 'sell'
        fill = entry_record(shared.OrderResult(evidence['previous_position'], target, actual,
                            side, abs(delta), trade), self.store.pending()['trigger'], '')
        if fill['fill_status'] != 'complete':
            raise TradingError('永豐2缺少完整成交明細，保留 guard，不推估損益')
        self.confirmed_price = sum(number(f['price'])*f['quantity'] for f in fill['fills']) / abs(delta)
        return actual
