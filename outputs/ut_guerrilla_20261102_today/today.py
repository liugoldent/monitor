from pathlib import Path
import io, gzip, hashlib, json, sys
import pandas as pd
import numpy as np

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
sys.path.insert(0,str(OUT.parent/'ut_guerrilla_20261102'))
from analyze import candles, session, metrics, MINUTE

CUTOFF=pd.Timestamp('2026-11-02 19:46:00')
START=pd.Timestamp('2026-11-02 08:45:00')

def read(name):
    f=OUT/(name+'.gz')
    b=gzip.decompress(f.read_bytes()) if f.exists() else (ROOT/'backend-futures-py/tv_doc'/name).read_bytes()
    if not f.exists():f.write_bytes(gzip.compress(b))
    return pd.read_csv(io.BytesIO(b)), hashlib.sha256(b).hexdigest()

def main():
    p,ph=read('webhook_data_1min.csv');x,xh=read('mxf_value.csv')
    p['t']=pd.to_datetime(p['TradingView Time']);p['r']=pd.to_datetime(p['Record Time'])
    p=p[(p.t<=CUTOFF)&(p.Symbol=='MXF1!')].sort_values(['t','r']).drop_duplicates('t').copy()
    p['sid'],v,p['expected']=session(p.t);p=p[v].copy()
    x['t']=pd.to_datetime(x.time);x['g']=pd.to_numeric(x.mtx_bvav,errors='coerce')
    x=x[(x.t<=CUTOFF)&x.g.notna()].sort_values('t').drop_duplicates('t').copy();x['available']=x.t+MINUTE
    c=candles(p,1)
    ev=c[(c.bucket>=START)&(c.raw_direction!=0)].copy();ev['direction']=ev.raw_direction
    ev=pd.merge_asof(ev.sort_values('available'),x[['t','available','g']].rename(columns={'t':'g_time','available':'g_available'}),left_on='available',right_on='g_available',direction='backward',tolerance=MINUTE)
    ev['fill_t']=ev.available.dt.floor('min')+MINUTE
    ev=ev.merge(p[['t','Open','sid']].rename(columns={'t':'fill_t','Open':'fill','sid':'fill_sid'}),on='fill_t',how='left')
    ev['gate500']=ev.g*ev.direction>500
    ev['valid_fill']=(ev.sid==ev.fill_sid)&ev.fill.notna()&(ev.fill_t<ev.sid+(ev.expected-1)*MINUTE)
    ev.to_csv(OUT/'today_signal_candidates.csv',index=False)
    price=p.set_index('t');trades=[];open_positions=[];issues=[]
    for sid,z in ev.groupby('sid'):
        position=0;entry=None;terminal=sid+(z.expected.iloc[0]-1)*MINUTE
        def close(t,reason):
            nonlocal position,entry
            if t not in price.index:
                issues.append({'time':str(t),'reason':'missing_exit_price','entry':str(entry['fill_t'])});position=0;entry=None;return
            fill=float(price.loc[t,'Open']);gross=(fill-entry['fill'])*position
            trades.append(dict(sid=sid,signal_time=entry['bucket'],entry_t=entry['fill_t'],exit_t=t,side=position,entry=entry['fill'],exit=fill,g=entry['g'],gross=gross,net4=gross-4,reason=reason))
            position=0;entry=None
        for e in z.to_dict('records'):
            if position:
                deadline=min(entry['fill_t']+10*MINUTE,terminal)
                if deadline<=CUTOFF and deadline<=e['fill_t']:close(deadline,'holding_limit' if deadline<terminal else 'scheduled_flat')
            if not e['valid_fill']:
                if e['gate500'] or position:issues.append({'time':str(e['bucket']),'reason':'unavailable_signal_fill'})
                continue
            if position and position!=e['direction']:close(e['fill_t'],'opposite_signal')
            if not position and e['gate500']:
                entry=e;position=e['direction']
        if position:
            deadline=min(entry['fill_t']+10*MINUTE,terminal)
            if deadline<=CUTOFF:close(deadline,'holding_limit' if deadline<terminal else 'scheduled_flat')
            else:open_positions.append(dict(entry_t=str(entry['fill_t']),side=position,entry=entry['fill'],mark=float(price.iloc[-1].Close),unrealized=(float(price.iloc[-1].Close)-entry['fill'])*position))
    trades=pd.DataFrame(trades);trades.to_csv(OUT/'today_trades.csv',index=False)
    summary=[]
    if not trades.empty:
        for (sid,side),z in trades.groupby(['sid','side']):summary.append(dict(session=str(sid),side=int(side),**metrics(z)))
    periods=[]
    for sid,z in x[x.t>=START].groupby(np.where(x[x.t>=START].t.dt.hour<15,'day','night')):
        for label,condition in [('above500',z.g>500),('below_minus500',z.g< -500)]:
            a=z[condition]
            if not a.empty:
                a=a.copy();a['block']=(a.t.diff()!=MINUTE).cumsum()
                for _,b in a.groupby('block'):periods.append(dict(session=sid,condition=label,start=str(b.t.min()),end=str(b.t.max()),min=int(b.g.min()),max=int(b.g.max()),n=len(b)))
    today=p[p.t>=START]
    gaps=[]
    for sid,z in today.groupby('sid'):
        target=pd.date_range(sid,min(CUTOFF,sid+(z.expected.iloc[0]-1)*MINUTE),freq='min')
        gaps.extend(str(t) for t in target.difference(z.t))
    result=dict(cutoff=str(CUTOFF),price_sha256=ph,guerrilla_sha256=xh,threshold_periods=periods,summary=summary,open_positions=open_positions,issues=issues,missing_price_minutes=gaps,accepted_candidates=ev[ev.gate500][['bucket','direction','g','g_time','fill_t','fill','valid_fill','warm_count']].to_dict('records'))
    (OUT/'today_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,default=str),flush=True)

if __name__=='__main__': main()
