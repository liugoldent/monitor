"""Research-only comparison of current Again and Clamp-style raw 1->0 exit.

Reuses the project replay and production Again decisions; never imports a
broker, changes runtime state, or submits orders.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "again_long_exit_replay", ROOT / "compare_hysteresis_again_total_2026.py")
base = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = base
SPEC.loader.exec_module(base)


def raw_long_exit(decision, current, event):
    # Exactly the Clamp semantics: suppress this event's positive target,
    # including when flat. Leave other legs, locks, and shorts unchanged.
    if decision.target > 0 and event.previous_position == 1 and event.new_position == 0:
        return replace(decision, target=0, reason="原始1→0訊號，本筆多單目標歸零")
    return decision


def enrich(result, bars):
    sides = {side: {"gross_profit": 0.0, "gross_loss": 0.0, "realized": 0.0,
                    "unrealized": 0.0, "closed_legs": 0, "winning_legs": 0,
                    "turnover": 0} for side in ("long", "short")}
    held, entry = 0, None
    net_cash, peak, max_drawdown = 0.0, 0.0, 0.0
    trades = result["ledger"]
    trade_index = 0
    marked = set()
    pnl_values = []
    reentries = 0
    forced_exit_time = None
    for stamp, bar in sorted(bars.items()):
        while trade_index < len(trades) and datetime.fromisoformat(trades[trade_index]["fill"]) <= stamp:
            trade = trades[trade_index]
            assert held == trade["before"]
            target, price = trade["target"], trade["price"]
            if held:
                side = sides["long" if held > 0 else "short"]
                pnl = (price - entry) * held * base.POINT_VALUE
                side["realized"] += pnl
                side["closed_legs"] += 1
                side["winning_legs"] += int(pnl > 0)
                side["gross_profit"] += max(pnl, 0)
                side["gross_loss"] += max(-pnl, 0)
                side["turnover"] += abs(held)
                pnl_values.append(pnl)
            if target:
                sides["long" if target > 0 else "short"]["turnover"] += abs(target)
                if target > 0 and forced_exit_time is not None:
                    if datetime.fromisoformat(trade["fill"]) - forced_exit_time <= timedelta(minutes=15):
                        reentries += 1
                forced_exit_time = None
            if trade["reason"] == "原始1→0訊號，本筆多單目標歸零" and held > 0:
                forced_exit_time = datetime.fromisoformat(trade["fill"])
            net_cash -= ((target - held) * price * base.POINT_VALUE
                         + abs(target - held) * base.ONE_WAY_COST_POINTS * base.POINT_VALUE)
            held, entry = target, price if target else None
            # Mark at the fill open before this minute's close.
            equity = net_cash + held * price * base.POINT_VALUE
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            trade_index += 1
        equity = net_cash + held * bar[2] * base.POINT_VALUE
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if held:
            marked.add(stamp)
    assert trade_index == len(trades)
    if held:
        sides["long" if held > 0 else "short"]["unrealized"] = (
            bars[max(bars)][2] - entry) * held * base.POINT_VALUE
    for side in sides.values():
        side["total"] = side["realized"] + side["unrealized"]
        side["estimated_net"] = (side["total"] - side["turnover"]
                                 * base.ONE_WAY_COST_POINTS * base.POINT_VALUE)
        side["profit_factor"] = side["gross_profit"] / side["gross_loss"] if side["gross_loss"] else None
        side["win_rate"] = side["winning_legs"] / side["closed_legs"] if side["closed_legs"] else None
        assert abs(side["gross_profit"] - side["gross_loss"] - side["realized"]) < 1e-7
    overall = result["overall"]
    assert held == overall["ending_position"]
    for key in ("gross_profit", "gross_loss", "realized", "unrealized", "total", "turnover", "closed_legs", "estimated_net"):
        assert abs(sum(side[key] for side in sides.values()) - overall[key]) < 1e-7, key
    assert abs(equity - overall["estimated_net"]) < 1e-7
    overall["max_drawdown_net_twd_minute_close"] = max_drawdown
    overall["win_rate"] = sum(p > 0 for p in pnl_values) / len(pnl_values) if pnl_values else None
    result["sides"] = sides
    result["forced_exit_reentries_within_15_minutes"] = reentries
    result["missing_minute_marks_while_held"] = sum(
        stamp + timedelta(minutes=1) not in bars for stamp in marked if stamp < max(bars))
    return result


def validate_hook():
    event = base.source.SignalEvent(1, datetime(2026, 10, 8, 9), "CFC07m", 1, 0)
    decision = base.again.Decision(1, 2, 2, False, False, "hold")
    assert raw_long_exit(decision, 1, event).target == 0
    assert raw_long_exit(decision, 0, event).target == 0
    short = replace(decision, target=-1)
    assert raw_long_exit(short, -1, event) == short
    unrelated = replace(event, previous_position=-1)
    assert raw_long_exit(decision, 1, unrelated) == decision
    # Raw previous=1 is decisive even if the locally tracked leg was already 0.
    positions = dict.fromkeys(base.source.ALL_STRATEGIES, 0)
    positions.update({c: 1 for c in base.source.PORTFOLIO_E[:2] + base.source.PORTFOLIO_F[:2]})
    event = replace(event, strategy_code=base.source.PORTFOLIO_E[-1])
    assert positions[event.strategy_code] == 0
    ordinary = base.again.decide(positions, base.source.PORTFOLIO_E, base.source.PORTFOLIO_F, 1, False)
    assert ordinary.target == 1 and raw_long_exit(ordinary, 1, event).target == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-06-01T08:45:00")
    parser.add_argument("--end", default="2026-10-09T05:00:30")
    parser.add_argument("--cost", type=float, default=2.4)
    parser.add_argument("--output", type=Path, default=ROOT / "again_long_exit_2026_jun_oct08.json")
    args = parser.parse_args()
    base.START, base.END = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    base.ONE_WAY_COST_POINTS = args.cost
    if base.START >= base.END or args.cost < 0:
        raise ValueError("invalid period or cost")
    validate_hook()
    bars, bar_audit = base.load_bars()
    bootstrap, events, signal_audit = base.load_events()
    cycles, excluded, cycle_audit = base.covered_cycles(events, bars)
    if not cycles or not bars:
        raise ValueError("no covered cycles or bars")
    results = {}
    for mode in ("current", "raw_1_to_0_long_exit"):
        counts = Counter()

        def event_hook(decision, held, event):
            updated = raw_long_exit(decision, held, event) if mode != "current" else decision
            if updated != decision:
                counts["forced_long_exits" if held > 0 else "suppressed_long_entries"] += 1
            return updated

        result = base.replay_one("again", bootstrap, events, cycles, bars,
                                 post_decision=base.again.veto_active_opposition,
                                 post_event_decision=event_hook)
        result["extra_rule_triggers"] = dict(counts)
        results[mode] = enrich(result, bars)
    # Verify the new optional event callback leaves the existing baseline intact.
    baseline = base.replay_one("again", bootstrap, events, cycles, bars,
                               post_decision=base.again.veto_active_opposition)
    assert baseline["ledger"] == results["current"]["ledger"]
    assert results["current"]["sides"]["short"] == results["raw_1_to_0_long_exit"]["sides"]["short"]
    audit = bar_audit | signal_audit | cycle_audit
    audit["excluded_cycles"] = excluded
    audit["included_first_cycle"] = min(cycles).isoformat()
    audit["included_last_cycle"] = max(cycles).isoformat()
    report = {"period_taipei": [base.START.isoformat(), base.END.isoformat()],
              "assumptions": {
                  "signal_time": "received_at; untimed rows initialize source state only",
                  "fill": "MXF1! exact next-minute Open; 01:00 exact Open; proxy, not actual TMF fills",
                  "position_unit": 1, "point_value_twd": base.POINT_VALUE,
                  "one_way_cost_points": args.cost,
                  "boundary": "flat at each 08:45 cycle; carry source states; end mark at last eligible Close",
                  "final_mark": max(bars).isoformat(),
                  "missing_fills": "exclude identical whole cycles from both variants",
                  "drawdown": "net equity marked at fills and available minute closes; missing bars may understate drawdown",
                  "current": "production 2/1 hysteresis, daily re-cross locks, 0->-1 long exit, symmetric active opposition veto",
                  "variant": "same current rule plus raw source previous=1,new=0 suppresses this event's positive target; no new cooldown or lock",
              }, "audit": audit, "results": results}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**{k: v for k, v in report.items() if k not in {"results", "audit"}},
                      "audit": {k: v for k, v in audit.items() if k != "excluded_cycles"},
                      "results": {mode: {k: v for k, v in result.items() if k != "ledger"}
                                  for mode, result in results.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
