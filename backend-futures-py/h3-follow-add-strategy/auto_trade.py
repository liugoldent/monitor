"""H3 guerrilla strategy's process-long API_KEY production session."""
from __future__ import annotations

import os
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import shioaji_tmf_target as shared

_api = None
_sj = None
_failed_apis = []


def required(name):
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError(f'缺少必要環境變數: {name}')
    return value


def position_unit():
    unit = int(os.getenv('H_UNIT', '1'))
    if unit < 1:
        raise ValueError('H_UNIT 必須是正整數')
    return unit


def initialize_broker_session():
    global _api, _sj
    if _api is not None:
        return _api
    # Account selection is explicit. Never fall back to another numbered key.
    key, secret, person = required('API_KEY'), required('SECRET_KEY'), required('PERSON_ID')
    path = Path(os.getenv('CA_PATH') or BACKEND / 'Sinopac.pfx')
    if not path.is_file():
        raise FileNotFoundError('找不到永豐憑證檔，請確認 CA_PATH')
    import shioaji as sj
    api = sj.Shioaji(simulation=False)
    try:
        api.login(key, secret)
        api.activate_ca(ca_path=str(path), ca_passwd=person, person_id=person)
    except Exception:
        _failed_apis.append(api)
        raise
    _api, _sj = api, sj
    return api


def close_broker_session():
    """Discard closed sessions so the next retry performs a fresh login."""
    global _api, _sj
    sessions = list(_failed_apis)
    _failed_apis.clear()
    if _api is not None:
        sessions.append(_api)
    _api, _sj = None, None
    for api in sessions:
        try:
            api.logout()
        except Exception:
            print('永豐舊連線登出失敗，已清除快取，稍後重新連線', flush=True)
