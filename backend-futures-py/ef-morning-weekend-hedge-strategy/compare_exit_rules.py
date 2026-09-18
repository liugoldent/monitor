"""Offline comparison only; does not import the broker or alter live state."""
import csv
import argparse
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from strategy import Calendar, STRATEGIES

BASE = Path(__file__).resolve().parent
START = datetime.fromisoformat('2026-06-25T08:45:00')
END = datetime.fromisoformat('2026-09-17T12:20:00')


def target_books(book, event, mode):
    """Books are FIFO (source, direction, entry price) tuples."""
    stamp, index, code, previous, new = event
    target = list(book)
    if code == 'flat':
        return []
    if mode == 'offset':
        if previous == 0 and new:
            match = next((i for i,b in enumerate(target) if b[1] == -new), None)
            if match is not None:
                target.pop(match)
            else:
                target.append((f'{code}/{index}',new,None))
        elif previous and new == 0:
            match = next((i for i,b in enumerate(target) if b[1] == previous), None)
            if match is not None:
                target.pop(match)
        return target
    if mode == 'own_strict':
        existing = next((b for b in target if b[0] == code), None)
        if previous == 0 and new and existing is None:
            target.append((code,new,None))
        elif previous and new == 0 and existing and existing[1] == previous:
            target.remove(existing)
        return target
    if mode == 'own':
        existing = next((b for b in target if b[0] == code), None)
        if existing and existing[1] == new:
            return target
        target = [b for b in target if b[0] != code]
        if new:
            target.append((code, new, None))
    else:
        # Only explicit exits close one oldest matching-direction leg,
        # even when the emitting strategy has no tracked position.
        if previous and new == 0:
            match = next((i for i, b in enumerate(target) if b[1] == previous), None)
            if match is not None:
                target.pop(match)
        if new and previous == 0:
            target.append((f'{code}/{index}', new, None))
    return target


def replay(events, bars, mode):
    book, closed, ledger = [], [], []
    cash = 0.0
    turnover = rejected = missing = 0
    for event in events:
        stamp, index, code, previous, new = event
        target = target_books(book, event, mode)
        old_net = sum(b[1] for b in book)
        new_net = sum(b[1] for b in target)
        if code != 'flat' and abs(new_net) > 2:
            rejected += 1
            continue
        if target == book:
            continue
        fill = stamp if code == 'flat' else stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        if fill >= END or fill not in bars:
            raise ValueError(f'Missing exact execution bar: {mode} {fill}')
        price = bars[fill]['open']
        remaining_ids = {b[0] for b in target}
        for owner, direction, entry in book:
            # Reversal replaces the same owner's direction.
            if owner not in remaining_ids or not any(b[0] == owner and b[2] is not None for b in target):
                closed.append((price - entry) * direction * 10)
        book = [(owner, direction, price if entry is None else entry) for owner, direction, entry in target]
        delta = new_net - old_net
        turnover += abs(delta)
        cash -= delta * price * 10
        ledger.append(dict(signal_time=stamp.isoformat(), fill_time=fill.isoformat(), source=code,
                           previous=old_net, target=new_net, price=price, quantity=abs(delta)))
    mark_time = max(t for t in bars if START <= t and t + timedelta(minutes=1) <= END)
    mark = bars[mark_time]['close']
    unrealized = sum((mark-entry)*direction*10 for _, direction, entry in book)
    profit = sum(x for x in closed if x > 0)
    loss = -sum(x for x in closed if x < 0)
    realized = sum(closed)
    total = realized + unrealized
    assert abs(profit-loss-realized) < 1e-6
    assert abs(total-(cash+sum(b[1] for b in book)*mark*10)) < 1e-6
    assert sum(x['target']-x['previous'] for x in ledger) == sum(b[1] for b in book)
    assert turnover == sum(x['quantity'] for x in ledger)
    return dict(gross_profit=profit, gross_loss=loss, profit_factor=profit/loss if loss else None,
                closed_legs=len(closed), realized=realized, unrealized=unrealized, total=total,
                turnover=turnover, cost_twd=turnover*20, net_twd=total-turnover*20,
                ending_position=sum(b[1] for b in book), ending_books=book,
                rejected=rejected, missing_execution_bars=missing,
                mark_time=mark_time.isoformat(), mark=mark, ledger=ledger)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offset-vs-own', action='store_true')
    args = parser.parse_args()
    calendar = Calendar.load(BASE/'config/calendar.json')
    bars, duplicates = {}, 0
    with (BASE.parent/'tv_doc/webhook_data_1min.csv').open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if row['Symbol'] != 'MXF1!':
                continue
            t = datetime.fromisoformat(row['TradingView Time'])
            if not START <= t < END:
                continue
            record = datetime.fromisoformat(row['Record Time'])
            if record > END:
                continue
            duplicates += t in bars
            # Earliest recorded copy, never a later revision chosen retrospectively.
            if t not in bars or record < bars[t]['record']:
                bars[t] = dict(open=float(row['Open'].replace(',', '')),
                               close=float(row['Close'].replace(',', '')), record=record)
    events, seen = [], set()
    audit = dict(undated=0, duplicate_signals=0, outside_window=0, excluded_strategy=0, outside_hours=0)
    with (BASE.parent/'tv_doc/six_strategy_signal_events.csv').open(encoding='utf-8-sig', newline='') as f:
        for index, row in enumerate(csv.DictReader(f)):
            if not row['received_at']:
                audit['undated'] += 1
                continue
            stamp = datetime.fromisoformat(row['received_at'])
            if not START <= stamp < END:
                audit['outside_window'] += 1
                continue
            code = row['strategy_code'] or row['raw_strategy_code']
            code = 'CFCWIN01m' if code == 'CFCWN01m' else code
            if code not in STRATEGIES:
                audit['excluded_strategy'] += 1
                continue
            if not calendar.is_open(stamp) or time(1) <= stamp.time() < time(8,45):
                audit['outside_hours'] += 1
                continue
            previous, new = int(float(row['previous_position'])), int(float(row['new_position']))
            assert previous in (-1,0,1) and new in (-1,0,1)
            key = (stamp, row['message_time'], code, previous, new)
            if key in seen:
                audit['duplicate_signals'] += 1
                continue
            seen.add(key)
            events.append((stamp,index,code,previous,new))
    audit['eligible_signals'] = len(events)
    day = START.date()
    while day <= END.date():
        closure = calendar.closure(day)
        if closure and START <= closure.start < END:
            events.append((closure.start,-1,'flat',0,0))
        day += timedelta(days=1)
    events.sort()
    def cycle(stamp):
        return (stamp-timedelta(days=1)).date() if stamp.time() < time(8,45) else stamp.date()
    gaps = []
    bad_cycles = set()
    for stamp, _, code, _, _ in events:
        fill = stamp if code == 'flat' else stamp.replace(second=0,microsecond=0)+timedelta(minutes=1)
        if fill not in bars:
            gaps.append(fill.isoformat())
            bad_cycles.add(cycle(stamp))
    audit['missing_candidate_bars'] = gaps
    audit['excluded_cycles'] = sorted(str(d) for d in bad_cycles)
    audit['signals_in_excluded_cycles'] = sum(e[2]!='flat' and cycle(e[0]) in bad_cycles for e in events)
    events = [e for e in events if cycle(e[0]) not in bad_cycles]
    audit['included_cycles'] = len({cycle(e[0]) for e in events if e[2]!='flat'})
    modes = ('offset','own_strict') if args.offset_vs_own else ('own','any')
    results = {mode: replay(events,bars,mode) for mode in modes}
    result = dict(start=START.isoformat(), end=END.isoformat(), audit=audit,
                  duplicate_price_rows=duplicates, assumptions=dict(start_flat=True,
                  signal_time='received_at', fill='exact next minute open; 01:00 exact open',
                  prices='MXF1! historical OHLC proxy, not actual TMF executions',
                  point_value_twd=10, one_way_cost_points=2, net_cap=2,
                  any_exit='Only 0->+/-1 enters; only +/-1->0 closes one FIFO same-direction leg; reversals ignored',
                  availability='OHLC delivered after bar; retrospective execution proxy, not an observed live quote'),
                  results=results)
    out=BASE/'records/exit_rule_comparison_strict_20260917.json'
    if args.offset_vs_own:
        result['assumptions'].pop('any_exit')
        result['assumptions']['offset'] = 'Only explicit entries and exits; opposite entry offsets one existing leg without retaining a new strategy position; any matching exit closes one FIFO leg'
        result['assumptions']['own_strict'] = 'Only explicit entries create a per-strategy leg; only its matching explicit exit removes that leg; rejected entries create no position'
        result['assumptions']['reversals'] = 'Ignored in both modes'
        out=BASE/'records/offset_vs_own_20260917.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({**result,'results':{k:{a:b for a,b in v.items() if a!='ledger'} for k,v in results.items()}},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
