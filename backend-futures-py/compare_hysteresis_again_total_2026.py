"""June–September 2026 signal replay of the two EF hysteresis shadow rules."""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
START = datetime(2026, 6, 1, 8, 45)
END = datetime(2026, 9, 30, 21, 55, 30)
POINT_VALUE = 10.0
ONE_WAY_COST_POINTS = 2.4


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


source = module("ef_source_for_comparison", ROOT / "ef-strong-consensus-morning-flat-strategy/strategy.py")
again = module("ef_again_for_comparison", ROOT / "ef-hysteresis-again-strategy/strategy.py")
total = module("ef_total_for_comparison", ROOT / "ef-hysteresis-total-breakout-strategy/strategy.py")


def load_bars():
    bars = {}
    audit = Counter()
    with (ROOT / "tv_doc/webhook_data_1min.csv").open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row.get("Symbol") != "MXF1!":
                continue
            try:
                stamp = datetime.fromisoformat(row["TradingView Time"])
                recorded = datetime.fromisoformat(row["Record Time"])
                if not START <= stamp < END or recorded > END:
                    continue
                opening = float(row["Open"].replace(",", ""))
                closing = float(row["Close"].replace(",", ""))
            except (KeyError, ValueError):
                audit["invalid_bar_rows"] += 1
                continue
            audit["eligible_bar_rows"] += 1
            if stamp in bars:
                audit["duplicate_bar_rows"] += 1
            if stamp not in bars or recorded < bars[stamp][0]:
                bars[stamp] = (recorded, opening, closing)
    return bars, dict(audit)


def load_events():
    positions = dict.fromkeys(source.ALL_STRATEGIES, 0)
    events = []
    seen = set()
    audit = Counter()
    with (ROOT / "tv_doc/six_strategy_signal_events.csv").open(encoding="utf-8-sig", newline="") as file:
        for number, row in enumerate(csv.DictReader(file), 1):
            event = source.parse_signal_row(row, number)
            if event is None:
                if not str(row.get("received_at") or "").strip():
                    parsed = source.parse_position_row(row)
                    if parsed:
                        positions[parsed[0]] = parsed[1]
                        audit["untimed_bootstrap_rows"] += 1
                else:
                    audit["invalid_signal_rows"] += 1
                continue
            if event.timestamp >= END:
                continue
            key = (event.timestamp, event.strategy_code, event.previous_position,
                   event.new_position, row.get("account"))
            if key in seen:
                audit["duplicate_signal_rows"] += 1
                continue
            seen.add(key)
            events.append(event)
            if START <= event.timestamp:
                audit["dated_signals_in_window"] += 1
    events.sort(key=lambda e: (e.timestamp, e.row_number))
    return positions, events, dict(audit)


def cycle_date(stamp):
    return stamp.date() if stamp.time() >= time(8, 45) else stamp.date() - timedelta(days=1)


def fill_time(stamp):
    return stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)


def positions_before(bootstrap, events, cutoff):
    positions = bootstrap.copy()
    for event in events:
        if event.timestamp >= cutoff:
            break
        positions[event.strategy_code] = event.new_position
    return positions


def covered_cycles(events, bars):
    grouped = defaultdict(list)
    audit = Counter()
    for event in events:
        if event.timestamp < START or event.timestamp >= END:
            continue
        if time(1) <= event.timestamp.time() < time(8, 45):
            audit["morning_state_only_signals"] += 1
            continue
        day = cycle_date(event.timestamp)
        if day < START.date():
            continue
        boundary = datetime.combine(day + timedelta(days=1), time(1))
        if fill_time(event.timestamp) >= boundary:
            audit["after_flat_fill_signals"] += 1
            continue
        grouped[day].append(event)
    included = {}
    excluded = {}
    for day, rows in sorted(grouped.items()):
        boundary = datetime.combine(day + timedelta(days=1), time(1))
        missing = sorted({fill_time(e.timestamp).isoformat() for e in rows
                          if fill_time(e.timestamp) not in bars})
        if boundary < END and boundary not in bars:
            missing.append(boundary.isoformat())
        if missing:
            excluded[day.isoformat()] = sorted(set(missing))
        else:
            included[day] = rows
    audit.update({"candidate_cycles": len(grouped), "included_cycles": len(included),
                  "included_actionable_signals": sum(map(len, included.values())),
                  "excluded_actionable_signals": sum(len(grouped[date.fromisoformat(k)]) for k in excluded)})
    return included, excluded, dict(audit)


def replay_one(mode, bootstrap, events, cycles, bars, *, post_decision=None):
    months = defaultdict(lambda: {"gross_profit": 0.0, "gross_loss": 0.0,
                                  "closed_legs": 0, "realized": 0.0, "unrealized": 0.0,
                                  "turnover": 0, "cycles": 0, "ending_position": 0})
    ledger = []
    for day, rows in cycles.items():
        reopen = datetime.combine(day, time(8, 45))
        baseline_cutoff = datetime.combine(day, time(5))
        positions = positions_before(bootstrap, events, reopen)
        base = (total.capture_baseline(positions_before(bootstrap, events, baseline_cutoff),
                                       source.PORTFOLIO_E, source.PORTFOLIO_F)
                if mode in {"total", "total_new_short_exit"} else None)
        long_locked = again.should_lock_long(positions, source.PORTFOLIO_E, source.PORTFOLIO_F)
        short_locked = again.should_lock_short(positions, source.PORTFOLIO_E, source.PORTFOLIO_F)
        held = 0
        entry = None
        month = day.strftime("%Y-%m")
        metrics = months[month]
        metrics["cycles"] += 1
        for event in rows:
            old_leg = positions[event.strategy_code]
            positions[event.strategy_code] = event.new_position
            if mode == "again":
                decision = again.decide(positions, source.PORTFOLIO_E, source.PORTFOLIO_F,
                                        held, long_locked, short_locked)
                decision = again.exit_on_new_short(decision, held, old_leg, event.new_position)
                if post_decision is not None:
                    decision = post_decision(decision, positions,
                                             source.PORTFOLIO_E, source.PORTFOLIO_F)
                long_locked, short_locked = decision.long_locked, decision.short_locked
            else:
                decision = total.decide(positions, source.PORTFOLIO_E, source.PORTFOLIO_F,
                                        held, base)
                if mode == "total_new_short_exit" and held > 0 and decision.target > 0:
                    if old_leg == 0 and event.new_position == -1:
                        decision = total.Decision(0, decision.e_net, decision.f_net,
                                                  decision.total, "追蹤到0→-1反向訊號，多單出場")
            target = decision.target
            if target == held:
                continue
            stamp = fill_time(event.timestamp)
            price = bars[stamp][1]
            if held:
                pnl = (price - entry) * held * POINT_VALUE
                metrics["realized"] += pnl
                metrics["closed_legs"] += 1
                if pnl >= 0:
                    metrics["gross_profit"] += pnl
                else:
                    metrics["gross_loss"] -= pnl
            previous = held
            metrics["turnover"] += abs(target - held)
            held = target
            entry = price if held else None
            ledger.append({"cycle": day.isoformat(), "signal": event.timestamp.isoformat(),
                           "fill": stamp.isoformat(), "strategy_code": event.strategy_code,
                           "reported_previous": event.previous_position, "tracked_previous": old_leg,
                           "new_leg": event.new_position, "before": previous, "target": target,
                           "price": price, "reason": decision.reason})
        boundary = datetime.combine(day + timedelta(days=1), time(1))
        if boundary < END and held:
            price = bars[boundary][1]
            pnl = (price - entry) * held * POINT_VALUE
            metrics["realized"] += pnl
            metrics["closed_legs"] += 1
            if pnl >= 0:
                metrics["gross_profit"] += pnl
            else:
                metrics["gross_loss"] -= pnl
            metrics["turnover"] += abs(held)
            ledger.append({"cycle": day.isoformat(), "signal": boundary.isoformat(),
                           "fill": boundary.isoformat(), "strategy_code": "clock_flat",
                           "before": held, "target": 0, "price": price, "reason": "01:00 shadow flat"})
            held, entry = 0, None
        if held:
            mark_time = max(bars)
            if mark_time < fill_time(rows[-1].timestamp):
                raise ValueError("final mark precedes last action")
            metrics["unrealized"] += (bars[mark_time][2] - entry) * held * POINT_VALUE
            metrics["ending_position"] = held
    for metrics in months.values():
        metrics["total"] = metrics["realized"] + metrics["unrealized"]
        metrics["estimated_cost"] = metrics["turnover"] * ONE_WAY_COST_POINTS * POINT_VALUE
        metrics["estimated_net"] = metrics["total"] - metrics["estimated_cost"]
        metrics["profit_factor"] = (metrics["gross_profit"] / metrics["gross_loss"]
                                    if metrics["gross_loss"] else None)
        assert abs(metrics["gross_profit"] - metrics["gross_loss"] - metrics["realized"]) < 1e-7
    keys = ("gross_profit", "gross_loss", "closed_legs", "realized", "unrealized",
            "turnover", "cycles", "total", "estimated_cost", "estimated_net")
    overall = {key: sum(month[key] for month in months.values()) for key in keys}
    overall["profit_factor"] = (overall["gross_profit"] / overall["gross_loss"]
                                if overall["gross_loss"] else None)
    overall["ending_position"] = months["2026-09"]["ending_position"]
    assert sum(abs(t["target"] - t["before"]) for t in ledger) == overall["turnover"]
    return {"overall": overall, "months": dict(sorted(months.items())), "ledger": ledger}


def main():
    bars, bar_audit = load_bars()
    bootstrap, events, signal_audit = load_events()
    cycles, excluded, cycle_audit = covered_cycles(events, bars)
    result = {"period": [START.isoformat(), END.isoformat()],
              "assumptions": {"point_value_twd": POINT_VALUE,
                              "one_way_cost_points": ONE_WAY_COST_POINTS,
                              "price_proxy": "MXF1! next-minute Open; 01:00 exact Open",
                              "signal_time": "received_at",
                              "untimed_rows": "initial E/F bootstrap only; never actionable",
                              "cycle_start_target": 0,
                              "final_mark_time": max(bars).isoformat(),
                              "total_breakout_new_short_exit": (
                                  "same TOTAL breakout rule, plus immediate long exit when "
                                  "a tracked source position changes 0 to -1 and the "
                                  "ordinary TOTAL decision would otherwise keep the long")},
              "audit": bar_audit | signal_audit | cycle_audit | {"excluded_cycles": excluded},
              "again": replay_one("again", bootstrap, events, cycles, bars),
              "total_breakout": replay_one("total", bootstrap, events, cycles, bars),
              "total_breakout_new_short_exit": replay_one(
                  "total_new_short_exit", bootstrap, events, cycles, bars)}
    output = ROOT / "hysteresis_again_total_2026_jun_sep.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"period": result["period"], "audit": result["audit"],
                      "again": {k: v for k, v in result["again"].items() if k != "ledger"},
                      "total_breakout": {k: v for k, v in result["total_breakout"].items() if k != "ledger"},
                      "total_breakout_new_short_exit": {
                          k: v for k, v in result["total_breakout_new_short_exit"].items()
                          if k != "ledger"}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
