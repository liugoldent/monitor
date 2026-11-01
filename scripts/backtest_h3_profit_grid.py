"""Offline H3 profit-policy grid. Receipt-time decisions, strict next-open fills.

No broker import, no networking, and no writes to any live/simulation ledger.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

from backtest_five_strategies_next_open import PriceBar, Result


@dataclass(frozen=True)
class Policy:
    name: str
    mode: str = 'trailing'
    activation: float = 500
    giveback: float = .3
    entry_percent: float = 0


def policies():
    thresholds = (300, 500, 750, 1000, 1250, 1500, 2000)
    return ([Policy('baseline', mode='baseline')]
            + [Policy(f'trail_{t}_{g}', activation=t, giveback=g/100)
               for t in thresholds for g in (20, 30, 40, 50)]
            + [Policy(f'fixed_tp_{t}', mode='fixed_tp', activation=t) for t in thresholds]
            + [Policy('trail_entry1pct_30', entry_percent=.01)])


def read_bars(path, end):
    audit, unique = Counter(), {}
    with path.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if row.get('Symbol') != 'MXF1!' or row.get('Timeframe') != '1':
                audit['other_symbol_timeframe'] += 1
                continue
            try:
                t = datetime.fromisoformat(row['TradingView Time'])
                r = datetime.fromisoformat(row['Record Time'])
                o, h, l, c = [float(row[k]) for k in ('Open','High','Low','Close')]
                assert 0 < l <= min(o,c) <= max(o,c) <= h
            except (KeyError, ValueError, AssertionError):
                audit['invalid_rows'] += 1
                continue
            if t >= end or r > end:
                audit['after_cutoff'] += 1
                continue
            if r < t:
                audit['receipt_before_bar'] += 1
                continue
            if t in unique:
                audit['duplicate_minutes'] += 1
                if r >= unique[t].record_time:
                    continue
            unique[t] = PriceBar(t,r,o,c)
    bars = sorted(unique.values(), key=lambda b:b.bar_time)
    audit.update(unique_bars=len(bars),
        stale_over_180s=sum((b.record_time-b.bar_time).total_seconds()>180 for b in bars),
        gaps_including_regular_closures=sum((b.bar_time-a.bar_time).total_seconds()>60
                                          for a,b in zip(bars,bars[1:])))
    return bars, dict(audit)


def read_trades(path):
    entries, trades, audit = {}, [], Counter()
    with path.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            kind = row['類型']
            if not kind.startswith(('Entry','Exit')):
                audit['placeholder_rows'] += 1
                continue
            t = datetime.strptime(row['日期']+' '+row['時間'], '%Y/%m/%d %H:%M:%S')
            price, n = float(row['價格']), row['No']
            assert float(row['數量']) == 1
            if kind.startswith('Entry'):
                assert n not in entries
                entries[n] = dict(id=n, entry=t, entry_price=price,
                                  side=1 if kind == 'Entry Long' else -1)
            else:
                trade = entries.pop(n)
                pnl = (price-trade['entry_price'])*trade['side']
                assert abs(pnl*10-float(row['獲利($)'])) < 1e-8
                trades.append(dict(trade, exit=t, exit_price=price, baseline_points=pnl))
    trades.sort(key=lambda t:t['entry'])
    assert all(a['exit']<=b['entry'] for a,b in zip(trades,trades[1:]))
    audit.update(open_export_rounds_excluded=len(entries), export_closed_rounds=len(trades))
    return trades, dict(audit)


def common_market_fills(trades, bars):
    times = [b.bar_time for b in bars]
    mapped, skipped = [], []
    for t in trades:
        fills = []
        for boundary in ('entry','exit'):
            desired = t[boundary].replace(second=0,microsecond=0)+timedelta(minutes=1)
            i = bisect.bisect_left(times, desired)
            if i == len(bars) or (times[i]-desired).total_seconds()>120:
                break
            fills.append(bars[i])
        if len(fills)!=2:
            skipped.append(t['id'])
            continue
        en,ex=fills
        mapped.append(dict(t, entry=en.bar_time, exit=ex.bar_time,
            entry_price=en.open, exit_price=ex.open, baseline_points=(ex.open-en.open)*t['side']))
    return mapped, skipped


def prepare(trades,bars):
    obs=sorted(bars,key=lambda b:(b.record_time,b.bar_time))
    receipts=[b.record_time for b in obs]
    times=[b.bar_time for b in bars]
    next_open={}
    for b in obs:
        desired=b.record_time.replace(second=0,microsecond=0)+timedelta(minutes=1)
        i=bisect.bisect_left(times,desired)
        next_open[b.record_time]=bars[i] if i<len(bars) else None
    prepared=[]
    for t in trades:
        left,right=bisect.bisect_left(receipts,t['entry']),bisect.bisect_left(receipts,t['exit'])
        selected=[b for b in obs[left:right] if b.bar_time>=t['entry']]
        prepared.append((t,selected))
    return prepared,next_open


def replay(prepared, next_open, policy, cost=0):
    realized=peak_equity=max_dd=0.0
    turnover=0
    rows=[]
    months={}
    last_mark=None

    def mark(stamp, value):
        nonlocal peak_equity,max_dd,last_mark
        assert last_mark is None or stamp>=last_mark
        last_mark=stamp
        peak_equity=max(peak_equity,value)
        max_dd=max(max_dd,peak_equity-value)
        months[stamp.strftime('%Y-%m')]=value

    for t,observations in prepared:
        ep,side=t['entry_price'],t['side']
        threshold=ep*policy.entry_percent if policy.entry_percent else policy.activation
        mfe=0.0
        armed=False
        trigger=None
        pending=None
        latest_market=None
        turnover+=1
        mark(t['entry'],realized-turnover*cost)
        for bar in observations:
            if pending is not None and bar.record_time>=pending.bar_time:
                break
            if latest_market is not None and bar.bar_time<=latest_market:
                continue
            latest_market=bar.bar_time
            pnl=(bar.close-ep)*side
            mark(bar.record_time,realized+pnl-turnover*cost)
            if (bar.record_time-bar.bar_time).total_seconds()>180:
                continue
            mfe=max(mfe,pnl)
            if policy.mode=='baseline' or pending is not None:
                continue
            armed=armed or mfe>=threshold
            condition=(pnl>=threshold if policy.mode=='fixed_tp'
                       else armed and pnl<=mfe*(1-policy.giveback))
            if condition:
                candidate=next_open.get(bar.record_time)
                if candidate is not None and candidate.bar_time<t['exit']:
                    assert candidate.bar_time>bar.record_time
                    pending,trigger=candidate,bar.record_time
        exit_price=pending.open if pending else t['exit_price']
        exit_time=pending.bar_time if pending else t['exit']
        pnl=(exit_price-ep)*side
        realized+=pnl
        turnover+=1
        mark(exit_time,realized-turnover*cost)
        if exit_time<t['exit']:
            mark(t['exit'],realized-turnover*cost)
        rows.append(dict(**t, policy=policy.name, activation=threshold,
            giveback=policy.giveback if policy.mode=='trailing' else None,
            maximum_observed_profit_while_held=mfe, armed=armed,
            trigger_at=trigger, actual_exit=exit_time, actual_exit_price=exit_price,
            early_exit=pending is not None, gross_points=pnl, net_points=pnl-2*cost,
            improvement=pnl-t['baseline_points'],
            trigger_to_fill_seconds=(exit_time-trigger).total_seconds() if trigger else None))
    result=Result(policy.name,realized,0,turnover,0,[r['gross_points'] for r in rows])
    assert abs(result.gross_profit-result.gross_loss-realized)<1e-8
    assert turnover==2*len(rows)
    assert result.total==result.realized+result.unrealized
    monthly,previous={},0
    for month,value in months.items():
        monthly[month]=value-previous
        previous=value
    assert abs(sum(monthly.values())-(result.total-turnover*cost))<1e-8
    summary=dict(policy=policy.name, mode=policy.mode,
        activation_points=None if policy.entry_percent or policy.mode=='baseline' else policy.activation,
        entry_percent=policy.entry_percent,
        giveback=policy.giveback if policy.mode=='trailing' else None,
        gross_profit=result.gross_profit, gross_loss=result.gross_loss,
        profit_factor=result.profit_factor, realized=result.realized, unrealized=0,
        total=result.total, closed_legs=len(rows), one_way_turnover=turnover,
        ending_position=0, one_way_cost_points=cost, cost=turnover*cost,
        net_total=result.total-turnover*cost, max_drawdown_sampled=max_dd,
        win_rate=sum(r['gross_points']>0 for r in rows)/len(rows),
        early_exits=sum(r['early_exit'] for r in rows),
        improved_rounds=sum(r['improvement']>0 for r in rows),
        worsened_rounds=sum(r['improvement']<0 for r in rows),
        delayed_fills_over_180s=sum((r['trigger_to_fill_seconds'] or 0)>180 for r in rows),
        return_to_drawdown=(result.total-turnover*cost)/max_dd if max_dd else None,
        first_entry=prepared[0][0]['entry'], last_exit=prepared[-1][0]['exit'],
        monthly_net_points=monthly)
    return summary,rows


def write_csv(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(payload, output):
    results=payload['results']
    names=['baseline','trail_500_20','trail_500_30','trail_500_50',
           'trail_1000_20','trail_1000_30','trail_1000_40','fixed_tp_500','fixed_tp_1000']
    labels={'baseline':'原 H3','fixed_tp_500':'500 點直接停利','fixed_tp_1000':'1000 點直接停利'}
    def label(name):
        if name in labels:
            return labels[name]
        _,threshold,giveback=name.split('_')
        return f'{threshold} 點啟動／回吐 {giveback}%'
    lines=['# H3 獲利保護參數比較（2026-10-28）','',
        '永豐 2 程式已固定為 `simulation=True`，帳號核對仍保留，模擬與原帳本分開存放。這次只改模式並做離線研究，沒有啟動策略。', '',
        '## 判讀','',
        '- 若重點是減少浮盈回吐與累計權益回撤，500 點啟動、回吐 20～30% 比 1000 點啟動更貼近需求。原先 500／30 規則仍保留，未依回測自動換參數。',
        '- 若願意承受較大回撤換取行情延續機會，1000／20 是可供比較的候選；全期間最高總損益是 1000／40，但其最大回撤接近原 H3，不等於最佳獲利保護。',
        '- 「1000 點啟動回吐停利」與「1000 點直接出場」是不同規則，本次都有測。', '',
        '## 範圍與方法','',
        '固定截止 2026/10/28 00:00（台北）。主比較為 2026/4/30 09:38～10/27 23:02 的 172 筆完整 H3 交易，每筆固定 1 口。',
        '測試 300、500、750、1000、1250、1500、2000 點的啟動門檻，搭配 20%、30%、40%、50% 回吐（28 組）；同樣 7 個門檻的固定停利，加上原 H3 與進場價 1%／回吐 30% 對照，共 37 組。',
        '主比較保留 CSV 的 H3 意圖進出場價，浮盈使用 MXF1! 一分鐘 Close 在本機收到後判斷；延遲超過 180 秒的棒不可觸發。回吐比例是「最高浮盈的百分比」，不是指數百分比。停利後本輪保持空手。',
        '觸發後使用接收時間所在分鐘的下一分鐘或之後首個有資料分鐘的 Open；不使用理想停利線、訊號分鐘開盤或事後最高／最低價。固定停利也可能高於或低於門檻成交。',
        '所有表格為單口點數，未扣手續費、稅及額外滑價。最大回撤來自含浮盈虧的累計權益抽樣，不是只有已平倉損益，也不是逐筆行情最大回撤。', '',
        '## 主要比較','',
        '| 規則 | 正獲利合計 | 虧損合計（正數） | 總損益 | 最大回撤 | 獲利因子 | 提前出場 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name in names:
        a=results['all'][name]
        lines.append(f"| {label(name)} | {a['gross_profit']:,.0f} | {a['gross_loss']:,.0f} | {a['total']:,.0f} | {a['max_drawdown_sampled']:,.0f} | {a['profit_factor']:.3f} | {a['early_exits']} |")
    lines += ['', '每組皆為 172 筆已平倉、344 單邊成交口數、期末空手、未實現損益 0，因此總損益等於已實現損益。若單邊成本為 c 點，各組主比較再扣 344×c 點；不同出場時點仍可能有不同滑價。', '',
        '## 全組合：總損益／最大回撤','',
        '| 啟動門檻 | 回吐20% | 回吐30% | 回吐40% | 回吐50% |','|---|---:|---:|---:|---:|']
    for threshold in (300,500,750,1000,1250,1500,2000):
        values=[]
        for giveback in (20,30,40,50):
            a=results['all'][f'trail_{threshold}_{giveback}']
            values.append(f"{a['total']:,.0f}／{a['max_drawdown_sampled']:,.0f}")
        lines.append(f'| {threshold} | '+' | '.join(values)+' |')
    lines += ['', '## 時間切分與統一行情成交檢查','',
        '前段以 8/1 前進場的 111 筆完整交易選參數；後段為 8/3 起新進場的 61 筆，10 月新進場只有 12 筆。跨界持倉不納入後段，不混用 8 月月度權益報酬。這是事後時間切分，且 500／30 先前已看過部分歷史，不是全新獨立樣本。',
        f"只看前段總獲利，選出 {label(payload['earlier_segment_selected_by_return'])}；只看前段總損益／最大回撤，選出 {label(payload['earlier_segment_selected_by_return_to_drawdown'])}。兩種目標選出不同規則。", '',
        '| 規則 | 前段損益 | 8月起損益 | 8月起最大回撤 | 10月損益 | 統一行情損益 | 統一行情最大回撤 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name in names:
        tr,va,oc,ma=[results[w][name] for w in ('pre_august','since_august','october','common_market')]
        lines.append(f"| {label(name)} | {tr['total']:,.0f} | {va['total']:,.0f} | {va['max_drawdown_sampled']:,.0f} | {oc['total']:,.0f} | {ma['total']:,.0f} | {ma['max_drawdown_sampled']:,.0f} |")
    lines += ['',
        '統一行情敏感度檢查：把 H3 進出場也改為以 CSV 時間假定訊號可行動、下一分鐘開盤成交，超過預定分鐘 120 秒仍無行情者整輪排除，剩 168 筆、336 單邊口數、未實現 0。排除交易 663、664、703、704。這項檢查仍缺少真正 Relay 接收時間，不等於實盤回放。', '',
        '## 最近那筆 144 點空單','',
        '第 745 筆在 10/22 09:39 以 46,975 放空，原 H3 在 10/27 23:02 以 46,831 平倉，賺 144 點。全輪最大已觀測 Close 浮盈 1,445 點。', '',
        '| 規則 | 模擬出場損益 | 出場時間 |','|---|---:|---|']
    with (output/'trades.csv').open(encoding='utf-8-sig',newline='') as f:
        latest={r['policy']:r for r in csv.DictReader(f) if r['id']=='745'}
    for name in names:
        a=latest[name]
        lines.append(f"| {label(name)} | {float(a['gross_points']):,.0f} | {a['actual_exit']} |")
    lines += ['',
        '500／30 在第一段回吐時先保住 648 點；1000 點啟動的各組尚未在第一段達標，之後最高浮盈 1,445 點，重新開盤收到價格時卻只剩較小獲利，下一分鐘模擬出場剩 215 點。不能假設跨休市一定能成交在最高浮盈×保留比例。',
        '本機資料在 10/23 04:59 後下一棒為 10/27 08:45；期交所公告 10/23 因颱風休市、10/26 補假休市，這段價格跳躍包含真實休市，不能直接以保護線補成交。參考：[期交所最新公告](https://www.taifex.com.tw/cht/11/announcement)、[期交所首頁休市公告](https://www.taifex.com.tw/)。', '',
        '## 資料限制與驗證','',
        '行情始於 4/29，因此沒有回測整份 CSV 的兩年。較早 573 筆、1 筆未完成交易與占位列排除。按市場分鐘保留最早接收版本，重複 11 筆；過期棒 227 筆，分鐘間隔超過一分鐘共 633 處（包含正常休市與資料缺漏），詳見 results.json。未觀測價格可能低估回撤或漏觸發。',
        '使用連續 MXF1! 與匯出的 H3 價格，不包含真實月份轉倉成交、實際 API 到達時間、券商撮合與部分成交。參數選自小量歷史交易，最高獲利組合不保證未來最優；37 組共享相同訊號，也不是獨立分散策略。',
        '回放測試涵蓋固定停利與移動保護差異、門檻、空單對稱、下一分鐘成交、成本核對、過期行情、同輪禁止重進、H3優先出場；每個結果都核對正獲利−虧損＝已實現、已實現＋未實現＝總損益、成交口數與月權益加總。', '',
        '```powershell',
        "python scripts/backtest_h3_profit_grid.py --source 'C:\\Users\\USER\\Downloads\\h3.csv' --prices backend-futures-py/tv_doc/webhook_data_1min.csv --end '2026-10-28 00:00:00' --output docs/research/h3-profit-grid-20261028",
        'python -m unittest discover -s scripts -p test_backtest_h3_profit_grid.py -v','```','']
    (output/'README.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--prices',type=Path,required=True)
    p.add_argument('--end',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cost',type=float,default=0)
    args=p.parse_args()
    end=datetime.fromisoformat(args.end)
    bars,bar_audit=read_bars(args.prices,end)
    all_trades,trade_audit=read_trades(args.source)
    selected=[t for t in all_trades if t['entry']>=bars[0].bar_time and t['exit']<=end]
    market,skipped=common_market_fills(selected,bars)
    trade_audit.update(selected_rounds=len(selected),earlier_rounds_excluded=len(all_trades)-len(selected),
                       market_fill_excluded_ids=skipped)
    windows=dict(all=selected,
        pre_august=[t for t in selected if t['entry']<datetime(2026,8,1)],
        since_august=[t for t in selected if t['entry']>=datetime(2026,8,1)],
        october=[t for t in selected if t['entry']>=datetime(2026,10,1)],
        common_market=market)
    results={}
    flat=[]
    details=[]
    for window,trades in windows.items():
        prepared,next_open=prepare(trades,bars)
        results[window]={}
        for policy in policies():
            summary,rows=replay(prepared,next_open,policy,args.cost)
            results[window][policy.name]=summary
            flat.append(dict(window=window,**{k:v for k,v in summary.items() if k!='monthly_net_points'}))
            if window=='all':
                details.extend(dict(window=window,**r) for r in rows)
    # Freeze selection based ONLY on the earlier segment; report later segment
    # separately. This is a retrospective split, not a new independent OOS test.
    candidates=[r for r in results['pre_august'].values() if r['mode']=='trailing']
    train_return=max(candidates,key=lambda r:r['total'])['policy']
    train_ratio=max(candidates,key=lambda r:r['return_to_drawdown'])['policy']
    payload=dict(end=end,source=str(args.source),prices=str(args.prices),
        source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
        price_file_sha256=hashlib.sha256(args.prices.read_bytes()).hexdigest(),
        bar_audit=bar_audit,trade_audit=trade_audit,policy_count=len(policies()),
        earlier_segment_selected_by_return=train_return,
        earlier_segment_selected_by_return_to_drawdown=train_ratio,results=results)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    write_csv(args.output/'summary.csv',flat)
    write_csv(args.output/'trades.csv',details)
    write_report(payload,args.output)
    print(json.dumps(dict(policy_count=payload['policy_count'],bar_audit=bar_audit,
        trade_audit=trade_audit,train_return=train_return,train_ratio=train_ratio),indent=2))
    print('Top full-period trailing policies by points:')
    for r in sorted([r for r in results['all'].values() if r['mode']=='trailing'],key=lambda r:r['total'],reverse=True)[:8]:
        print(r['policy'],r['total'],r['max_drawdown_sampled'],results['since_august'][r['policy']]['total'])


if __name__=='__main__':
    main()
