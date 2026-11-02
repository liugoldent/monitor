import sys,os,json,msvcrt
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from kgi_monitor import credentials,account_key,read_config
from shared_h import Store,load_env
from kgi_broker import KgiBroker
load_env(BASE/'.env')
creds=credentials()
assert creds[-1] is False
config=read_config(BASE/'h-config.json')
runtime=BASE/'runtime'
key=account_key(creds[2],creds[3],False)
lock=(runtime/(key+'.lock')).open('a+b')
lock.seek(0)
msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
store=Store(':memory:')
os.chdir(runtime/(key+'-sdk'))
broker=KgiBroker(config,store,output=lambda *a,**kw:None)
# This diagnostic has no signal watcher, execution helper or create_order call.
result={'read_only':True,'order_submission':False}
try:
    broker.login(*creds)
    result['account_suffix']=creds[2][-4:]
    result['position_first']=broker.position()
    result['position_second']=broker.position()
    result['orders']=list(broker.core.order_list)
    result['deals']=list(broker.core.deal_list)
    result['trade_count']=len(broker.api.FutOrder.get_trades(full=True))
except Exception as exc:
    result['error_type']=type(exc).__name__
finally:
    (runtime/'normal_mode_readonly_private.json').write_text(json.dumps(result,ensure_ascii=True,indent=2,default=str),encoding='utf-8')
    broker.close()
    store.db.close()
    lock.close()
