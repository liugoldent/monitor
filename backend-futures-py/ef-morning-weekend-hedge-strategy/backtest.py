"""Offline hedge-only ledger using exact 04:59/08:45 Open proxies."""
import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from strategy import Calendar, hedge_target, signal_position

BASE = Path(__file__).resolve().parent


def run(signals, prices, calendar, start, end, cost=2.0, unit=1):
    if not start < end or cost < 0:
        raise ValueError("區間必須遞增且成本不可為負")
    bars = {}
    with prices.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Symbol") != "MXF1!":
                continue
            stamp = datetime.fromisoformat(row["TradingView Time"])
            value = (row["Record Time"], float(row["Open"].replace(",", "")))
            if stamp not in bars or value[0] > bars[stamp][0]:
                bars[stamp] = value
    day, ledger, skipped = start.date(), [], []
    while day <= end.date():
        closure = calendar.closure(day)
        day += timedelta(days=1)
        if not closure or not start <= closure.start < end:
            continue
        if closure.reopen >= end or closure.start not in bars or closure.reopen not in bars:
            skipped.append({"start": closure.start.isoformat(), "reason": "missing_exact_boundary_or_outside_interval"})
            continue
        try:
            source = signal_position(signals, closure.start, unit)
        except ValueError as exc:
            skipped.append({"start": closure.start.isoformat(), "reason": str(exc)})
            continue
        target = hedge_target(source["net_position"], closure.cap)
        price_in, price_out = bars[closure.start][1], bars[closure.reopen][1]
        points = target * (price_out - price_in) - 2 * abs(target) * cost
        ledger.append({"start": closure.start.isoformat(), "reopen": closure.reopen.isoformat(),
                       "source_net": source["net_position"], "cap": closure.cap, "hedge": target,
                       "entry_proxy": price_in, "exit_proxy": price_out,
                       "net_points_contracts": points, "net_twd": points * 10,
                       "source_mismatches": source["mismatches"]})
    return {"scope": "hedge_account_only", "price_proxy": "MXF1! exact boundary Open; not TMF fills",
            "single_side_cost_points": cost, "source_unit": unit,
            "net_twd": sum(r["net_twd"] for r in ledger), "ledger": ledger, "skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="不含結束時間")
    parser.add_argument("--cost", type=float, default=2)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--signals", type=Path, default=BASE.parent / "tv_doc/six_strategy_signal_events.csv")
    parser.add_argument("--prices", type=Path, default=BASE.parent / "tv_doc/webhook_data_1min.csv")
    parser.add_argument("--calendar", type=Path, default=BASE / "config/calendar.json")
    parser.add_argument("--output", type=Path, default=BASE / "records/backtest.json")
    args = parser.parse_args()
    result = run(args.signals, args.prices, Calendar.load(args.calendar),
                 datetime.fromisoformat(args.start), datetime.fromisoformat(args.end), args.cost, args.unit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"ledger", "skipped"}}, ensure_ascii=False))
    print(f"納入 {len(result['ledger'])} 次，排除 {len(result['skipped'])} 次；詳情 {args.output}")


if __name__ == "__main__":
    main()
