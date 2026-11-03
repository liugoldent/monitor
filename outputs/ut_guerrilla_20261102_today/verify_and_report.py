import json
import pandas as pd
import numpy as np
from today import OUT, read

b=json.loads((OUT/'tv_observed.json').read_text(encoding='utf-8'))
ev=pd.read_csv(OUT/'today_signal_candidates.csv',parse_dates=['bucket','fill_t'])
t=pd.read_csv(OUT/'today_trades.csv',parse_dates=['signal_time','entry_t','exit_t'])
mapping={pd.Timestamp(a[0]):a[6]-a[7] for a in b['signals']}
times=pd.date_range(b['start'],b['end'],freq='min')
checks=[]
for ts in times:
    z=ev[ev.bucket==ts]
    reconstructed=0 if z.empty else int(z.iloc[0].direction)
    checks.append(dict(time=ts,actual=mapping.get(ts,0),reconstructed=reconstructed,match=mapping.get(ts,0)==reconstructed))
check=pd.DataFrame(checks)
check.to_csv(OUT/'tv_today_concordance.csv',index=False)
assert len(check)==b['bars'] and check.match.all()
for a in b['signals']:
    z=ev[ev.bucket==pd.Timestamp(a[0])].iloc[0]
    assert [z.Open,z.High,z.Low,z.Close]==a[2:6]
assert set(t.signal_time)<=set(mapping)
assert np.allclose((t.exit-t.entry)*t.side,t.gross)
assert np.allclose(t.gross-4,t.net4)
assert ((t.exit_t-t.entry_t)<=pd.Timedelta(minutes=10)).all()
assert (t.g>500).all() and (t.side==1).all()
assert t.net4.sum()==96 and (t.net4>0).sum()==5 and (t.net4<0).sum()==3
x,_=read('mxf_value.csv');x['time']=pd.to_datetime(x.time)
day=x[(x.time>='2026-11-02 08:45')&(x.time<'2026-11-02 13:45')]
r=json.loads((OUT/'today_results.json').read_text(encoding='utf-8'))
lines=['# 2026-11-02 日盤：Buy ＋游擊 > +500','',
'沿用一分鐘 UT Bot Alerts（1／10）、普通 K 線、收棒訊號、單口不加碼。進場需當時可用的游擊 > +500；反向 UT 訊號可執行時平倉，或持有滿 10 分鐘平倉，取較早者。日盤 13:44 強制平倉。','',
'模擬結果：8 筆多單，5 勝 3 敗，勝率 62.5%。毛利 +128 點，假設每筆來回扣 4 點，共扣 32 點，淨利 +96 點，平均每筆 +12 點。成本改為 2／8 點時，分別為 +112／+64 點。沒有固定點數停損。','',
f"今天日盤游擊最高 +{int(day.mtx_bvav.max()):,}，有多段超過 +500。13:09 的 Buy 因當時游擊未達 +500 不進場；超過門檻本身也不會直接進場，仍需新的 Buy 標籤。",'',
'已直接從 TradingView 數據視窗核對 11:10～13:10，共 121 根 K 線、17 個實際正訊號，標籤狀態與訊號 OHLC 均一致，涵蓋全部 8 筆進場及各筆反向觸發出場。其他時段未逐根核對，今日候選總數由本機完整日盤行情重建取得。','',
'| 標籤時間 | 當時可用游擊 | 代理進場時間／價格 | 代理出場時間／價格 | 原因 | 扣成本點數 |',
'|---|---:|---|---|---|---:|']
for z in t.itertuples():
    why='滿10分鐘' if z.reason=='holding_limit' else '反向訊號'
    lines.append(f'| {z.signal_time:%H:%M} | +{int(z.g):,} | {z.entry_t:%H:%M}／{z.entry:,.0f} | {z.exit_t:%H:%M}／{z.exit:,.0f} | {why} | {z.net4:+.0f} |')
lines += ['',
'價格與時間假設：游擊 CSV 只有分鐘精度，記錄時間加一分鐘才視為已可用。UT 收棒與本機行情記錄時間取較晚者，再於其所在分鐘的下一分鐘開盤價模擬成交。因此 11:12 的 Buy，在此模型是 11:14 才進場；不是以 11:12 開盤或標籤價格成交。歷史標籤無法證明實際警示抵達時間。','',
'今天日盤一分鐘行情完整。資料截點固定為 2026-11-02 19:46（臺北時間）；夜盤仍在進行，本機 17:29～17:39 缺少 11 根行情，本報告不將夜盤重建候選的零成交當成完整驗證結果。','',
'這是固定規則套用到今天的單日模擬，不是實際下單紀錄，也不是跨期穩定獲利證明。前次 10/29 的 60% 勝率屬另一個日期與樣本；今日為 62.5%。','',
'逐筆紀錄：`today_trades.csv`。實際標籤原始讀取：`tv_observed.json`。逐根比對：`tv_today_concordance.csv`。價格與游擊使用本次固定快照，原始資料檔未修改。']
(OUT/'report_zh.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('121 bars and 17 actual labels matched. All 8 entry labels verified; accounting reconciled.')
