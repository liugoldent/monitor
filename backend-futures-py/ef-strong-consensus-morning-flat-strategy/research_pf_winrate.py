"""Research EF consensus hysteresis with a fixed train/validation split.

This is offline-only. It never imports broker or live-monitor modules.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from strategy import ALL_STRATEGIES, PORTFOLIO_E, PORTFOLIO_F, parse_time
from hysteresis_strategy import hysteresis_target


BASE = Path(__file__).resolve().parent
SIGNALS = BASE.parent / "tv_doc" / "six_strategy_signal_events.csv"
PRICES = BASE.parent / "tv_doc" / "webhook_data_1min.csv"
START = datetime(2026, 6, 25, 8, 45)
SPLIT = datetime(2026, 8, 17)
END = datetime(2026, 9, 18, 11, 59, 2)
COST_POINTS = 2.0


@dataclass
class Metrics:
    gross_profit: float
    gross_loss: float
    profit_factor: float | None
    net_profit_factor: float | None
    closed_trades: int
    win_rate: float | None
    net_win_rate: float | None
    realized_points: float
    net_points: float
    turnover: int


def cycle(stamp: datetime):
    return (stamp - timedelta(days=1)).date() if stamp.time() < time(8, 45) else stamp.date()


def load_data():
    bars: dict[datetime, tuple[datetime, float]] = {}
    with PRICES.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Symbol") != "MXF1!":
                continue
            stamp, record = parse_time(row["TradingView Time"]), parse_time(row["Record Time"])
            if not START <= stamp < END or record > END:
                continue
            value = (record, float(row["Open"].replace(",", "")))
            if stamp not in bars or record < bars[stamp][0]:
                bars[stamp] = value

    events = []
    seen = set()
    with SIGNALS.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if not row.get("received_at"):
                continue
            stamp = parse_time(row["received_at"])
            code = row.get("strategy_code") or row.get("raw_strategy_code")
            code = "CFCWIN01m" if code == "CFCWN01m" else code
            if code not in ALL_STRATEGIES or stamp >= END:
                continue
            previous, new = int(float(row["previous_position"])), int(float(row["new_position"]))
            key = stamp, row.get("message_time"), code, previous, new
            if key in seen:
                continue
            seen.add(key)
            events.append((stamp, index, code, new))

    # Use the same conservative complete-cycle exclusions as the current-rule report.
    bad_cycles = set()
    for stamp, _, _, _ in events:
        if stamp < START or time(1) <= stamp.time() < time(8, 45):
            continue
        fill = stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        if fill not in bars:
            bad_cycles.add(cycle(stamp))
    day = START.date()
    while day <= END.date():
        previous = day - timedelta(days=1)
        if previous.weekday() < 5:
            flat = datetime.combine(day, time(1))
            if START <= flat < END and flat not in bars:
                bad_cycles.add(previous)
        day += timedelta(days=1)
    events = [event for event in events if cycle(event[0]) not in bad_cycles]
    return bars, sorted(events), sorted(str(value) for value in bad_cycles)


def desired(positions: dict[str, int], current: int, entry: int, hold: int) -> int:
    return hysteresis_target(
        positions, current, entry_threshold=entry, hold_threshold=hold
    )[0]


def replay(bars, events, start, end, entry, hold):
    positions = dict.fromkeys(ALL_STRATEGIES, 0)
    for stamp, _, code, new in events:
        if stamp >= start:
            break
        positions[code] = new
    position = 0
    entry_price = None
    pnls = []
    turnover = 0
    actions = []
    for stamp, index, code, new in events:
        if start <= stamp < end:
            morning = time(1) <= stamp.time() < time(8, 45)
            excluded = cycle(stamp) in BAD_CYCLES
            fill = stamp if morning or excluded else stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
            actions.append((fill, 1, index, code, new, morning or excluded))
    day = start.date()
    while day <= end.date():
        flat = datetime.combine(day, time(1))
        if start <= flat < end and cycle(flat) not in BAD_CYCLES:
            actions.append((flat, 0, -1, "flat", 0, False))
        day += timedelta(days=1)
    for stamp, kind, _, code, new, state_only in sorted(actions):
        if kind == 0:
            target, fill = 0, stamp
        else:
            positions[code] = new
            target = 0 if state_only else desired(positions, position, entry, hold)
            fill = stamp
        if target == position:
            continue
        price = bars[fill][1]
        if position:
            pnls.append((price - entry_price) * position)
        turnover += abs(target - position)
        position = target
        entry_price = price if target else None
    # Both windows terminate at a flat boundary for independent attribution.
    if position:
        candidates = [stamp for stamp in bars if start <= stamp < end]
        price = bars[max(candidates)][1]
        pnls.append((price - entry_price) * position)
        turnover += 1
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = -sum(value for value in pnls if value < 0)
    net_pnls = [value - 2 * COST_POINTS for value in pnls]
    net_profit = sum(value for value in net_pnls if value > 0)
    net_loss = -sum(value for value in net_pnls if value < 0)
    return Metrics(
        gross_profit, gross_loss, gross_profit / gross_loss if gross_loss else None,
        net_profit / net_loss if net_loss else None, len(pnls),
        sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        sum(value > 0 for value in net_pnls) / len(pnls) if pnls else None,
        sum(pnls), sum(pnls) - turnover * COST_POINTS, turnover,
    )


bars, events, excluded = load_data()
BAD_CYCLES = {datetime.fromisoformat(value).date() for value in excluded}
rows = []
for entry in range(1, 5):
    for hold in range(0, entry + 1):
        train = replay(bars, events, START, SPLIT, entry, hold)
        valid = replay(bars, events, SPLIT, END, entry, hold)
        full = replay(bars, events, START, END, entry, hold)
        rows.append({"entry": entry, "hold": hold, "train": asdict(train),
                     "validation": asdict(valid), "full": asdict(full)})

# Rank without looking at full-period results: validation targets PF 1.5 and win rate 50%,
# with a minimum number of closed trades and a positive training result.
def score(row):
    train, valid = row["train"], row["validation"]
    if train["net_points"] <= 0 or valid["closed_trades"] < 15:
        return 999
    return abs((valid["net_profit_factor"] or 0) - 1.5) + 2 * abs((valid["net_win_rate"] or 0) - .5)

rows.sort(key=score)
result = {
    "period": [START.isoformat(), END.isoformat()],
    "split": SPLIT.isoformat(),
    "cost_points_per_way": COST_POINTS,
    "fill": "received_at exact next-minute MXF1! Open; 01:00 exact Open",
    "rule": "enter when both E/F group nets reach entry; hold while both retain hold in position direction; flat 01:00",
    "excluded_cycles": excluded,
    "candidates": rows,
}
out = BASE / "records" / "pf_winrate_research_20260918.json"
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
for row in rows[:10]:
    print(json.dumps(row, ensure_ascii=False))
