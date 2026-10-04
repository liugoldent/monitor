"""Account 3, confirmed flatten followed by a separate TMFR1 entry."""
import os
import sys
from pathlib import Path
from threading import Lock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import shioaji_tmf_target as shared

_api = None
_sj = None
_lock = Lock()
_failed_apis = []


def required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必要環境變數: {name}")
    return value


def position_unit():
    unit = int(os.getenv("H_UNIT", "1"))
    if unit < 1:
        raise ValueError("H_UNIT 必須是正整數")
    return unit


def initialize_broker_session():
    global _api, _sj
    if _api is not None:
        return _api
    import shioaji as sj
    path = Path(os.getenv("CA_PATH3") or os.getenv("CA_PATH") or BACKEND / "Sinopac.pfx")
    if not path.is_file():
        raise FileNotFoundError("找不到永豐憑證檔，請確認 CA_PATH3 / CA_PATH")
    api = sj.Shioaji(simulation=False)
    try:
        api.login(required("API_KEY3"), required("SECRET_KEY3"))
        person = os.getenv("PERSON_ID3") or required("PERSON_ID")
        api.activate_ca(ca_path=str(path), ca_passwd=person, person_id=person)
    except Exception:
        _failed_apis.append(api)
        raise
    _api, _sj = api, sj
    return api


def execute_signal(direction, *, guard, persist, record, api=None, sj=None):
    target = direction * position_unit()  # Validate before flattening.
    with _lock:
        api = api if api is not None else initialize_broker_session()
        sj = sj if sj is not None else _sj
        for phase, position in (("flatten", 0), ("entry", target)):
            result = shared.execute_target_position(
                position, api=api, sj=sj, strict_tmf=True,
                guard=guard, persist_guard=persist)
            if not result.confirmed or result.actual_position != position:
                raise shared.BrokerOrderError("未確認前一階段部位，停止後續委託")
            record(phase, result)
        return result
