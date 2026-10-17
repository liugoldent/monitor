"""Offline EF reverse-signal research; never imports broker or monitor modules.

Threshold variants are research rules, not a claim of historical live fills.
Reverse the original rule's target book, retaining its entry/exit clock.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path

import backtest_five_strategies_next_open as base
import compare_ef_live_candidates as common


def load(root, end):
    audit, snapshots = {}, {}
    snapshots_data = {}
    for label, relative in (
        ("signals", "backend-futures-py/tv_doc/six_strategy_signal_events.csv"),
        ("prices", "backend-futures-py/tv_doc/webhook_data_1min.csv"),
    ):
        data = (root / relative).read_bytes()
        snapshots[label] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        snapshots_data[label] = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    bars = {}
    revisions = duplicates = invalid = 0
    for row in snapshots_data["prices"]:
        if row.get("Symbol") != "MXF1!":
            continue
        try:
            stamp = datetime.fromisoformat(row["TradingView Time"])
            record = datetime.fromisoformat(row["Record Time"])
            op, close = (float(row[k].replace(",", "")) for k in ("Open", "Close"))
        except (KeyError, ValueError, TypeError):
            invalid += 1
            continue
        # Require the entire marked minute to have finished by the frozen end.
        if stamp + timedelta(minutes=1) > end or record > end:
            continue
        if stamp in bars:
            duplicates += 1
            revisions += bars[stamp][1:] != (op, close)
        # Retain the earliest recorded version, not a later revision.
        if stamp not in bars or record < bars[stamp][0]:
            bars[stamp] = (record, op, close)
    events, seen, first, mismatches, state = [], set(), {}, [], {}
    counts = Counter()
    for number, row in enumerate(snapshots_data["signals"], 1):
        code = row.get("strategy_code") or row.get("raw_strategy_code")
        code = "CFCWIN01m" if code == "CFCWN01m" else code
        if code not in base.ALL_STRATEGIES:
            counts["unrecognized"] += 1
            continue
        if not row.get("received_at"):
            counts["untimed"] += 1
            continue
        try:
            stamp = datetime.fromisoformat(row["received_at"])
            old, new = (int(float(row[k])) for k in ("previous_position", "new_position"))
            if old not in (-1, 0, 1) or new not in (-1, 0, 1):
                raise ValueError("non-unit source position")
        except (ValueError, KeyError, TypeError):
            counts["invalid"] += 1
            continue
        if stamp > end:
            counts["after_end"] += 1
            continue
        key = (stamp, code, old, new)  # one logical strategy book across accounts
        if key in seen:
            counts["duplicates"] += 1
            continue
        seen.add(key)
        events.append((stamp, number, code, old, new))
    events.sort()
    for stamp, _, code, old, new in events:
        first.setdefault(code, str(stamp))
        if code in state and state[code] != old:
            mismatches.append({"time": str(stamp), "code": code, "tracked": state[code], "reported_previous": old})
        state[code] = new
    calendar = json.loads((root / "backend-futures-py/ef-morning-weekend-hedge-strategy/config/calendar.json").read_text(encoding="utf-8"))
    audit.update(signal_counts=dict(counts), usable_events=len(events), price_bars=len(bars),
                 duplicate_bars=duplicates, revised_bars=revisions, invalid_prices=invalid,
                 first_receipt_by_code=first, continuity_mismatches=mismatches,
                 source_snapshots=snapshots)
    return bars, events, calendar, audit


def timeline(bars, events, calendar, start, end):
    times = sorted(bars)
    raw = dict.fromkeys(base.ALL_STRATEGIES, 0)
    actions = []
    for stamp, number, code, old, new in events:
        before = raw.copy()
        raw[code] = new
        if not start <= stamp < end:
            continue
        desired = stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        index = bisect.bisect_left(times, desired)
        actual = times[index] if index < len(times) else None
        delay = None if actual is None else (actual - desired).total_seconds()
        eligible = (actual is not None and actual < end and delay <= 120
                    and common.calendar_open(stamp, calendar)
                    and common.calendar_open(actual, calendar)
                    and not time(1) <= actual.time() < time(8, 45))
        # Decisions use receipt-time raw state only. Stale events update state
        # but are never queued to execute hours/days after a data gap.
        actions.append((actual if eligible else stamp, 1, number, {
            "receipt": stamp, "e": sum(raw[c] for c in base.PORTFOLIO_E),
            "f": sum(raw[c] for c in base.PORTFOLIO_F), "raw": raw.copy(),
            "before_e": sum(before[c] for c in base.PORTFOLIO_E),
            "before_f": sum(before[c] for c in base.PORTFOLIO_F),
            "eligible": eligible, "delay_seconds": delay,
            "price": bars[actual][1] if eligible else None, "code": code,
        }))
    missing_flat = 0
    day = start.date()
    while day <= end.date():
        desired = datetime.combine(day, time(1))
        if start <= desired < end and common.calendar_open(desired, calendar):
            idx = bisect.bisect_left(times, desired)
            if idx < len(times) and (times[idx] - desired).total_seconds() <= 120:
                actions.append((times[idx], 0, -1, {"price": bars[times[idx]][1]}))
            else:
                missing_flat += 1
        day += timedelta(days=1)
    for stamp in times:
        if start <= stamp < end:
            mark_time = stamp + timedelta(minutes=1)
            actions.append((mark_time, 2, -1, {"price": bars[stamp][2], "month": mark_time.strftime("%Y-%m")}))
    # At time T, the preceding bar's close is marked before a T-open fill.
    actions.sort(key=lambda a: (a[0], -1 if a[1] == 2 else a[1], a[2]))
    return actions, missing_flat


def replay(spec, actions, start, end, cost):
    e_threshold, f_threshold, hold, side, style, code = spec
    pos = 0  # original rule book; reverse execution is always -pos
    entry = entry_time = None
    realized = turnover = 0
    peak = drawdown = 0.0
    long_lock = short_lock = False
    cycle = None
    pnls, trades, months = [], [], {}
    delayed = 0
    equity = 0.0
    for stamp, kind, _, action in actions:
        price = action.get("price")
        target = pos
        if kind == 2:
            equity = -realized - ((price - entry) * pos if pos else 0) - turnover * cost
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            months[action["month"] if "month" in action else stamp.strftime("%Y-%m")] = equity
            continue
        if kind == 0:
            target = 0
            long_lock = short_lock = False
            cycle = None
        elif not action["eligible"]:
            continue
        elif code and action["code"] != code:
            continue
        elif code:
            target = action["raw"][code]
        else:
            receipt = action["receipt"]
            e, f = action["e"], action["f"]
            new_cycle = receipt.date() if receipt.time() >= time(1) else receipt.date() - timedelta(days=1)
            if style == "again_veto" and cycle != new_cycle:
                long_lock = action["before_e"] >= e_threshold and action["before_f"] >= f_threshold
                short_lock = action["before_e"] <= -e_threshold and action["before_f"] <= -f_threshold
                cycle = new_cycle
            bull, bear = e >= e_threshold and f >= f_threshold, e <= -e_threshold and f <= -f_threshold
            long_lock = long_lock and bull
            short_lock = short_lock and bear
            he, hf = (e_threshold, f_threshold) if hold == "entry" else (1, 1)
            if bull:
                target = 0 if long_lock and pos <= 0 else 1
            elif bear:
                target = 0 if short_lock and pos >= 0 else -1
            elif pos > 0 and e >= he and f >= hf:
                target = 1
            elif pos < 0 and e <= -he and f <= -hf:
                target = -1
            else:
                target = 0
            if style == "again_veto":
                values = action["raw"].values()
                if (target > 0 and any(x < 0 for x in values)) or (target < 0 and any(x > 0 for x in values)):
                    target = 0
        if (side == "long" and target < 0) or (side == "short" and target > 0):
            target = 0
        if target == pos:
            continue
        # Mark at actual proxy fill, including fees already paid, then trade.
        equity = -realized - ((price - entry) * pos if pos else 0) - turnover * cost
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        if pos:
            pnl = (price - entry) * pos
            pnls.append(pnl)
            trades.append({"entry": str(entry_time), "exit": str(stamp), "original_side": pos,
                           "entry_price": entry, "exit_price": price,
                           "original_gross_points": pnl, "reverse_net_points": -pnl - 2 * cost})
            realized += pnl
        turnover += abs(target - pos)
        if kind == 1 and action["delay_seconds"]:
            delayed += 1
        pos, entry, entry_time = target, price if target else None, stamp if target else None
        equity = -realized - turnover * cost
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    n = len(pnls)
    reverse_net = [-p - 2 * cost for p in pnls]
    rgp, rgl = sum(-p for p in pnls if p < 0), sum(p for p in pnls if p > 0)
    ngp, ngl = sum(p for p in reverse_net if p > 0), -sum(p for p in reverse_net if p < 0)
    unrealized = equity + turnover * cost + realized
    assert abs(rgp - rgl + realized) < 1e-8
    assert turnover == 2 * n + abs(pos)
    assert abs(sum(reverse_net) - (-realized - 2 * n * cost)) < 1e-8
    assert abs(equity - (-realized + unrealized - turnover * cost)) < 1e-8
    prior, monthly = 0, {}
    for month, value in months.items():
        monthly[month] = value - prior
        prior = value
    result = {
        "closed_trades": n, "original_gross_failure_rate": sum(p < 0 for p in pnls) / n if n else None,
        "original_net_failure_rate": sum(p - 2 * cost < 0 for p in pnls) / n if n else None,
        "reverse_net_expectancy_points": sum(reverse_net) / n if n else None,
        "reverse_net_win_rate": sum(p > 0 for p in reverse_net) / n if n else None,
        "reverse_gross_profit_points": rgp, "reverse_gross_loss_points": rgl,
        "reverse_gross_pf": rgp / rgl if rgl else None,
        "reverse_net_closed_pf": ngp / ngl if ngl else None,
        "reverse_realized_gross_points": -realized, "reverse_unrealized_points": unrealized,
        "reverse_gross_total_points": -realized + unrealized,
        "reverse_net_total_points": equity, "reverse_net_realized_points": sum(reverse_net),
        "reverse_net_max_drawdown_points": drawdown, "one_way_turnover": turnover,
        "reverse_ending_position": -pos, "delayed_transitions": delayed,
        "monthly_reverse_net_points": monthly,
        "reverse_expectancy_cost_sensitivity": {str(c): (-realized / n - 2*c if n else None) for c in (0, 2.4, 5, 10)},
    }
    return result, trades


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-07-14 00:00:00")
    p.add_argument("--split", default="2026-09-01 00:00:00")
    p.add_argument("--end", required=True)
    p.add_argument("--cost", type=float, default=2.4)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    start, split, end = map(datetime.fromisoformat, (args.start, args.split, args.end))
    if not start < split < end or args.cost < 0:
        p.error("require start < split < end and cost >= 0")
    root = Path(__file__).resolve().parents[1]
    bars, events, calendar, audit = load(root, end)
    windows = {"full": (start, end), "train": (start, split), "validation": (split, end),
               "all_codes_observed": (max(datetime.fromisoformat(v) for v in audit["first_receipt_by_code"].values()) + timedelta(minutes=1), end)}
    timelines = {}
    for label, (a, b) in windows.items():
        timelines[label], missing = timeline(bars, events, calendar, a, b)
        audit[label] = {"missing_0100_price": missing,
                        "ineligible_events": sum(k == 1 and not v["eligible"] for _, k, _, v in timelines[label]),
                        "stale_or_missing_next_open": sum(k == 1 and (v["delay_seconds"] is None or v["delay_seconds"] > 120) for _, k, _, v in timelines[label])}
    specs = []
    for e in range(1, 8):
        for f in range(1, 7):
            for hold in (("one",) if (e, f) == (1, 1) else ("one", "entry")):
                for side in ("both", "long", "short"):
                    for style in ("plain", "again_veto"):
                        specs.append((e, f, hold, side, style, ""))
    specs.extend((0, 0, "one", "both", "individual", c) for c in base.ALL_STRATEGIES)
    results, trade_sets = [], {}
    for spec in specs:
        e, f, hold, side, style, code = spec
        name = code or f"E{e}_F{f}_hold-{hold}_{side}_{style}"
        row = {"name": name, "e_entry": e, "f_entry": f, "hold": hold,
               "original_side": side, "style": style}
        for label, (a, b) in windows.items():
            row[label], trades = replay(spec, timelines[label], a, b, args.cost)
            if label == "full":
                trade_sets[name] = trades
        results.append(row)
    def qualifies(row, window, minimum):
        r = row[window]
        return (r["closed_trades"] >= minimum and r["original_gross_failure_rate"] > .5
                and r["reverse_net_expectancy_points"] > 0)
    full = sorted([r for r in results if qualifies(r, "full", 30)],
                  key=lambda r: r["full"]["reverse_net_expectancy_points"], reverse=True)
    training = sorted([r for r in results if qualifies(r, "train", 20)],
                      key=lambda r: r["train"]["reverse_net_expectancy_points"], reverse=True)
    top_train = training[0] if training else None
    report = {"contract": {"windows": {k:[str(a),str(b)] for k,(a,b) in windows.items()},
        "cost_points_one_way":args.cost,"point_value_twd":10,
        "fill":"receipt -> first next-minute open, max 120s extra delay; no stale catch-up",
        "session":"01:00 flat, no new entry 01:00-08:45 or outside calendar session",
        "boundary":"flat book at each window start; raw source state rebuilt from prior receipts; open ending legs marked only",
        "unknown_source":"zero until first received event; no future backfill; strict later-window sensitivity included",
        "reverse":"negative of original rule target; same entry/exit clock; original rule state kept separate",
        "variants":"E 1..7, F 1..6; hold 1/1 or entry thresholds; original long/short/both; plain or Again lock+opposition veto; plus 13 individual books",
        "selection":"train only: original gross failure >50%, >=20 closed legs, reverse expectancy >0; full exploratory >=30 legs",
        "validation_caveat":"chronological diagnostic, not pristine holdout: existing rules and hypothesis may have been informed by this history"},
        "audit":audit,"variants_tested":len(results),"full_candidates":len(full),
        "training_candidates":len(training),"train_selected":top_train,
        "top_full_exploratory":full[:15],"top_train":training[:10],"results":results}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    flat = []
    for row in sorted(results, key=lambda r: (qualifies(r, "full", 30),
                                             r["full"]["closed_trades"] >= 30,
                                             r["full"]["reverse_net_expectancy_points"]
                                             if r["full"]["closed_trades"] else float("-inf")), reverse=True):
        entry = {k:v for k,v in row.items() if k not in windows}
        for label in windows:
            entry.update({f"{label}_{k}":v for k,v in row[label].items() if not isinstance(v,dict)})
        flat.append(entry)
    with (args.output/"ranking.csv").open("w",encoding="utf-8-sig",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(flat[0]));writer.writeheader();writer.writerows(flat)
    with (args.output/"candidates.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        metrics = ("closed_trades", "original_gross_failure_rate", "reverse_net_expectancy_points",
                   "reverse_net_realized_points", "reverse_net_max_drawdown_points", "reverse_net_closed_pf")
        fields = ["name", *metrics, "train_closed_trades", "train_expectancy", "later_closed_trades", "later_expectancy"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in full:
            writer.writerow({"name": row["name"], **{k: row["full"][k] for k in metrics},
                             "train_closed_trades": row["train"]["closed_trades"],
                             "train_expectancy": row["train"]["reverse_net_expectancy_points"],
                             "later_closed_trades": row["validation"]["closed_trades"],
                             "later_expectancy": row["validation"]["reverse_net_expectancy_points"]})
    chosen = set(r["name"] for r in full[:5]+training[:3])
    chosen.add("E2_F2_hold-one_both_again_veto")
    (args.output/"selected_trades.json").write_text(json.dumps({k:trade_sets[k] for k in chosen},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("results","audit")},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
