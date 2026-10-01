"""Offline comparison of directional entry vetoes. Never connects to a broker."""
import csv
import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path

from strategy import Calendar, STRATEGIES

BASE = Path(__file__).resolve().parent
START = datetime(2026, 6, 25, 8, 45)
END = datetime(2026, 9, 30, 8, 45)  # Last complete 01:00-flat cycle.
POINT_VALUE = 10
COST_POINTS = 2  # Per contract, per side; matches backtest.py.


def cycle(stamp):
    return (stamp - timedelta(days=1)).date() if stamp.time() < time(8, 45) else stamp.date()


def fill_time(stamp, flat=False):
    return stamp if flat else stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)


def load():
    calendar = Calendar.load(BASE / "config/calendar.json")
    bars = {}
    with (BASE.parent / "tv_doc/webhook_data_1min.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["Symbol"] != "MXF1!":
                continue
            stamp = datetime.fromisoformat(row["TradingView Time"])
            if not START <= stamp < END:
                continue
            recorded = datetime.fromisoformat(row["Record Time"])
            if recorded > END:
                continue
            if stamp not in bars or recorded < bars[stamp][0]:
                bars[stamp] = (recorded, float(row["Open"].replace(",", "")),
                               float(row["Close"].replace(",", "")))

    events = defaultdict(list)
    audit = defaultdict(int)
    with (BASE.parent / "tv_doc/six_strategy_signal_events.csv").open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if not row["received_at"]:
                audit["undated"] += 1
                continue
            stamp = datetime.fromisoformat(row["received_at"])
            if not START <= stamp < END:
                continue
            code = row["strategy_code"] or row["raw_strategy_code"]
            code = "CFCWIN01m" if code == "CFCWN01m" else code
            if code not in STRATEGIES:
                audit["excluded_strategy"] += 1
                continue
            if not calendar.is_open(stamp) or time(1) <= stamp.time() < time(8, 45):
                audit["outside_hours"] += 1
                continue
            new = int(float(row["new_position"]))
            assert new in (-1, 0, 1)
            events[cycle(stamp)].append((stamp, index, code, new))
    day = START.date()
    while day <= END.date():
        closure = calendar.closure(day)
        if closure and START <= closure.start < END:
            events[cycle(closure.start)].append((closure.start, -1, "flat", 0))
        day += timedelta(days=1)

    valid = {}
    excluded = {}
    for day, rows in sorted(events.items()):
        rows.sort()
        if not any(code != "flat" for _, _, code, _ in rows):
            continue
        missing = sorted({fill_time(stamp, code == "flat") for stamp, _, code, _ in rows
                          if fill_time(stamp, code == "flat") not in bars})
        if missing or not any(code == "flat" for _, _, code, _ in rows):
            excluded[str(day)] = [str(x) for x in missing] or ["missing 01:00 flat"]
        else:
            valid[day] = rows
    audit["candidate_cycles"] = len(valid) + len(excluded)
    audit["included_cycles"] = len(valid)
    audit["excluded_cycles"] = excluded
    audit["included_signals"] = sum(code != "flat" for rows in valid.values()
                                    for _, _, code, _ in rows)
    return valid, bars, dict(audit)


def replay(events, bars, mode):
    cash = turnover = 0.0
    orders = exits_by_rule = reentries = 0
    daily = []
    fill_states = {}
    for day, rows in events.items():
        legs = dict.fromkeys(STRATEGIES, 0)
        held = 0
        paused_long = False
        prior_long_pause = False
        bearish_veto_legs = set()
        bullish_veto_legs = set()
        day_cash_start = cash
        day_orders_start = orders
        for stamp, _, code, new in rows:
            previous = legs[code] if code != "flat" else 0
            if code == "flat":
                legs = dict.fromkeys(STRATEGIES, 0)
                paused_long = False
                bearish_veto_legs.clear()
                bullish_veto_legs.clear()
                target = 0
            else:
                legs[code] = new
                net = sum(legs.values())
                long_exit = previous == 1 and new == 0
                new_long = previous != 1 and new == 1
                if previous == 0 and new == -1:
                    bearish_veto_legs.add(code)
                elif new != -1:
                    bearish_veto_legs.discard(code)
                if previous == 0 and new == 1:
                    bullish_veto_legs.add(code)
                elif new != 1:
                    bullish_veto_legs.discard(code)
                if mode in ("exit_1_to_0", "both") and held > 0 and long_exit:
                    paused_long = True
                elif new_long:
                    paused_long = False
                long_veto = mode in ("entry_0_to_minus_1", "both", "symmetric_veto") and bool(bearish_veto_legs)
                short_veto = mode == "symmetric_veto" and bool(bullish_veto_legs)
                target = (1 if net > 0 and not paused_long and not long_veto else
                          -1 if net < 0 and not short_veto else 0)
                if held > 0 and target == 0 and net > 0:
                    exits_by_rule += 1
                    prior_long_pause = True
                if prior_long_pause and target > 0:
                    reentries += 1
                    prior_long_pause = False
            if target != held:
                filled_at = fill_time(stamp, code == "flat")
                price = bars[filled_at][1]
                delta = target - held
                cash -= delta * price * POINT_VALUE + abs(delta) * COST_POINTS * POINT_VALUE
                turnover += abs(delta)
                orders += 1
                held = target
                fill_states[filled_at] = (cash, held)
        assert held == 0, f"cycle {day} not flat"
        daily.append((str(day), cash - day_cash_start, orders - day_orders_start))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for _, pnl, _ in daily:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    minute_cash = minute_position = minute_peak = minute_drawdown = 0.0
    included_days = set(events)
    for stamp, (_, _, close) in sorted(bars.items()):
        if cycle(stamp) not in included_days:
            continue
        if stamp in fill_states:
            minute_cash, minute_position = fill_states[stamp]
        marked_equity = minute_cash + minute_position * close * POINT_VALUE
        minute_peak = max(minute_peak, marked_equity)
        minute_drawdown = max(minute_drawdown, minute_peak - marked_equity)
    return {"net_twd": round(cash), "orders": orders, "turnover_contracts": int(turnover),
            "cost_twd": int(turnover * COST_POINTS * POINT_VALUE),
            "rule_forced_exits": exits_by_rule, "long_reentries": reentries,
            "minute_close_max_drawdown_twd": round(minute_drawdown),
            "end_of_cycle_max_drawdown_twd": round(max_drawdown),
            "winning_cycles": sum(pnl > 0 for _, pnl, _ in daily),
            "losing_cycles": sum(pnl < 0 for _, pnl, _ in daily),
            "daily": daily}


def main():
    events, bars, audit = load()
    modes = ("original", "exit_1_to_0", "entry_0_to_minus_1", "both", "symmetric_veto")
    results = {mode: replay(events, bars, mode) for mode in modes}
    output = {"period": [START.isoformat(), END.isoformat()], "audit": audit,
              "assumptions": {"start_and_end_flat_each_cycle": True,
                              "signals": "dated CSV rows received after 08:45; tracked JSON legs reset at cycle start",
                              "exits": "legacy modes affect longs only; symmetric_veto applies to both sides",
                              "1_to_0": "tracked +1 to 0 while long; pause long until a fresh tracked long entry",
                              "0_to_minus_1": "tracked 0 to -1 creates a long veto until that leg leaves -1",
                              "0_to_plus_1": "tracked 0 to +1 creates a short veto until that leg leaves +1",
                              "fill": "MXF1! exact next-minute Open; flat exact 01:00 Open; not TMF fills",
                              "price_rows": "earliest recorded copy at each TradingView Time",
                              "cost": "2 points per contract per side; NT$10 per point",
                              "drawdown": "minute-close marks on available MXF1! bars and separate cycle-end equity"},
              "results": results}
    path = BASE / "records/long_exit_veto_comparison_20260930.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**output, "results": {name: {k: v for k, v in result.items() if k != "daily"}
                                              for name, result in results.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
