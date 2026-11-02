import sys,os,json,msvcrt,sqlite3
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from kgi_monitor import credentials,account_key,read_config
from shared_h import Store,load_env
from kgi_broker import KgiBroker,ExplicitPositionBridge
def forbidden(*args,**kwargs):
    raise RuntimeError('Order submission disabled in this read-only reconciliation')
KgiBroker.send_and_confirm=forbidden
ExplicitPositionBridge.Order=forbidden
load_env(BASE/'.env')
creds=credentials()
assert creds[-1] is False
config=read_config(BASE/'h-config.json')
runtime=BASE/'runtime'
key=account_key(creds[2],creds[3],False)
lock=(runtime/(key+'.lock')).open('a+b')
lock.seek(0)
msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
store=Store(runtime/(key+'.sqlite'))
backup=sqlite3.connect(runtime/(key+'-before-normal-mode.sqlite'))
store.db.backup(backup)
backup.close()
os.chdir(runtime/(key+'-sdk'))
broker=KgiBroker(config,store,output=lambda *a,**kw:None)
result={'read_only_broker_calls':True,'order_submission':False,'signal_watcher_started':False}
try:
    before=store.db.execute('SELECT payload FROM guard').fetchall()
    broker.login(*creds)
    result['account_suffix']=creds[2][-4:]
    result['reconciled_rejection']=broker.recover_rejected_pending()
    result['pending_after']=bool(store.pending())
    result['position_after']=broker.position()
    row=store.db.execute("SELECT detail FROM audit WHERE event='order_resolved_rejected' ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        proof=json.loads(row[0])['evidence']
        result['error_code']=proof['error_code']
        result['zero_fills']=proof['zero_fills']
        result['proof_source']=proof['source']
except Exception as exc:
    result['error_type']=type(exc).__name__
finally:
    (runtime/'normal_mode_reconcile_result.json').write_text(json.dumps(result,ensure_ascii=True,indent=2),encoding='utf-8')
    broker.close()
    store.db.close()
    lock.close()
