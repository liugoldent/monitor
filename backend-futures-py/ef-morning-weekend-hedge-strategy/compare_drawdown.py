"""Mark saved comparison ledgers at each available minute close, including costs."""
import csv
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent


def curve(ledger, bars, start):
    cash = position = cursor = peak = maximum = 0
    peak_time = start
    worst = {}
    rows = []
    for stamp, price in sorted(bars.items()):
        while cursor < len(ledger) and datetime.fromisoformat(ledger[cursor]['fill_time']) <= stamp:
            event = ledger[cursor]
            assert event['previous'] == position
            delta = event['target']-position
            cash -= delta*event['price']*10 + abs(delta)*20
            position = event['target']
            cursor += 1
        equity = cash+position*price*10
        if equity > peak:
            peak, peak_time = equity, stamp
        drawdown = peak-equity
        if drawdown > maximum:
            maximum = drawdown
            worst = dict(peak_time=peak_time.isoformat(), trough_time=stamp.isoformat(),
                         peak_equity=peak, trough_equity=equity)
        rows.append(dict(time=stamp.isoformat(),equity=equity,drawdown=drawdown,position=position))
    assert cursor == len(ledger)
    recovery = next((r['time'] for r in rows if r['time'] > worst['trough_time'] and r['equity'] >= worst['peak_equity']), None) if worst else None
    return dict(max_drawdown=maximum, **worst, recovery_time=recovery,
                ending_equity=rows[-1]['equity'], observations=len(rows)), rows


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--offset-vs-own',action='store_true')
    args=parser.parse_args()
    stem = 'offset_vs_own_20260917' if args.offset_vs_own else 'exit_rule_comparison_strict_20260917'
    suffix = 'offset_vs_own_20260917' if args.offset_vs_own else 'strict_20260917'
    saved = json.loads((BASE/f'records/{stem}.json').read_text(encoding='utf-8'))
    start, end = map(datetime.fromisoformat, (saved['start'],saved['end']))
    records, bars = {}, {}
    with (BASE.parent/'tv_doc/webhook_data_1min.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if r['Symbol'] != 'MXF1!':
                continue
            stamp, received = map(datetime.fromisoformat,(r['TradingView Time'],r['Record Time']))
            if not start <= stamp or stamp+timedelta(minutes=1)>end or received>end:
                continue
            if stamp not in records or received<records[stamp]:
                records[stamp] = received
                bars[stamp] = float(r['Close'].replace(',',''))
    result = dict(start=saved['start'],end=saved['end'],
                  method='Available minute closes, net cash plus marked position, 20 TWD per one-way contract; initial equity 0',
                  limitation='Same six excluded cycles as comparison; missing minute marks and intraminute extremes are not measured; no percentage without starting capital',results={})
    for mode, replay in saved['results'].items():
        summary, rows = curve(replay['ledger'],bars,start)
        assert abs(summary['ending_equity']-replay['net_twd']) < 1e-6
        summary['profit_to_max_drawdown'] = replay['net_twd']/summary['max_drawdown']
        result['results'][mode] = summary
        with (BASE/f'records/equity_{mode}_{suffix}.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (BASE/f'records/drawdown_{suffix}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
