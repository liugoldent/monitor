"""Additional predeclared 10-minute holding cap; same frozen input data."""
import json
import numpy as np
import pandas as pd
from analyze import OUT, SPLIT, MINUTE, load, candles, make_events, replay, metrics

def check_replay():
    sid=pd.Timestamp('2026-09-01 08:45')
    times=pd.date_range('2026-09-01 09:00',periods=4,freq='min')
    events=pd.DataFrame(dict(sid=[sid]*4,fill_t=times,fill=[100.,105.,104.,108.],direction=[1,-1,1,-1],g=[1500.,1500.,-1500.,-1500.]))
    p=pd.DataFrame(dict(sid=[sid]*5,t=[*times,pd.Timestamp('2026-09-01 13:44')],Open=[100.,105.,104.,108.,110.],expected=[300]*5))
    a=replay(events,p,{sid},'baseline',0,1)
    b=replay(events,p,{sid},'same_sign',1000,1)
    assert list(a.gross)==[5.,1.,4.,-2.]
    assert list(b.gross)==[5.,-2.]
    assert metrics(a)['net4']==-8 and metrics(b)['net4']==-5
    equal=events.iloc[:1].copy();equal['g']=1000.
    assert replay(equal,p,{sid},'same_sign',1000,1).empty

def mtm(t,p):
    q=p.sort_values('t').reset_index(drop=True)
    index=pd.Index(q.t)
    delta=np.zeros(len(q));unreal=np.zeros(len(q))
    for row in t.itertuples():
        a=index.get_loc(row.entry_t);b=index.get_loc(row.exit_t)
        delta[b]+=row.gross-4
        unreal[a:b]+=(q.Close.iloc[a:b].to_numpy()-row.entry)*row.side-2
    eq=np.r_[0,np.cumsum(delta)+unreal]
    return float((np.maximum.accumulate(eq)-eq).max())

def main():
    check_replay()
    p,x,eligible,audit=load()
    summaries=[];all_trades=[]
    for tf in [1,2,3,5]:
        ev=make_events(candles(p,tf),x,p,eligible)
        for scope in ['day_only','day_and_night']:
            allowed={sid for sid in eligible if scope=='day_and_night' or sid.hour==8}
            events=ev[ev.sid.isin(allowed)]
            for mode,k in [('baseline',0)]+[(m,k) for m in ['same_sign','opposite_sign'] for k in [0,500,1000,1500,2000]]:
                t=replay(events,p,allowed,mode,k,tf,max_hold=10)
                if t.empty:continue
                t['scope']=scope
                assert ((t.exit_t-t.entry_t)<=10*MINUTE).all()
                assert (t.exit_t>=t.entry_t).all()
                assert (t.entry_t+MINUTE<=t.exit_t).all()
                all_trades.append(t)
                for period,sub in [('train',t[t.sid<SPLIT]),('holdout',t[t.sid>=SPLIT])]:
                    summaries.append(dict(scope=scope,tf=tf,mode=mode,threshold=k,period=period,**metrics(sub)))
        print('holding cap TF',tf,flush=True)
    summary=pd.DataFrame(summaries);trades=pd.concat(all_trades,ignore_index=True)
    summary.to_csv(OUT/'short_hold_summary.csv',index=False);trades.to_csv(OUT/'short_hold_trades.csv',index=False)
    selected={}
    for scope in ['day_only','day_and_night']:
        train=summary[(summary.scope==scope)&(summary.period=='train')&(summary.trades>=100)&(summary['mode']!='baseline')].sort_values('net4',ascending=False)
        b=train.iloc[0]
        t=trades[(trades.scope==scope)&(trades.tf==b.tf)&(trades['mode']==b['mode'])&(trades.threshold==b.threshold)]
        hold=t[t.sid>=SPLIT]
        hold_sids={sid for sid in eligible if sid>=SPLIT and (scope=='day_and_night' or sid.hour==8)}
        days=pd.DatetimeIndex(sorted({sid.normalize() for sid in hold_sids}))
        daily=hold.groupby(hold.sid.dt.normalize()).net4.sum().reindex(days,fill_value=0).to_numpy()
        rng=np.random.default_rng(20261102)
        boot=rng.choice(daily,size=(10000,len(daily)),replace=True).mean(axis=1)
        selected[scope]={'train':b.to_dict(),'holdout':metrics(hold),'holdout_mark_to_market_drawdown':mtm(hold,p[p.sid>=SPLIT]),'holdout_sessions':len(hold_sids),'observed_calendar_dates':len(days),'mean_net_points_per_observed_date':float(daily.mean()),'bootstrap_95pct_mean_points_per_date':np.quantile(boot,[.025,.975]).tolist(),'train_without_largest_win':float(t[t.sid<SPLIT].net4.sum()-t[t.sid<SPLIT].net4.max())}
        months=[]
        for month,z in t.groupby(t.sid.dt.to_period('M')): months.append(dict(month=str(month),**metrics(z)))
        selected[scope]['monthly']=months
    original=pd.read_csv(OUT/'trades.csv',parse_dates=['sid','entry_t','exit_t'])
    original=original[(original.tf==10)&(original['mode']=='same_sign')&(original.threshold==1000)&(original.sid>=SPLIT)]
    selected['original_selected_mtm_drawdown']=mtm(original,p[p.sid>=SPLIT])
    (OUT/'short_hold_results.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(selected,ensure_ascii=False,default=str),flush=True)

if __name__=='__main__':main()
