"""Research replay: prior entry veto versus veto by every active opposing leg.

Uses the production backtest's received_at, next-minute MXF1! Open, exact
01:00 Open, one-contract clamp, and two-point one-way cost assumptions.
Cycles with any missing required fill bar are excluded for both policies.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from strategy import Calendar, STRATEGIES, clamped_target_direction, integer


BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
START = datetime(2026, 6, 24, 8, 45)
END = datetime(2026, 10, 1, 21, 5, 8)  # Exclusive; 21:04 bar arrived at 21:05:07.
POINT_VALUE = 10
ONE_WAY_COST_POINTS = 2.0


def cycle_day(stamp):
    return stamp.date() if stamp.time() >= time(8, 45) else stamp.date() - timedelta(days=1)


def next_open(stamp):
    return stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)


def load_bars():
    bars = {}
    audit = Counter()
    path = BACKEND / "tv_doc/webhook_data_1min.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Symbol") != "MXF1!":
                continue
            try:
                stamp = datetime.fromisoformat(row["TradingView Time"])
                if not START <= stamp < END:
                    continue
                record = datetime.fromisoformat(row["Record Time"])
                opening = float(row["Open"].replace(",", ""))
                closing = float(row["Close"].replace(",", ""))
            except (KeyError, ValueError):
                audit["invalid_bar_rows"] += 1
                continue
            if record > END:
                audit["bars_recorded_after_end"] += 1
                continue
            audit["eligible_bar_rows"] += 1
            if stamp in bars:
                audit["duplicate_bar_rows"] += 1
            # Match backtest.py: latest recorded row for a TradingView minute.
            if stamp not in bars or record > bars[stamp][0]:
                bars[stamp] = (record, opening, closing)
    return bars, dict(audit)


def load_cycles(calendar, bars):
    grouped = defaultdict(list)
    audit = Counter()
    path = BACKEND / "tv_doc/six_strategy_signal_events.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            code = row.get("strategy_code") or row.get("raw_strategy_code")
            code = "CFCWIN01m" if code == "CFCWN01m" else code
            if code not in STRATEGIES or not row.get("received_at"):
                audit["untimed_or_unknown_strategy_rows"] += 1
                continue
            try:
                stamp = datetime.fromisoformat(row["received_at"])
                new = integer(row["new_position"])
                if new not in {-1, 0, 1}:
                    raise ValueError("invalid position")
            except ValueError:
                audit["invalid_signal_rows"] += 1
                continue
            if not START < stamp < END:
                audit["outside_window_rows"] += 1
                continue
            if not calendar.is_open(stamp) or time(1) <= stamp.time() < time(8, 45):
                audit["closed_session_rows"] += 1
                continue
            day = cycle_day(stamp)
            flat = datetime.combine(day + timedelta(days=1), time(1))
            if next_open(stamp) >= flat:
                audit["signal_fill_at_or_after_flat"] += 1
                continue
            grouped[day].append((stamp, index, code, new))
    included, excluded = {}, {}
    for day, events in sorted(grouped.items()):
        flat = datetime.combine(day + timedelta(days=1), time(1))
        missing = sorted({next_open(e[0]).isoformat() for e in events
                          if next_open(e[0]) not in bars})
        if flat < END and flat not in bars:
            missing.append(flat.isoformat())
        if missing:
            excluded[day.isoformat()] = sorted(set(missing))
        else:
            included[day] = sorted(events)
    audit.update(candidate_cycles=len(grouped), included_cycles=len(included),
                 excluded_cycles=len(excluded),
                 included_signals=sum(map(len, included.values())),
                 excluded_signals=sum(len(grouped[date.fromisoformat(day)])
                                      for day in excluded))
    return included, excluded, dict(audit)


def replay(policy, cycles, bars):
    gross_profit = gross_loss = realized = unrealized = turnover = 0.0
    closed_legs = target_differences = reversal_signals = 0
    ledger = []
    decision_differences = []
    for day, events in cycles.items():
        legs = dict.fromkeys(STRATEGIES, 0)
        long_vetoes, short_vetoes = set(), set()
        position, entry = 0, None
        flat = datetime.combine(day + timedelta(days=1), time(1))
        sequence = [(stamp, code, new, next_open(stamp))
                    for stamp, _, code, new in events]
        if flat < END:
            sequence.append((flat, "flat", 0, flat))
        for stamp, code, new, fill in sequence:
            if code == "flat":
                legs = dict.fromkeys(STRATEGIES, 0)
                long_vetoes.clear()
                short_vetoes.clear()
            else:
                previous = legs[code]
                reversal_signals += previous * new == -1
                legs[code] = new
                if previous == 0 and new == -1:
                    long_vetoes.add(code)
                elif new != -1:
                    long_vetoes.discard(code)
                if previous == 0 and new == 1:
                    short_vetoes.add(code)
                elif new != 1:
                    short_vetoes.discard(code)
            net = sum(legs.values())
            if policy == "active_veto":
                target = clamped_target_direction(legs)
            else:
                target = (0 if net > 0 and long_vetoes or net < 0 and short_vetoes
                          else 1 if net > 0 else -1 if net < 0 else 0)
            if policy == "active_veto" and code != "flat":
                old_target = (0 if net > 0 and long_vetoes or net < 0 and short_vetoes
                              else 1 if net > 0 else -1 if net < 0 else 0)
                if target != old_target:
                    target_differences += 1
                    decision_differences.append({
                        "cycle": day.isoformat(), "signal_time": stamp.isoformat(),
                        "strategy": code, "previous_signal_position": previous,
                        "new_signal_position": new, "net": net,
                              "previous_target": old_target, "active_veto_target": target,
                    })
            if target == position:
                continue
            price = bars[fill][1]
            previous_position = position
            if position:
                pnl = (price - entry) * position * POINT_VALUE
                realized += pnl
                gross_profit += max(pnl, 0)
                gross_loss += max(-pnl, 0)
                closed_legs += 1
            delta = target - position
            turnover += abs(delta)
            ledger.append({"cycle": day.isoformat(), "signal_time": stamp.isoformat(),
                           "fill_time": fill.isoformat(), "source": code,
                           "previous": previous_position, "target": target,
                           "price": price, "delta": delta})
            position, entry = target, price if target else None
        if position:
            assert flat >= END, "complete cycle must end flat"
            mark = bars[max(stamp for stamp in bars if stamp < END)][2]
            unrealized += (mark - entry) * position * POINT_VALUE
    total = realized + unrealized
    net = total - turnover * ONE_WAY_COST_POINTS * POINT_VALUE
    assert abs(gross_profit - gross_loss - realized) < 1e-7
    assert abs(realized + unrealized - total) < 1e-7
    assert turnover == sum(abs(item["delta"]) for item in ledger)
    return {"gross_profit_twd": gross_profit, "gross_loss_twd": gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "closed_legs": closed_legs, "realized_twd": realized,
            "unrealized_twd": unrealized, "total_before_cost_twd": total,
            "one_way_turnover": turnover, "estimated_cost_twd": turnover * ONE_WAY_COST_POINTS * POINT_VALUE,
            "estimated_net_twd": net, "ending_position": position,
            "different_target_signals": target_differences,
            "direct_reversal_signals": reversal_signals,
            "decision_differences": decision_differences, "ledger": ledger}


def main():
    calendar = Calendar.load(BASE / "config/calendar.json")
    bars, bar_audit = load_bars()
    cycles, excluded, signal_audit = load_cycles(calendar, bars)
    assert cycles, "no fully covered trading cycles"
    results = {policy: replay(policy, cycles, bars)
               for policy in ("previous", "active_veto")}
    result = {"period": [START.isoformat(), END.isoformat()],
              "assumptions": {"signal_time": "received_at",
                              "fill": "next-minute MXF1! Open; 01:00 exact Open",
                              "point_value_twd": POINT_VALUE,
                              "one_way_cost_points": ONE_WAY_COST_POINTS,
                              "start_each_cycle_flat": True,
                              "partial_final_cycle_mark": "last eligible MXF1! Close",
                              "gap_policy": "exclude complete cycle for any missing required fill bar"},
              "audit": bar_audit | signal_audit | {"excluded_cycle_details": excluded},
              "results": results}
    output = BASE / "records/compare_active_veto_2026-10-01.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"period": result["period"],
                      "audit": {k: v for k, v in result["audit"].items()
                                if k != "excluded_cycle_details"},
                      "results": {k: {name: value for name, value in data.items()
                                       if name not in {"ledger", "decision_differences"}}
                                  for k, data in results.items()}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
