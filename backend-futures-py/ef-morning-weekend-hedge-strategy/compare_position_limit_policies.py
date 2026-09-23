"""Compare three account policies for a pure-EF net-position limit.

Policies:
  hold_last: live behavior; keep the last accepted account target while abs(net) > 1.
  clamp: always map the source net to -1/0/+1.
  flat_wait: hold zero while abs(net) > 1, then re-enter when it returns in range.
  path_aware: split a -1<->+1 reversal at zero and apply hold_last to each leg.

Signals use their local receipt timestamp and fill at the exact next-minute MXF1!
open.  The 01:00 daily flatten fills at the exact 01:00 open.  This is an
offline execution proxy and never touches the broker or live state.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, time, timedelta
from pathlib import Path

from strategy import Calendar, STRATEGIES


BASE = Path(__file__).resolve().parent
POINT_VALUE = 10


def trading_cycle(stamp: datetime):
    return (stamp - timedelta(days=1)).date() if stamp.time() < time(8, 45) else stamp.date()


def policy_target(mode: str, source_net: int, previous_target: int) -> int:
    if mode == "hold_last":
        return previous_target if abs(source_net) > 1 else source_net
    if mode == "clamp":
        return 1 if source_net > 0 else -1 if source_net < 0 else 0
    if mode == "flat_wait":
        return 0 if abs(source_net) > 1 else source_net
    raise ValueError(f"unknown mode: {mode}")


def replay(events, bars, mode: str, start: datetime, end: datetime, cost_points: float):
    legs = dict.fromkeys(STRATEGIES, 0)
    position = 0
    entry_price = None
    realized_legs = []
    cash = 0.0
    turnover = 0
    ledger = []

    def execute(stamp, code, source_net, target, fill):
        nonlocal position, entry_price, cash, turnover
        if target == position:
            return
        price = bars[fill]["open"]
        previous = position
        delta = target - previous

        if previous and target != previous:
            realized_legs.append((price - entry_price) * previous * POINT_VALUE)
            entry_price = None
        if target and target != previous:
            entry_price = price

        cash -= delta * price * POINT_VALUE
        turnover += abs(delta)
        ledger.append({
            "signal_time": stamp.isoformat(),
            "fill_time": fill.isoformat(),
            "source": code,
            "source_net": source_net,
            "previous": previous,
            "target": target,
            "price": price,
            "quantity": abs(delta),
        })
        position = target

    for stamp, index, code, new in events:
        if code == "flat":
            legs = dict.fromkeys(STRATEGIES, 0)
            source_net = 0
            target = 0
            fill = stamp
            execute(stamp, code, source_net, target, fill)
        else:
            fill = stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
            old = legs[code]
            if mode == "path_aware" and old and new and old != new:
                # A reversal is semantically an exit followed by a new entry.
                # Both substeps share the signal's next-minute execution proxy.
                legs[code] = 0
                source_net = sum(legs.values())
                target = policy_target("hold_last", source_net, position)
                execute(stamp, code + "/exit", source_net, target, fill)
                legs[code] = new
                source_net = sum(legs.values())
                target = policy_target("hold_last", source_net, position)
                execute(stamp, code + "/entry", source_net, target, fill)
            else:
                legs[code] = new
                source_net = sum(legs.values())
                policy = "hold_last" if mode == "path_aware" else mode
                target = policy_target(policy, source_net, position)
                execute(stamp, code, source_net, target, fill)

    mark_time = max(stamp for stamp in bars if start <= stamp < end)
    mark = bars[mark_time]["close"]
    gross_profit = sum(value for value in realized_legs if value > 0)
    gross_loss = -sum(value for value in realized_legs if value < 0)
    realized = sum(realized_legs)
    unrealized = (mark - entry_price) * position * POINT_VALUE if position else 0.0
    total = realized + unrealized
    cost_twd = turnover * cost_points * POINT_VALUE
    equity = cash + position * mark * POINT_VALUE - cost_twd

    peak = 0.0
    max_drawdown = 0.0
    curve_cash = 0.0
    curve_position = 0
    cursor = 0
    for stamp in sorted(t for t in bars if start <= t < end):
        while cursor < len(ledger) and datetime.fromisoformat(ledger[cursor]["fill_time"]) <= stamp:
            item = ledger[cursor]
            change = item["target"] - curve_position
            curve_cash -= change * item["price"] * POINT_VALUE
            curve_cash -= abs(change) * cost_points * POINT_VALUE
            curve_position = item["target"]
            cursor += 1
        curve_equity = curve_cash + curve_position * bars[stamp]["close"] * POINT_VALUE
        peak = max(peak, curve_equity)
        max_drawdown = max(max_drawdown, peak - curve_equity)

    assert abs(gross_profit - gross_loss - realized) < 1e-6
    assert abs(realized + unrealized - total) < 1e-6
    assert abs(equity - (total - cost_twd)) < 1e-6
    assert turnover == sum(item["quantity"] for item in ledger)
    assert cursor == len(ledger)
    return {
        "gross_profit_twd": gross_profit,
        "gross_loss_twd": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "closed_legs": len(realized_legs),
        "realized_twd": realized,
        "unrealized_twd": unrealized,
        "total_before_cost_twd": total,
        "turnover_one_way_contracts": turnover,
        "cost_twd": cost_twd,
        "net_twd": total - cost_twd,
        "max_drawdown_twd": max_drawdown,
        "return_over_drawdown": (total - cost_twd) / max_drawdown if max_drawdown else None,
        "ending_position": position,
        "mark_time": mark_time.isoformat(),
        "mark": mark,
        "ledger": ledger,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-06-25T08:45:00")
    parser.add_argument("--end", required=True)
    parser.add_argument("--cost", type=float, default=2.0,
                        help="one-way cost in index points (default: 2)")
    parser.add_argument("--signals", type=Path,
                        default=BASE.parent / "tv_doc/six_strategy_signal_events.csv")
    parser.add_argument("--prices", type=Path,
                        default=BASE.parent / "tv_doc/webhook_data_1min.csv")
    parser.add_argument("--calendar", type=Path, default=BASE / "config/calendar.json")
    parser.add_argument("--output", type=Path,
                        default=BASE / "records/position_limit_policy_comparison.json")
    args = parser.parse_args()
    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if not start < end or args.cost < 0:
        raise ValueError("invalid period or cost")

    bars = {}
    duplicate_price_rows = 0
    with args.prices.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Symbol") != "MXF1!":
                continue
            stamp = datetime.fromisoformat(row["TradingView Time"])
            if not start <= stamp < end:
                continue
            record = datetime.fromisoformat(row["Record Time"])
            if record > end:
                continue
            duplicate_price_rows += stamp in bars
            if stamp not in bars or record < bars[stamp]["record"]:
                bars[stamp] = {
                    "open": float(row["Open"].replace(",", "")),
                    "close": float(row["Close"].replace(",", "")),
                    "record": record,
                }

    calendar = Calendar.load(args.calendar)
    events = []
    seen = set()
    audit = {"undated": 0, "duplicates": 0, "outside_window": 0,
             "excluded_strategy": 0, "outside_hours": 0}
    with args.signals.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if not row.get("received_at"):
                audit["undated"] += 1
                continue
            stamp = datetime.fromisoformat(row["received_at"])
            if not start < stamp < end:
                audit["outside_window"] += 1
                continue
            code = row.get("strategy_code") or row.get("raw_strategy_code")
            code = "CFCWIN01m" if code == "CFCWN01m" else code
            if code not in STRATEGIES:
                audit["excluded_strategy"] += 1
                continue
            if not calendar.is_open(stamp) or time(1) <= stamp.time() < time(8, 45):
                audit["outside_hours"] += 1
                continue
            new = int(float(row["new_position"]))
            previous = int(float(row["previous_position"]))
            if new not in (-1, 0, 1) or previous not in (-1, 0, 1):
                raise ValueError(f"invalid EF position at row {index}")
            key = (stamp, row.get("message_time"), code, previous, new)
            if key in seen:
                audit["duplicates"] += 1
                continue
            seen.add(key)
            events.append((stamp, index, code, new))

    day = start.date()
    while day <= end.date():
        closure = calendar.closure(day)
        if closure and start <= closure.start < end:
            events.append((closure.start, -1, "flat", 0))
        day += timedelta(days=1)
    events.sort()

    missing = []
    excluded_cycles = set()
    for stamp, _, code, _ in events:
        fill = stamp if code == "flat" else stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        if fill not in bars:
            missing.append(fill.isoformat())
            excluded_cycles.add(trading_cycle(stamp))
    audit["missing_candidate_bars"] = missing
    audit["excluded_cycles"] = sorted(map(str, excluded_cycles))
    audit["signals_in_excluded_cycles"] = sum(
        code != "flat" and trading_cycle(stamp) in excluded_cycles
        for stamp, _, code, _ in events
    )
    events = [event for event in events if trading_cycle(event[0]) not in excluded_cycles]
    audit["eligible_signals"] = sum(event[2] != "flat" for event in events)
    audit["included_cycles"] = len({trading_cycle(event[0]) for event in events if event[2] != "flat"})

    results = {
        mode: replay(events, bars, mode, start, end, args.cost)
        for mode in ("hold_last", "clamp", "flat_wait", "path_aware")
    }
    result = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "audit": audit,
        "duplicate_price_rows": duplicate_price_rows,
        "assumptions": {
            "start_boundary": "flat",
            "event_time": "local received_at",
            "fill": "exact next-minute MXF1! open; daily flatten at exact 01:00 open",
            "price_limitation": "historical MXF1! OHLC proxy, not actual TMF fill or an observable live quote",
            "point_value_twd": POINT_VALUE,
            "one_way_cost_points": args.cost,
            "hold_last": "keep the last accepted account target while abs(source net) > 1",
            "clamp": "map every source net to sign(net), limited to -1/0/+1",
            "flat_wait": "target zero while abs(source net) > 1; re-enter when it returns to -1/0/+1",
            "path_aware": "split -1<->+1 reversals into exit and entry substeps; apply hold_last at each substep",
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {mode: {key: value for key, value in values.items() if key != "ledger"}
               for mode, values in results.items()}
    print(json.dumps({**result, "results": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
