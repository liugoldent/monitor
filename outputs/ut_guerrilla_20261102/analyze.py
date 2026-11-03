"""Exploratory UT Bot / guerrilla study. Never connects to execution services."""
from pathlib import Path
import io, json, hashlib, gzip
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
END = pd.Timestamp('2026-11-02 18:49:00')
SPLIT = pd.Timestamp('2026-09-01')
MINUTE = pd.Timedelta(minutes=1)

def snapshot(name):
    frozen = OUT / (name + '.gz')
    b = gzip.decompress(frozen.read_bytes()) if frozen.exists() else (ROOT / 'backend-futures-py/tv_doc' / name).read_bytes()
    if not frozen.exists(): frozen.write_bytes(gzip.compress(b))
    return pd.read_csv(io.BytesIO(b)), {'file': name, 'bytes':len(b), 'sha256':hashlib.sha256(b).hexdigest()}

def session(t):
    m = t.dt.hour * 60 + t.dt.minute
    day = (m >= 525) & (m < 825)
    valid = day | (m >= 900) | (m < 300)
    sid = t.dt.normalize() + pd.to_timedelta(np.where(day,525,np.where(m>=900,900,-540)),unit='m')
    return sid, valid, np.where(day,300,840)

def load():
    p, pa = snapshot('webhook_data_1min.csv')
    x, xa = snapshot('mxf_value.csv')
    p['t'] = pd.to_datetime(p['TradingView Time'],errors='coerce')
    p['r'] = pd.to_datetime(p['Record Time'],errors='coerce')
    x['t'] = pd.to_datetime(x['time'],errors='coerce')
    pa['duplicate_times'] = int(p.t.duplicated().sum())
    xa['duplicate_times'] = int(x.t.duplicated().sum())
    pa['conflicting_duplicate_times'] = int((p.groupby('t')[['Open','High','Low','Close']].nunique().max(axis=1)>1).sum())
    xa['conflicting_duplicate_times'] = int((x.groupby('t').mtx_bvav.nunique()>1).sum())
    # Conflicting prices are excluded, not silently overwritten. For identical bars use earliest receipt.
    conflict = p.groupby('t')[['Open','High','Low','Close']].nunique().max(axis=1)
    p = p[~p.t.isin(conflict[conflict>1].index)].copy()
    p = p.sort_values(['t','r']).drop_duplicates('t',keep='first')
    p = p[(p.t<=END)&p.t.notna()&p.r.notna()&(p.Symbol=='MXF1!')].copy()
    for c in ['Open','High','Low','Close']: p[c] = pd.to_numeric(p[c],errors='coerce')
    valid_ohlc = p[['Open','High','Low','Close']].notna().all(axis=1)&(p.High>=p[['Open','Close','Low']].max(axis=1))&(p.Low<=p[['Open','Close','High']].min(axis=1))
    pa['invalid_ohlc'] = int((~valid_ohlc).sum())
    p = p[valid_ohlc].copy()
    p['sid'], valid, p['expected'] = session(p.t)
    pa['outside_regular_sessions'] = int((~valid).sum())
    p = p[valid].copy()
    x = x[(x.t<=END)&x.t.notna()].sort_values('t').drop_duplicates('t',keep='first').copy()
    x['g'] = pd.to_numeric(x.mtx_bvav,errors='coerce')
    x = x[x.g.notna()].copy()
    # CSV has minute-only sampling times. Add one minute to avoid treating a sample as known at :00.
    x['available'] = x.t + MINUTE
    check = pd.merge_asof(p[['t','sid','expected']].sort_values('t'),x[['available','g']],left_on='t',right_on='available',tolerance=MINUTE,direction='backward')
    sessions = check.groupby('sid').agg(n=('t','size'),expected=('expected','first'),g_valid=('g','count'))
    sessions['coverage'] = sessions.g_valid / sessions.expected
    sessions['eligible'] = (sessions.n==sessions.expected)&(sessions.coverage>=.95)
    sessions.to_csv(OUT/'session_coverage.csv')
    eligible = set(sessions[sessions.eligible].index)
    pa.update(start=str(p.t.min()),end=str(p.t.max()),rows=len(p))
    xa.update(start=str(x.t.min()),end=str(x.t.max()),rows=len(x))
    audit = {'cutoff':str(END),'split':str(SPLIT),'prices':pa,'guerrilla':xa,'sessions':len(sessions),'complete_price_sessions':int((sessions.n==sessions.expected).sum()),'eligible_sessions':len(eligible),'eligible_by_month':sessions[sessions.eligible].groupby(sessions[sessions.eligible].index.to_period('M')).size().astype(int).to_dict(),'guerrilla_rows_by_month':x.groupby(x.t.dt.to_period('M')).size().astype(int).to_dict()}
    audit['eligible_by_month']={str(k):v for k,v in audit['eligible_by_month'].items()}
    audit['guerrilla_rows_by_month']={str(k):v for k,v in audit['guerrilla_rows_by_month'].items()}
    return p,x,eligible,audit

def candles(p, tf):
    z=p.copy()
    z['bucket']=z.sid+pd.to_timedelta(((z.t-z.sid).dt.total_seconds()//(60*tf))*tf,unit='m')
    c=z.groupby(['sid','bucket'],sort=True).agg(Open=('Open','first'),High=('High','max'),Low=('Low','min'),Close=('Close','last'),r=('r','max'),n=('t','size'),expected=('expected','first')).reset_index()
    c=c[c.n==tf].copy()
    c['end']=c.bucket+tf*MINUTE
    c['available']=c[['r','end']].max(axis=1)
    # Reset after unobserved candles; carry state across complete, scheduled session breaks.
    sig=[]; raw_sig=[]; warm_counts=[]; atr=np.nan; stop=0.; prev=np.nan; trseed=[]; warm=0; last=None
    for row in c.itertuples():
        continuous=last is not None and (row.bucket==last.end or (row.bucket==row.sid and last.end==last.sid+last.expected*MINUTE))
        if not continuous:
            atr=np.nan; stop=0.; prev=np.nan; trseed=[]; warm=0
        tr=max(row.High-row.Low,abs(row.High-prev),abs(row.Low-prev)) if np.isfinite(prev) else row.High-row.Low
        trseed.append(tr)
        if len(trseed)==10: atr=float(np.mean(trseed))
        elif len(trseed)>10: atr=(atr*9+tr)/10
        old=stop
        if np.isfinite(atr):
            if row.Close>old and prev>old: stop=max(old,row.Close-atr)
            elif row.Close<old and prev<old: stop=min(old,row.Close+atr)
            elif row.Close>old: stop=row.Close-atr
            else: stop=row.Close+atr
        else: stop=0.
        warm+=1
        direction=1 if row.Close>stop and prev<=old else (-1 if row.Close<stop and prev>=old else 0)
        raw_sig.append(direction if np.isfinite(atr) else 0)
        warm_counts.append(warm)
        sig.append(direction if warm>=50 and np.isfinite(atr) else 0)
        prev=row.Close; last=row
    c['direction']=sig
    c['raw_direction']=raw_sig
    c['warm_count']=warm_counts
    return c

def make_events(c,x,p,eligible,extra_delay=0):
    ev=c[(c.direction!=0)&c.sid.isin(eligible)].copy()
    ev=pd.merge_asof(ev.sort_values('available'),x[['available','g']].rename(columns={'available':'g_available'}),left_on='available',right_on='g_available',tolerance=MINUTE,direction='backward')
    ev['fill_t']=ev.available.dt.floor('min')+(1+extra_delay)*MINUTE
    ev=ev.merge(p[['t','Open','sid']].rename(columns={'t':'fill_t','Open':'fill','sid':'fill_sid'}),on='fill_t',how='left')
    ev=ev[(ev.sid==ev.fill_sid)&(ev.fill_t<ev.sid+(ev.expected-1)*MINUTE)].copy()
    return ev.sort_values(['fill_t','bucket'])

def replay(events,p,eligible,mode,threshold,tf,max_hold=None):
    trades=[]
    force=p[(p.sid.isin(eligible))&(p.t==p.sid+(p.expected-1)*MINUTE)].set_index('sid')
    price_map=p.set_index('t').Open
    for sid,es in events.groupby('sid',sort=True):
        position=0; entry=None
        def close(t,price,reason):
            nonlocal position,entry
            if position:
                gross=(price-entry['fill'])*position
                trades.append({'tf':tf,'mode':mode,'threshold':threshold,'sid':sid,'entry_t':entry['fill_t'],'exit_t':t,'side':position,'entry':entry['fill'],'exit':price,'g':entry['g'],'gross':gross,'net4':gross-4,'reason':reason})
                position=0; entry=None
        for e in es.to_dict('records'):
            if position and max_hold is not None:
                deadline=min(entry['fill_t']+max_hold*MINUTE,force.loc[sid].t)
                if e['fill_t']>=deadline:
                    close(deadline,float(price_map.loc[deadline]),'holding_limit')
            d=e['direction']
            if position and d!=position: close(e['fill_t'],e['fill'],'opposite_signal')
            if mode=='baseline': allowed=True
            else:
                v=e['g']*d*(1 if mode=='same_sign' else -1)
                allowed=pd.notna(v) and v>threshold
            if not position and allowed: position=d;entry=e
        if position:
            f=force.loc[sid]
            deadline=min(entry['fill_t']+max_hold*MINUTE,f.t) if max_hold is not None else f.t
            close(deadline,float(price_map.loc[deadline]),'holding_limit' if deadline<f.t else 'scheduled_flat')
    return pd.DataFrame(trades)

def metrics(t):
    if t.empty: return {'trades':0,'gross_profit':0.,'gross_loss':0.,'gross':0.,'net4':0.,'pf_net4':None,'win_rate_net4':None,'max_closed_drawdown_net4':0.,'one_way_turnover':0,'ending_position':0,'unrealized':0.}
    t=t.sort_values('exit_t'); gross=t.gross.to_numpy(); net=t.net4.to_numpy(); eq=np.r_[0,np.cumsum(net)]
    gp=float(gross[gross>0].sum()); gl=float(-gross[gross<0].sum()); loss=-net[net<0].sum()
    assert abs(gp-gl-gross.sum())<1e-8
    return {'trades':len(t),'gross_profit':gp,'gross_loss':gl,'gross':float(gross.sum()),'net2':float((gross-2).sum()),'net4':float(net.sum()),'net8':float((gross-8).sum()),'pf_net4':float(net[net>0].sum()/loss) if loss else None,'win_rate_net4':float(np.mean(net>0)),'mean_net4':float(net.mean()),'max_closed_drawdown_net4':float((np.maximum.accumulate(eq)-eq).max()),'one_way_turnover':2*len(t),'ending_position':0,'unrealized':0.,'best_trade_net4':float(net.max()),'worst_trade_net4':float(net.min())}

def main():
    p,x,eligible,audit=load()
    print('AUDIT',json.dumps(audit,ensure_ascii=False),flush=True)
    summaries=[]; all_trades=[]; event_sets={}
    for tf in [1,2,3,5,10,15]:
        c=candles(p,tf); ev=make_events(c,x,p,eligible);event_sets[tf]=(c,ev)
        c[c.bucket>=pd.Timestamp('2026-11-02 15:00')][['bucket','Open','High','Low','Close','direction']].to_csv(OUT/f'latest_signals_{tf}m.csv',index=False)
        configs=[('baseline',0)]+[(mode,k) for mode in ['same_sign','opposite_sign'] for k in [0,500,1000,1500,2000]]
        for mode,k in configs:
            t=replay(ev,p,eligible,mode,k,tf)
            if t.empty: continue
            all_trades.append(t)
            for period,sub in [('train',t[t.sid<SPLIT]),('holdout',t[t.sid>=SPLIT]),('all',t)]:
                summaries.append(dict(tf=tf,mode=mode,threshold=k,period=period,**metrics(sub)))
        print('TF complete',tf,flush=True)
    summary=pd.DataFrame(summaries);trades=pd.concat(all_trades,ignore_index=True)
    summary.to_csv(OUT/'strategy_summary.csv',index=False);trades.to_csv(OUT/'trades.csv',index=False)
    train=summary[(summary.period=='train')&(summary.trades>=100)&(summary['mode']!='baseline')].sort_values('net4',ascending=False)
    best=train.iloc[0]
    chosen=trades[(trades.tf==best.tf)&(trades['mode']==best['mode'])&(trades.threshold==best.threshold)]
    base=trades[(trades.tf==best.tf)&(trades['mode']=='baseline')]
    monthly=[]
    for label,t in [('selected',chosen),('baseline_same_tf',base)]:
        for month,sub in t.groupby(t.sid.dt.to_period('M')):
            monthly.append(dict(strategy=label,month=str(month),**metrics(sub)))
    pd.DataFrame(monthly).to_csv(OUT/'selected_monthly.csv',index=False)
    delay=[]
    for extra in [0,1,2]:
        ev=make_events(event_sets[int(best.tf)][0],x,p,eligible,extra)
        for mode,k in [(best['mode'],int(best.threshold)),('baseline',0)]:
            t=replay(ev,p,eligible,mode,k,int(best.tf))
            delay.append(dict(extra_delay_min=extra,mode=mode,**metrics(t[t.sid>=SPLIT] if not t.empty else t)))
    pd.DataFrame(delay).to_csv(OUT/'delay_sensitivity.csv',index=False)
    # Forward-return diagnostics are overlapping observations, not executable trade P&L.
    a=pd.merge_asof(p[['t','Open','sid']].sort_values('t'),x[['available','g']],left_on='t',right_on='available',tolerance=MINUTE,direction='backward')
    a=a[a.sid.isin(eligible)].copy()
    corr=[];bins=[]
    prices=p.set_index('t')
    for h in [1,2,3,5,10,15]:
        future=prices.reindex(pd.DatetimeIndex(a.t+h*MINUTE))
        ret=future.Open.to_numpy()-a.Open.to_numpy()
        same=future.sid.to_numpy()==a.sid.to_numpy()
        a['ret']=np.where(same,ret,np.nan)
        for period,sub in [('train',a[a.sid<SPLIT]),('holdout',a[a.sid>=SPLIT])]:
            sub=sub.dropna(subset=['g','ret'])
            corr.append(dict(horizon=h,period=period,n=len(sub),pearson=float(sub.g.corr(sub.ret)),spearman=float(sub.g.rank().corr(sub.ret.rank()))))
            for low,high in [(-np.inf,-2000),(-2000,-1000),(-1000,0),(0,1000),(1000,2000),(2000,np.inf)]:
                q=sub[(sub.g>=low)&(sub.g<high)]
                bins.append(dict(horizon=h,period=period,low=low,high=high,n=len(q),mean_return=float(q.ret.mean()),up_rate=float((q.ret>0).mean())))
    pd.DataFrame(corr).to_csv(OUT/'forward_correlations.csv',index=False)
    pd.DataFrame(bins).to_csv(OUT/'forward_buckets.csv',index=False)
    result={'audit':audit,'selected_on_train':best.to_dict(),'selected_holdout':metrics(chosen[chosen.sid>=SPLIT]),'baseline_holdout_same_tf':metrics(base[base.sid>=SPLIT]),'delay_sensitivity':delay,'correlations':corr,'monthly':monthly,'top_train':train.head(10).to_dict('records'),'baseline_all_tf_holdout':summary[(summary.period=='holdout')&(summary['mode']=='baseline')].to_dict('records')}
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print('RESULT',json.dumps({k:v for k,v in result.items() if k not in ['audit','monthly','delay_sensitivity']},ensure_ascii=False,default=str),flush=True)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(10,4.8))
        for label,t in [('Train-selected filter',chosen),('UT Bot, same timeframe',base)]:
            q=t[t.sid>=SPLIT].sort_values('exit_t')
            daily=q.groupby(q.exit_t.dt.normalize()).net4.sum().cumsum()
            ax.plot(daily.index,daily.values,label=label)
        ax.axhline(0,color='gray',lw=.7);ax.set_title('Holdout: cumulative closed P&L after 4 points per round trip')
        ax.set_ylabel('Points per contract');ax.legend();ax.grid(alpha=.2);fig.autofmt_xdate();fig.tight_layout();fig.savefig(OUT/'holdout_equity.png',dpi=150);plt.close(fig)
    except ImportError: pass

if __name__=='__main__': main()
