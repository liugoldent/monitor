import sys,os,time,json,msvcrt
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from local_env import load_env,selected_account
from h_monitor import read_config
from h_core import Store
from capital_broker import CapitalBroker,SK
def forbidden(*a,**k):
    raise RuntimeError('Orders disabled in read-only diagnostic')
SK.SendFutureProxyOrder=forbidden
load_env(BASE/'.env')
lock=(BASE/'runtime/live.lock').open('a+b')
lock.seek(0)
msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
config=read_config(BASE/'h-config.json')
config['account']=selected_account()
if not config['account']:raise RuntimeError('Explicit account required')
store=Store(':memory:')
broker=CapitalBroker(config,store,output=lambda *a,**k:None)
result={'mode':'read_only','orders_sent':0}
try:
    broker.login(os.environ['CAPITAL_LOGIN_ID'],os.environ['CAPITAL_PASSWORD'],diagnose=True)
    result.update(account_suffix=broker.account[-4:],login_ok=True,replay_complete=broker.replay_complete.is_set())
    broker.validate_contract()
    result['contract_valid']=True
    result['position_first']=broker.position()
    result['position_second']=broker.position()
    broker.check_outstanding()
    result['no_working_micro_orders']=True
    result['reports']=[{k:r.get(k,'') for k in ('KeyNo','SeqNo','MarketType','Type','OrderErr','BuySell','ComId','ComId1','YearMonth1','OrderNo','Qty','ExecutionNo','Date','Time','TradeDate','ErrorMsg')} for r in broker.reports if broker.is_micro(r)]
    rights=SK.GetFutureRights(broker.login_id,broker.account,0)
    result['rights_status']=rights.StatusCode
    result['rights']=[{k:getattr(b,k,b'').decode('ansi') for k in ('Equity','ExcessMargin','AccountBalance','InitialMargin','OrderMargin')} for b in rights.Blocks]
except Exception as exc:
    result['error_type']=type(exc).__name__
    result['reason']=broker.redact(str(exc)) if type(exc).__name__=='TradingError' else 'SDK or system error'
finally:
    result['audit']=[(e,json.loads(d)) for e,d in store.db.execute('SELECT event,detail FROM audit')]
    broker.close()
    store.db.close()
    lock.close()
    (BASE/'runtime/read_only_check_20261015_result.json').write_text(json.dumps(result,ensure_ascii=True,indent=2),encoding='utf-8')
