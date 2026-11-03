"""Compare UI-observed TradingView labels with frozen bars and as-of guerrilla."""
import json
import pandas as pd
import numpy as np
from analyze import OUT, MINUTE, load, candles, replay, metrics

def main():
    p,x,eligible,audit=load()
    blocks=json.loads((OUT/'tv_verified_blocks.json').read_text(encoding='utf-8'))
    rows=[]; positive=[]
    for b in blocks:
        tf=b['stepMinutes']
        ts=pd.date_range(b['start'],b['end'],freq=f'{tf}min')
        assert len(ts)==b['bars']
        mapping={pd.Timestamp(z[0]):int(z[6]-z[7]) for z in b['signals']}
        for t in ts: rows.append(dict(bucket=t,tf=tf,tv_direction=mapping.get(t,0)))
        for z in b['signals']:
            positive.append(dict(bucket=pd.Timestamp(z[0]),tf=z[1],tv_Open=z[2],tv_High=z[3],tv_Low=z[4],tv_Close=z[5]))
    observed=pd.DataFrame(rows).drop_duplicates(['bucket','tf']).sort_values(['tf','bucket'])
    positives=pd.DataFrame(positive).drop_duplicates(['bucket','tf'])
    comparisons=[]; events=[]
    for tf,g in observed.groupby('tf'):
        c=candles(p,int(tf))
        q=g.merge(c,on='bucket',how='left').merge(positives,on=['bucket','tf'],how='left')
        q['label_match']=q.tv_direction==q.raw_direction
        q['source_bar_available']=q.raw_direction.notna()
        q['sample']=np.where(q.bucket.dt.hour<5,'night_02_00_to_04_58','day_session')
        q['previous_policy_excluded']=(q.tv_direction!=0)&(q.direction==0)
        q['ohlc_match']=np.where(q.tv_Open.notna(),(q[['Open','High','Low','Close']].to_numpy()==q[['tv_Open','tv_High','tv_Low','tv_Close']].to_numpy()).all(axis=1),np.nan)
        comparisons.append(q)
        ev=q[q.tv_direction!=0].copy()
        ev['direction']=ev.tv_direction
        ev=pd.merge_asof(ev.sort_values('available'),x[['t','available','g']].rename(columns={'t':'g_sample_time','available':'g_available'}),left_on='available',right_on='g_available',direction='backward',tolerance=MINUTE)
        for field,key in [('g_at_bar_start','bucket'),('g_at_close','end')]:
            e=pd.merge_asof(ev[['bucket',key]].copy() if key!='bucket' else ev[['bucket']].copy(),x[['t','g']].rename(columns={'t':'sample_time','g':field}),left_on=key,right_on='sample_time',direction='backward',tolerance=MINUTE)
            ev[field]=e[field].to_numpy()
        ev['fill_t']=ev.available.dt.floor('min')+MINUTE
        ev=ev.merge(p[['t','Open','sid']].rename(columns={'t':'fill_t','Open':'fill','sid':'fill_sid'}),on='fill_t',how='left')
        ev['valid_fill']=(ev.sid==ev.fill_sid)&(ev.fill_t<ev.sid+(ev.expected-1)*MINUTE)&ev.fill.notna()
        prices=p.set_index('t')
        for h in [1,3,5,10,15]:
            et=ev.fill_t+h*MINUTE
            ep=prices.reindex(pd.DatetimeIndex(et))
            same=ep.sid.to_numpy()==ev.sid.to_numpy()
            ev[f'net_{h}m']=np.where(ev.valid_fill&same,(ep.Open.to_numpy()-ev.fill.to_numpy())*ev.direction.to_numpy()-4,np.nan)
        events.append(ev)
    checks=pd.concat(comparisons,ignore_index=True)
    all_events=pd.concat(events,ignore_index=True).sort_values(['tf','bucket'])
    checks[['bucket','tf','sample','tv_direction','raw_direction','direction','warm_count','source_bar_available','previous_policy_excluded','label_match','ohlc_match']].to_csv(OUT/'tv_label_concordance.csv',index=False)
    columns=['bucket','tf','sample','tv_direction','Open','High','Low','Close','label_match','ohlc_match','end','available','g_at_bar_start','g_at_close','g_sample_time','g','fill_t','fill','valid_fill']+[f'net_{h}m' for h in [1,3,5,10,15]]
    all_events[columns].to_csv(OUT/'tv_signal_guerrilla_audit.csv',index=False)
    summaries=[];trade_summaries=[];all_trades=[]
    for (sample,tf),es in all_events.groupby(['sample','tf']):
        ev=es[es.valid_fill].copy()
        sids=set(ev.sid)
        for side,d in [('Buy',1),('Sell',-1)]:
            side_ev=ev[ev.direction==d]
            for threshold in [None,0,500,1000,1500,2000,2500]:
                q=side_ev if threshold is None else side_ev[side_ev.g*d>threshold]
                for h in [1,3,5,10,15]:
                    a=q[f'net_{h}m'].dropna()
                    summaries.append(dict(sample=sample,tf=tf,side=side,threshold=threshold,horizon=h,signals=len(q),measurable=len(a),mean_net=float(a.mean()) if len(a) else None,sum_net=float(a.sum()),win_rate=float((a>0).mean()) if len(a) else None))
        for k in [None,0,500,1000,1500,2000,2500]:
            # Single position replay, opposite signals always close; entry alone is gated.
            t=replay(ev,p,sids,'baseline' if k is None else 'same_sign',0 if k is None else k,int(tf),max_hold=10)
            trade_summaries.append(dict(sample=sample,tf=tf,threshold=k,**metrics(t)))
            if not t.empty:
                t['threshold']=k
                t['sample']=sample
                all_trades.append(t)
    pd.DataFrame(summaries).to_csv(OUT/'tv_threshold_forward_diagnostics.csv',index=False)
    pd.DataFrame(trade_summaries).to_csv(OUT/'tv_threshold_replay_summary.csv',index=False)
    if all_trades: pd.concat(all_trades).to_csv(OUT/'tv_threshold_trades.csv',index=False)
    result={'observed_bars':len(checks),'observed_signals':int((checks.tv_direction!=0).sum()),'matched_bars':int(checks.label_match.sum()),'missing_source_bars':int((~checks.source_bar_available).sum()),'mismatch_observable_bars':int((~checks.label_match&checks.source_bar_available).sum()),'actual_signal_mismatches':int((~checks.label_match&(checks.tv_direction!=0)).sum()),'signal_ohlc_mismatches':int((checks.ohlc_match==0).sum()),'unfillable_signals':int((~all_events.valid_fill).sum()),'previous_warmup_policy_excluded':int(checks.previous_policy_excluded.sum()),'by_sample':checks.groupby(['sample','tf']).agg(bars=('bucket','size'),signals=('tv_direction',lambda a:int((a!=0).sum())),mismatches=('label_match',lambda a:int((~a).sum()))).reset_index().to_dict('records'),'replay':trade_summaries}
    (OUT/'tv_verification_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,default=str),flush=True)
    assert (checks.ohlc_match.dropna()==1).all(), 'Signal bar prices differ.'
    for t in all_trades:
        assert np.allclose((t.exit-t.entry)*t.side,t.gross)
        assert ((t.exit_t-t.entry_t)<=10*MINUTE).all()
        assert (t.exit_t>=t.entry_t).all()

if __name__=='__main__': main()
