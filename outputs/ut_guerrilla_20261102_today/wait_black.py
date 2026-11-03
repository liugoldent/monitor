"""Compare the same eight verified Buy setups, with entry after a later bearish close."""
import json
import numpy as np
import pandas as pd
from today import OUT, read, MINUTE, metrics

DAY=pd.Timestamp('2026-11-02 08:45')
TERMINAL=pd.Timestamp('2026-11-02 13:44')

def choose_entry(buy, black_bars, sell_available):
    """Use only a later candle observed after Buy; a Sell cancels the pending setup."""
    q=black_bars[(black_bars.t>buy.bucket)&(black_bars.available>=buy.available)]
    if q.empty:return None,'no_later_black'
    red=q.iloc[0]
    if sell_available is not None and sell_available<=red.available:
        return None,'sell_before_or_on_black'
    fill_t=red.available.floor('min')+MINUTE
    if sell_available is not None and sell_available<fill_t:
        return None,'sell_before_entry'
    if fill_t>=TERMINAL:return None,'session_ended'
    return red,None

def self_check():
    b=pd.Series(dict(bucket=pd.Timestamp('2026-11-02 11:12'),available=pd.Timestamp('2026-11-02 11:13:05')))
    reds=pd.DataFrame(dict(t=pd.to_datetime(['2026-11-02 11:12','2026-11-02 11:13']),available=pd.to_datetime(['2026-11-02 11:13:05','2026-11-02 11:14:05'])))
    a,why=choose_entry(b,reds,pd.Timestamp('2026-11-02 11:14:05'))
    assert a is None and why=='sell_before_or_on_black'
    a,why=choose_entry(b,reds,pd.Timestamp('2026-11-02 11:18:05'))
    assert why is None and a.t==pd.Timestamp('2026-11-02 11:13')
    assert a.available.floor('min')+MINUTE==pd.Timestamp('2026-11-02 11:15')

def main():
    self_check()
    p,_=read('webhook_data_1min.csv')
    p['t']=pd.to_datetime(p['TradingView Time']);p['r']=pd.to_datetime(p['Record Time'])
    p=p[(p.t>=DAY)&(p.t<=TERMINAL)&(p.Symbol=='MXF1!')].sort_values(['t','r']).drop_duplicates('t').copy()
    assert len(p)==300 and list(p.t)==list(pd.date_range(DAY,TERMINAL,freq='min'))
    p['available']=pd.concat([p.r,p.t+MINUTE],axis=1).max(axis=1)
    price=p.set_index('t')
    black=p[p.Close<p.Open]
    signals=pd.read_csv(OUT/'today_signal_candidates.csv',parse_dates=['bucket','available','fill_t'])
    setups=signals[(signals.bucket>=DAY)&(signals.bucket<=TERMINAL)&(signals.direction==1)&(signals.g>500)&signals.valid_fill].copy()
    assert len(setups)==8
    baseline=pd.read_csv(OUT/'today_trades.csv',parse_dates=['signal_time','entry_t','exit_t'])
    assert set(setups.bucket)==set(baseline.signal_time)
    obs=json.loads((OUT/'tv_observed.json').read_text(encoding='utf-8'))
    actual={pd.Timestamp(a[0]):a[6]-a[7] for a in obs['signals']}
    trades=[];decisions=[]
    for _,b in setups.iterrows():
        assert actual.get(b.bucket)==1
        opposite=signals[(signals.bucket>b.bucket)&(signals.bucket<=TERMINAL)&(signals.direction==-1)].sort_values('available')
        sell=None if opposite.empty else opposite.iloc[0]
        if sell is not None:assert actual.get(sell.bucket)==-1
        red,reason=choose_entry(b,black,None if sell is None else sell.available)
        common=dict(signal_time=b.bucket,g_at_buy=b.g,baseline_entry=b.fill_t)
        if red is None:
            decisions.append(dict(**common,status='cancelled',reason=reason,sell_signal_time=None if sell is None else sell.bucket))
            continue
        entry_t=red.available.floor('min')+MINUTE
        limit=entry_t+10*MINUTE
        candidates=[(limit,'holding_limit'),(TERMINAL,'scheduled_flat')]
        if sell is not None:candidates.append((sell.available.floor('min')+MINUTE,'opposite_signal'))
        exit_t,exit_reason=min(candidates,key=lambda z:z[0])
        assert entry_t in price.index and exit_t in price.index and exit_t>=entry_t
        entry=float(price.loc[entry_t,'Open']);exit_price=float(price.loc[exit_t,'Open']);gross=exit_price-entry
        trades.append(dict(sid=DAY,signal_time=b.bucket,black_time=red.t,black_available=red.available,black_open=red.Open,black_close=red.Close,g=b.g,entry_t=entry_t,exit_t=exit_t,entry=entry,exit=exit_price,side=1,gross=gross,net4=gross-4,reason=exit_reason))
        decisions.append(dict(**common,status='entered',black_time=red.t,entry_t=entry_t,exit_t=exit_t,net4=gross-4))
    t=pd.DataFrame(trades)
    t.to_csv(OUT/'wait_black_trades.csv',index=False)
    pd.DataFrame(decisions).to_csv(OUT/'wait_black_decisions.csv',index=False)
    if not t.empty:
        assert (t.black_close<t.black_open).all()
        assert (t.black_time>t.signal_time).all()
        assert (t.entry_t>t.black_available).all()
        assert ((t.exit_t-t.entry_t)<=10*MINUTE).all()
        assert (t.g>500).all()
        assert np.allclose(t.exit-t.entry,t.gross)
        assert np.allclose(t.gross-4,t.net4)
        z=t.sort_values('entry_t')
        assert (z.entry_t.iloc[1:].to_numpy()>=z.exit_t.iloc[:-1].to_numpy()).all()
    result={'date':'2026-11-02','session':'day','buy_guerrilla_check':'Buy availability only; do not recheck when black closes','black_definition':'Close < Open; strictly later candle than Buy','cancel_rule':'Sell available before/on black confirmation or before queued entry cancels setup','execution':'floor(black available minute)+1 minute open','baseline':metrics(baseline),'wait_black':metrics(t),'setups':len(setups),'cancelled':len(setups)-len(t),'decisions':decisions}
    (OUT/'wait_black_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    lines=['# 2026-11-02：Buy＋游擊 > +500，等收黑進場','',
    '只在原 Buy 確認時檢查游擊 > +500，之後不再檢查游擊。Buy 所屬 K 線之後，等待第一根 Close < Open 的一分鐘黑 K 收完，收到資料後下一分鐘開盤價代理進多。Buy 那根本身不算，十字線不算黑 K。','',
    '等待中如果先出現 Sell，包含該根黑 K 同時出現 Sell，便取消本次 Buy，不跨過 Sell 繼續等。每個 Buy 最多進一次，不加碼。','',
    '出場沿用反向 UT 訊號可執行時、進場後滿 10 分鐘、13:44 日盤強制平倉，取最早者。每筆來回假設扣 4 點成本。沒有固定點數停損、停利。','',
    '| 規則 | 筆數 | 勝率（扣成本） | 毛利 | 毛損 | 毛淨利 | 扣成本淨點數 | PF（扣成本） |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for label,m in [('原本直接進場',result['baseline']),('等後續第一根黑 K',result['wait_black'])]:
        lines.append(f"| {label} | {m['trades']} | {m['win_rate_net4']:.1%} | {m['gross_profit']:.0f} | {m['gross_loss']:.0f} | {m['gross']:.0f} | {m['net4']:+.0f} | {m['pf_net4']:.2f} |")
    lines += ['',f"原始 8 個 Buy 設定中，等待黑 K 版成交 {len(t)} 筆、取消 {len(setups)-len(t)} 筆；期末空倉，未實現損益為零。",'',
    '| Buy 時間 | 當時游擊 | 黑 K 時間 | 進場時間／價格 | 出場時間／價格 | 原因 | 扣成本點數 |','|---|---:|---|---|---|---|---:|']
    for a in t.itertuples():
        why='滿10分鐘' if a.reason=='holding_limit' else ('反向訊號' if a.reason=='opposite_signal' else '收盤平倉')
        lines.append(f'| {a.signal_time:%H:%M} | +{a.g:.0f} | {a.black_time:%H:%M} | {a.entry_t:%H:%M}／{a.entry:,.0f} | {a.exit_t:%H:%M}／{a.exit:,.0f} | {why} | {a.net4:+.0f} |')
    lines += ['',
    '本比較固定使用與前一輪相同的資料快照及同一天的 8 個 Buy；Buy／Sell 標籤已由 TradingView 11:10～13:10 的逐根讀取核對。黑 K 取自該日完整 300 根一分鐘行情。價格可用時間取行情記錄與收棒時間較晚者；不是看到事後黑 K 就回到該根開盤成交。','',
    '這是單日、單口的模擬結果；持有 10 分鐘上限從延後進場時重新計算，所以出場時間也可能改變，不能把差額全歸因於買價改善。等待 Sell 取消是本次明確採用的假設。','',
    '成本 2／4／8 點情境與所有逐筆紀錄保留於 wait_black_results.json、wait_black_trades.csv；取消明細在 wait_black_decisions.csv。']
    (OUT/'report_wait_black_zh.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,default=str),flush=True)

if __name__=='__main__':main()
