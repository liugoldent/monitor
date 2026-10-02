"""Replay current Again against a symmetric active-opposition veto.

This is research only: it does not alter the live strategy or submit orders.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "again_replay_base", ROOT / "compare_hysteresis_again_total_2026.py")
base = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = base
SPEC.loader.exec_module(base)

END = datetime(2026, 10, 1, 20, 59, 30)
base.END = END
OUTPUT = ROOT / "again_active_veto_2026_jun_oct01.json"


def replay(mode, bootstrap, events, cycles, bars):
    hook = None if mode == "current" else base.again.veto_active_opposition
    return base.replay_one("again", bootstrap, events, cycles, bars,
                           post_decision=hook)


def by_side(ledger, final_bar):
    metrics = {name: {"gross_profit": 0.0, "gross_loss": 0.0,
                      "closed_legs": 0, "turnover": 0, "realized": 0.0,
                      "unrealized": 0.0, "total": 0.0, "estimated_net": 0.0}
               for name in ("long", "short")}
    held = 0
    entry = None
    cycle = None
    for trade in ledger:
        if cycle != trade["cycle"]:
            assert held == 0, "new cycle began with an unclosed position"
            cycle = trade["cycle"]
        assert held == trade["before"]
        target = trade["target"]
        price = trade["price"]
        if held:
            side = metrics["long" if held > 0 else "short"]
            pnl = (price - entry) * held * base.POINT_VALUE
            side["realized"] += pnl
            side["closed_legs"] += 1
            if pnl >= 0:
                side["gross_profit"] += pnl
            else:
                side["gross_loss"] -= pnl
            side["turnover"] += abs(held)
        if target:
            metrics["long" if target > 0 else "short"]["turnover"] += abs(target)
        held, entry = target, price if target else None
    if held:
        side = metrics["long" if held > 0 else "short"]
        side["unrealized"] = (final_bar[2] - entry) * held * base.POINT_VALUE
    for side in metrics.values():
        side["total"] = side["realized"] + side["unrealized"]
        side["estimated_net"] = (side["total"] - side["turnover"]
                                 * base.ONE_WAY_COST_POINTS * base.POINT_VALUE)
        side["profit_factor"] = (side["gross_profit"] / side["gross_loss"]
                                 if side["gross_loss"] else None)
        assert abs(side["gross_profit"] - side["gross_loss"] - side["realized"]) < 1e-7
    return metrics


def main():
    bars, bar_audit = base.load_bars()
    bootstrap, events, signal_audit = base.load_events()
    cycles, excluded, cycle_audit = base.covered_cycles(events, bars)
    results = {}
    for mode in ("current", "active_veto"):
        result = replay(mode, bootstrap, events, cycles, bars)
        # The older June-September replay hardcodes September as its final month.
        result["overall"]["ending_position"] = result["months"][max(result["months"])]["ending_position"]
        result["sides"] = by_side(result["ledger"], bars[max(bars)])
        assert sum(v["total"] for v in result["sides"].values()) == result["overall"]["total"]
        assert sum(v["turnover"] for v in result["sides"].values()) == result["overall"]["turnover"]
        results[mode] = result
    report = {
        "period": [base.START.isoformat(), END.isoformat()],
        "assumptions": {
            "signal_time": "received_at",
            "fill": "first MXF1! minute open after the signal minute; 01:00 exact open",
            "point_value_twd": base.POINT_VALUE,
            "one_way_cost_points": base.ONE_WAY_COST_POINTS,
            "cycle_start_target": 0,
            "final_mark_time": max(bars).isoformat(),
            "current": "production Again 2/1 hysteresis, locks, long exit on 0 to -1",
            "active_veto": "same rule, but any tracked -1 forbids long and any tracked +1 forbids short; existing same-side positions exit",
            "excluded_cycles": "exclude a whole cycle if any actionable next-minute fill or 01:00 close lacks a bar",
        },
        "audit": bar_audit | signal_audit | cycle_audit | {"excluded_cycles": excluded},
        "results": results,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"period": report["period"], "audit": {
        k: v for k, v in report["audit"].items() if k != "excluded_cycles"},
        "results": {k: {"overall": v["overall"], "sides": v["sides"],
                        "months": v["months"]} for k, v in results.items()}},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
