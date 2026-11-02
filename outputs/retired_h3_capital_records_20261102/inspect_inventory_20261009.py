"""One-off read-only account diagnosis. Never watches signals or sends orders."""
import json
import msvcrt
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from local_env import load_env, selected_account
from h_monitor import read_config
from h_core import Store, parse_inventory
from capital_broker import CapitalBroker, SK

load_env(BASE / '.env')
secrets = [os.getenv(k, '') for k in ('CAPITAL_LOGIN_ID', 'CAPITAL_PASSWORD',
                                     'CAPITAL_ACCOUNT_1', 'CAPITAL_ACCOUNT_2')]
def emit(value):
    line = json.dumps(value, ensure_ascii=True)
    for secret in secrets:
        if secret:
            line = line.replace(secret, '[redacted]')
    print(line, flush=True)

lock = (BASE / 'runtime' / 'live.lock').open('r+b')
lock.seek(0)
msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
config = read_config(BASE / 'h-config.json')
config['account'] = selected_account()
if not config['account']:
    raise RuntimeError('An explicitly configured account is required')
store = Store(':memory:')
broker = CapitalBroker(config, store, output=emit)
try:
    broker.login(os.environ['CAPITAL_LOGIN_ID'], os.environ['CAPITAL_PASSWORD'], diagnose=True)
    secrets.append(broker.account)
    emit({'mode': 'read_only', 'account': '***' + broker.account[-4:],
          'message_1019': SK.GetMessage(1019)})
    for attempt in range(1, 3):
        broker.check()
        started = time.monotonic()
        result = SK.GetOpenInterestGW(broker.login_id, broker.account, 0)
        detail = {'attempt': attempt, 'elapsed_s': round(time.monotonic() - started, 3),
                  'status': result.StatusCode, 'raw': result.RawData,
                  'message': getattr(result, 'Message', '')}
        try:
            detail['micro_position'] = parse_inventory(result, broker.account, broker.login_id, broker.contract)
        except Exception as exc:
            detail['parse_error'] = str(exc)
        emit(detail)
        if attempt < 2:
            until = time.monotonic() + 3
            while time.monotonic() < until:
                broker.check()
                time.sleep(0.05)
    emit({'updated_position_method': broker.position(), 'query_busy_audit':
          store.db.execute('SELECT event,detail FROM audit').fetchall()})
finally:
    broker.close()
    store.db.close()
    lock.close()
    emit({'finished': True, 'orders_sent': 0})
