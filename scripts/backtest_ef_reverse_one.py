"""Offline receipt-time replay of sign(E+F), capped at one contract."""
from __future__ import annotations

import argparse
import bisect
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import scan_ef_reverse as source


def replay(bars, events, start, end, direction, cost):
    times = sorted(bars)
    raw = dict.fromkeys(source.base.ALL_STRATEGIES, 0)
    actions, skipped = [], []
    for stamp, number, code, old, new in events:
        raw[code] = new
        if not start <= stamp < end:
            continue
        desired = stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        index = bisect.bisect_left(times, desired)
        actual = times[index] if index < len(times) else None
        if actual is None or actual >= end or (actual-desired).total_seconds() > 120:
            skipped.append({'receipt': str(stamp), 'code': code, 'next_bar': str(actual)})
            continue
        total = sum(raw.values())
        target = direction * ((total > 0) - (total < 0))
        actions.append((actual, number, target, stamp, code, total))
    actions.sort()
    pos = turnover = realized = 0
    entry = entered = None
    trades, months = [], {}
    peak = dd = 0.0
    cursor = 0

    def equity(price):
        return realized + ((price-entry)*pos if pos else 0) - turnover*cost

    def mark(price):
        nonlocal peak, dd
        value = equity(price)
        peak = max(peak, value)
        dd = max(dd, peak-value)
        return value

    for stamp in times:
        if not start <= stamp < end:
            continue
        while cursor < len(actions) and actions[cursor][0] == stamp:
            _, _, target, receipt, code, total = actions[cursor]
            cursor += 1
            if target == pos:
                continue
            price = bars[stamp][1]
            mark(price)
            if pos:
                pnl = (price-entry)*pos
                realized += pnl
                trades.append({'entry': str(entered), 'exit': str(stamp), 'side': pos,
                               'entry_price': entry, 'exit_price': price,
                               'gross_points': pnl, 'net_points': pnl-2*cost,
                               'exit_signal_received': str(receipt), 'code': code})
            turnover += abs(target-pos)
            pos = target
            assert abs(pos) <= 1
            entry, entered = (price, stamp) if pos else (None, None)
            mark(price)
        value = mark(bars[stamp][2])
        months[stamp.strftime('%Y-%m')] = value
    assert cursor == len(actions)
    final_stamp = max(t for t in times if start <= t < end)
    unrealized = (bars[final_stamp][2]-entry)*pos if pos else 0
    gp = sum(t['gross_points'] for t in trades if t['gross_points'] > 0)
    gl = -sum(t['gross_points'] for t in trades if t['gross_points'] < 0)
    assert abs(gp-gl-realized) < 1e-8
    assert turnover == 2*len(trades)+abs(pos)
    total = realized+unrealized
    assert abs(total-turnover*cost-equity(bars[final_stamp][2])) < 1e-8
    previous, monthly = 0, {}
    for month, value in months.items():
        monthly[month] = round(value-previous, 6)
        previous = value
    return dict(start=str(start), last_bar=str(final_stamp), gross_profit=gp, gross_loss=gl,
                realized=realized, unrealized=unrealized, total=total,
                profit_factor=gp/gl if gl else None, closed_legs=len(trades),
                net_win_rate=sum(t['net_points'] > 0 for t in trades)/len(trades) if trades else 0,
                turnover=turnover, ending_position=pos, cost=turnover*cost,
                net_total=total-turnover*cost, max_drawdown_net=dd,
                monthly_net=monthly, skipped=skipped, trades=trades)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--end', required=True)
    parser.add_argument('--cost', type=float, default=2.4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    end = datetime.fromisoformat(args.end)
    bars, events, _, audit = source.load(root, end)
    results = {}
    for label, start in [('all_recorded', events[0][0]),
                         ('since_august', datetime(2026, 8, 1)),
                         ('all_codes_observed', datetime(2026, 9, 21)),
                         ('october', datetime(2026, 10, 1)),
                         ('this_week', datetime(2026, 10, 19))]:
        results[label] = {}
        for name, direction in [('forward', 1), ('reverse', -1)]:
            results[label][name] = replay(bars, events, start, end, direction, args.cost)
        a, b = results[label].values()
        assert a['total'] == -b['total']
        assert a['turnover'] == b['turnover']
        assert abs(a['net_total']+b['net_total']+2*a['cost']) < 1e-7
    report = dict(end=str(end), cost_points_per_side=args.cost, audit=audit,
                  rules='target = +/- sign(sum of 13 EF source positions); period starts flat; '
                        'first new receipt triggers adjustment; next minute open, max 2 minute missing-bar delay; '
                        'no daily flatten, no stops, end marked at last eligible close; '
                        'MXF1! price proxy, point units, not actual fills', results=results)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (args.output/'trades.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        fields = ['period', 'direction', *results['since_august']['reverse']['trades'][0]]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for label, variants in results.items():
            for name, result in variants.items():
                writer.writerows(dict(period=label, direction=name, **t) for t in result['trades'])
    for label, variants in results.items():
        for name, result in variants.items():
            print(label, name, json.dumps({k:v for k,v in result.items() if k not in ('trades', 'skipped')}, ensure_ascii=False))
            print('skipped', len(result['skipped']))


if __name__ == '__main__':
    main()
