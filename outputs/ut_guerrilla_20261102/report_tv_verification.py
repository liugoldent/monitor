"""Summarize direct TradingView UI observations, separate from reconstructed history."""
from pathlib import Path
import json
import pandas as pd
import numpy as np

OUT=Path(__file__).resolve().parent
r=json.loads((OUT/'tv_verification_results.json').read_text(encoding='utf-8'))
summary=pd.read_csv(OUT/'tv_threshold_replay_summary.csv')
t=pd.read_csv(OUT/'tv_threshold_trades.csv',parse_dates=['entry_t','exit_t','sid'])
events=pd.read_csv(OUT/'tv_signal_guerrilla_audit.csv')
checks=pd.read_csv(OUT/'tv_label_concordance.csv')
old=pd.read_csv(OUT/'short_hold_trades.csv')
old=old[(old.scope=='day_and_night')&(old['mode']=='same_sign')&(old.tf.isin([1,2,3]))&(old.threshold.isin([1000,1500,2000]))].copy()
old['period']=np.where(old.sid.str.slice(0,10)>='2026-09-01','holdout','train')
side=old.groupby(['tf','threshold','side','period']).agg(trades=('net4','size'),net4=('net4','sum'),mean_net4=('net4','mean')).reset_index()
side.to_csv(OUT/'reconstructed_history_side_breakdown.csv',index=False)

def fmt(x):
    if pd.isna(x):return '—'
    return f'{x:,.0f}'

lines=['# TradingView 實際歷史標籤 × 游擊門檻核對','',
'已直接從你登入的 TradingView 數據視窗逐根讀取 540 根 K 線、65 個歷史 Buy／Sell 標籤。這次的逐筆樣本回測使用實際標籤，不以重建訊號代替。','',
'目前的證據支持把「Buy 的正門檻」與「Sell 的負門檻」、日盤與夜盤分開研究。10 月 29 日日盤的負游擊篩選改善了部分結果；同一天凌晨的正游擊 > +2,000 並未改善到正收益。尚未找到可跨月份採用的固定門檻。','',
'## 實際讀取範圍','',
'| 區段（UTC+8） | 分盤 | 核對 K 線 | 實際標籤 |','|---|---:|---:|---:|',
'| 2026-10-29 08:45～13:44 日盤 | 1 分鐘 | 300 | 38 |',
'| 2026-10-29 08:45～13:43 日盤 | 2 分鐘 | 150 | 16 |',
'| 2026-10-29 02:00～04:58，屬 10/28 起始的夜盤 | 2 分鐘 | 90 | 11 |','',
'這些日期／區段因游擊曾出現較大正、負值而選取，不是隨機樣本，也未在選取前保留為獨立測試資料。因此門檻差異只能作為樣本內的描述。','',
'## 公平比較的進出與成本','',
'- UT Bot Alerts：Key Value＝1、ATR＝10，普通 K 線，沒有 Heikin Ashi。等待 K 線收完。',
'- Buy 只在當時可用的游擊大於正門檻時進多；Sell 只在游擊小於負門檻時進空。等於門檻不進場。',
'- 收到反向訊號便先平倉，反手進場另外檢查門檻。每次最多一口、不加碼。',
'- 每筆最多持有 10 分鐘，日盤 13:44、夜盤 04:59 的開盤價代理強制平倉。夜盤片段以 02:00 空手開始，沒有歸入更早進場的部位。',
'- 由本機該根 K 線的記錄／收棒時間估計歷史訊號可用時刻，再取下一分鐘開盤價代理成交。不是在標籤那根 K 線的開盤進場，也不是已證實的實際警示收到時間。',
'- 游擊記錄只有分鐘精度，因此記錄時間加一分鐘才視為可用。向前查找，不使用後來的游擊值。逐筆表另列 K 線開始時與收棒時的原始游擊，方便檢查對時差異。',
'- 每筆來回假設扣 4 點成本。所有結果是每口指數點數，另保留成本 2／8 點情境。沒有固定點數停損；持有上限不能限制最大虧損。','',
'## 當日門檻比較：實際標籤的單一部位重播','',
'同向門檻 K 指 Buy 要游擊 > +K、Sell 要游擊 < −K；不是把所有負游擊值當成做空訊號。','',
'| 門檻 | 一分鐘日盤 筆數／淨點數 | 二分鐘日盤 筆數／淨點數 | 二分鐘凌晨片段 筆數／淨點數 |','|---|---:|---:|---:|']
for k in [None,0,500,1000,1500,2000,2500]:
    cells=[]
    for sample,tf in [('day_session',1),('day_session',2),('night_02_00_to_04_58',2)]:
        z=summary[(summary['sample']==sample)&(summary.tf==tf)&(summary.threshold.isna() if k is None else summary.threshold.eq(k))].iloc[0]
        cells.append(f"{int(z.trades)}／{fmt(z.net4)}")
    lines.append('| '+('不篩選' if k is None else f'±{k:,}')+' | '+' | '.join(cells)+' |')
lines += ['',
'提高門檻不一定提高總收益：一分鐘日盤的 ±500 為 +189 點，±2,000 為 +165 點；二分鐘日盤不篩選為 +299 點，±2,000 只剩 +82 點。±2,000 的每筆品質在這個日盤樣本較好，但樣本分別只有 4 筆、2 筆。','',
'## 門檻 ±2,000 下的實際逐筆進出','',
'下表的時間是標籤所屬 K 線的開始時間，游擊是估計訊號可用時刻向前查找到的可用記錄。','',
'| 分盤／盤段 | 標籤時間 | 方向 | 當時可用游擊 | 代理進場 | 代理出場 | 扣成本點數 |','|---|---|---|---:|---|---|---:|']
q=t[t.threshold.eq(2000)].sort_values(['tf','entry_t'])
for z in q.itertuples():
    ev=events[(events.tf==z.tf)&(events.fill_t==str(z.entry_t))].iloc[0]
    label=pd.Timestamp(ev.bucket).strftime('%H:%M')
    segment='日盤' if z.sample=='day_session' else '凌晨'
    lines.append(f"| {z.tf} 分鐘／{segment} | {label} | {'Buy' if z.side>0 else 'Sell'} | {fmt(z.g)} | {z.entry_t.strftime('%H:%M')}，{fmt(z.entry)} | {z.exit_t.strftime('%H:%M')}，{fmt(z.exit)} | {fmt(z.net4)} |")
lines += ['',
'一分鐘日盤的 4 筆 Sell 合計 +165 點；二分鐘日盤的 2 筆 Sell 合計 +82 點；凌晨片段的 3 筆 Buy 合計 −209 點。這些策略共用行情與部分訊號，不能相加視為獨立、分散風險的投資組合。','',
'## 核對發現的資料與重建差異','',
'- 日盤一、二分鐘合計 450 根 K 線的標籤狀態完全一致，54 個訊號的 OHLC 完全一致。',
'- 凌晨片段的 11 個真實標籤，其價格與重建方向均一致；但當地 04:06 的二分鐘 K 線缺少完整一分鐘來源，無法完整比較。',
'- 缺棒後的重新初始化使重建在 04:38 多算一個 Sell。TradingView 數據視窗實際是 Buy＝0、Sell＝0。這一個重建訊號未納入本次實際標籤回測。',
'- 全部 540 根讀取值中，538 根可以直接匹配，一根缺少來源，一根重建出現額外訊號。全部 65 個實際正訊號均匹配；這不等於全部零訊號也匹配。',
'- 04:04 的 Sell 無法在指定延後時點取得一分鐘開盤價，因此列為不可成交代理，沒有偷偷改用訊號當根開盤或較晚價格；凌晨不篩選結果只含其餘 10 筆。該訊號不影響游擊 > +2,000 的 3 筆多單比較。',
'- 舊回測的前 50 根暖機保護，在這些樣本排除 4 個真實訊號，其中日盤二分鐘有 3 個、凌晨另有 1 個。因此舊表是暖機後的子樣本，不能稱作全部 TradingView 標籤的績效。',
'- 本次同時保留實際標籤方向、未套暖機的重建方向、舊回測方向、來源可用狀態與暖機根數。這些差異仍限制了把單日一致性外推到全部歷史的可信度。','',
'## 較長期間的補充：仍是重建子樣本','',
'以下來自前一輪固定資料的重建回測，不是已直接逐根讀取的完整 TradingView 歷史。它採相同 10 分鐘持有上限、每筆來回 4 點成本，且排除不完整盤段與暖機期。用來檢查單日觀察能否跨期維持，不能取代更多實際標籤核對。','',
'| 分盤／條件 | 9 月以前 筆數／淨點數 | 9 月以後 筆數／淨點數 |','|---|---:|---:|']
for tf,d in [(1,-1),(2,-1),(3,-1),(1,1),(2,1),(3,1)]:
    cells=[]
    for period in ['train','holdout']:
        z=side[(side.tf==tf)&(side.threshold==2000)&(side.side==d)&(side.period==period)].iloc[0]
        cells.append(f'{int(z.trades)}／{fmt(z.net4)}')
    lines.append(f"| {tf} 分鐘，{'Sell 且游擊 < −2,000' if d<0 else 'Buy 且游擊 > +2,000'} | {' | '.join(cells)} |")
lines += ['',
'一分鐘深負游擊的空單在前段較好，後段卻轉負。三分鐘 Sell／−2,000 後段雖為正收益，只有 7 筆。這些結果尚不足以選出可直接實盤使用的最佳門檻。','',
'## 目前可以確定的範圍','',
'可以確認：你觀察到的「達到一定數值後，部分 TV 訊號品質較好」在選取的日盤樣本確實存在。尚不能確認：±2,000 是通用值、Buy 與 Sell 應同樣處理，或門檻越大越好。','',
'後續研究應固定收棒、訊號延遲、出場與成本，再分開評估 Buy 正門檻、Sell 負門檻及日／夜盤；對每個候選使用未參與挑選的新日期驗證。先解決行情缺棒造成的重建假訊號，才能擴大自動比對的涵蓋率。','',
'## 檔案','',
'- `tv_verified_blocks.json`：逐根讀取範圍與實際正訊號。區段內未列出的 K 線已觀察為 Buy＝0、Sell＝0。',
'- `tv_label_concordance.csv`：540 根 K 線的比對狀態。',
'- `tv_signal_guerrilla_audit.csv`：65 個實際標籤、OHLC、游擊對時與後續點數診斷。',
'- `tv_threshold_trades.csv`：各門檻重播的逐筆進出。',
'- `tv_threshold_replay_summary.csv`：分盤、時段、門檻的筆數、毛利、毛損、淨收益、PF、單邊成交量及期末部位。',
'- `tv_threshold_forward_diagnostics.csv`：固定持有 1／3／5／10／15 分鐘的訊號診斷。其觀測會互相重疊，不能把加總視為單一部位可實現績效。','',
'資料截點與快照：沿用前次固定至 2026-11-02 18:49 的 CSV 快照，原始行情與游擊檔未修改。','',
'圖表來源：https://tw.tradingview.com/chart/rABGcFih/?symbol=TAIFEX%3AMXF1%21','']
(OUT/'report_tv_verified_zh.md').write_text('\n'.join(lines),encoding='utf-8')
# Accounting and side-filter checks independent of displayed summary formatting.
assert np.allclose(summary.gross_profit-summary.gross_loss,summary.gross)
assert np.allclose(summary.gross-4*summary.trades,summary.net4)
assert (summary.one_way_turnover==2*summary.trades).all()
assert (summary.ending_position==0).all() and (summary.unrealized==0).all()
assert np.allclose((t.exit-t.entry)*t.side,t.gross)
assert ((t.exit_t-t.entry_t)<=pd.Timedelta(minutes=10)).all()
assert (q.side*np.sign(q.g)==1).all() and (q.g.abs()>2000).all()
assert r['observed_bars']==sum(b['bars'] for b in json.loads((OUT/'tv_verified_blocks.json').read_text(encoding='utf-8')))
print('Saved direct-label report; trade reconciliation passed. Recorded data discrepancies are disclosed.')
