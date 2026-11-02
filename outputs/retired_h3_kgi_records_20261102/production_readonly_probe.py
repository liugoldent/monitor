"""Production connection proof: read-only queries, no order/watch entry point."""
import json
import msvcrt
import os
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from kgi_monitor import account_key, credentials, load_env, read_config
from kgi_broker import KgiBroker, parse_position
from shared_h import Store

load_env(BASE/'.env')
creds=credentials()
creds=(*creds[:-1],False)
config=read_config(BASE/'h-config.json')
key=account_key(creds[2],creds[3],False)
runtime=BASE/'runtime'
result={'environment':'production','read_only':True,'order_submission':False}
lock=(runtime/f'{key}.lock').open('a+b')
lock.seek(0)
if not lock.read(1):lock.write(b'0');lock.flush()
lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
store=Store(runtime/f'{key}.sqlite')
sdk=runtime/f'{key}-sdk';sdk.mkdir(exist_ok=True);os.chdir(sdk)
broker=KgiBroker(config,store)
try:
    try:
        broker.login(*creds)
        result['adapter_login_complete']=True
    except Exception as exc:
        result['adapter_error_type']=type(exc).__name__
    if broker.api is not None:
        native=getattr(broker.api,'_ObjOrder',None)
        result['connected']=getattr(native,'FIsConnected',None) is True
        result['logged_in']=getattr(native,'FIsLogon',None) is True
        info=broker.api.login_info().get('期貨',{})
        result['futures_account_verified']=info=={'account':creds[2],'broker_id':creds[3]}
        products=native.get_Fcontracts(type='df')
        products.to_json(runtime/'production_public_contracts.json',orient='records',force_ascii=False)
        rows=products[products.ComID.astype(str).str.strip()=='TMF']
        columns=[c for c in ('Exchange','ComID','ComTyp','ComCName','ComEName','Currency','ContractValue','PMultiplier','Tick') if c in rows.columns]
        result['tmf_metadata']=rows[columns].to_dict('records')
        frame=broker.api.FutAccount.PositionSum()
        result['inventory_response_received']=frame is not None
        if frame is not None:
            result['inventory_columns']=list(frame.columns)
            result['inventory_rows']=len(frame)
            try:
                result['tmf_net_position']=parse_position(frame,creds[2],creds[3],config['contract'])
            except Exception as exc:
                result['inventory_validation_error_type']=type(exc).__name__
except Exception as exc:
    result['probe_error_type']=type(exc).__name__
finally:
    (runtime/'production_readonly_probe_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    try:broker.close()
    finally:store.db.close();lock.close()
