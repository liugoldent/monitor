"""Pure EF replay: next-minute Open for signals, exact 04:59 Open for flattening."""
import argparse
import csv
import json
from datetime import datetime, time, timedelta
from pathlib import Path

from strategy import Calendar, STRATEGIES, integer

BASE = Path(__file__).resolve().parent


def run(signals, prices, calendar, start, end, cost=2.0, unit=1):
    unit = integer(unit)
    if not start < end or cost < 0 or not 1 <= unit <= 20:
        raise ValueError("區間、成本或每策略口數不合法")
    bars = {}
    with prices.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Symbol") == "MXF1!":
                stamp = datetime.fromisoformat(row["TradingView Time"])
                value = (row["Record Time"], float(row["Open"].replace(",", "")))
                if stamp not in bars or value[0] > bars[stamp][0]:
                    bars[stamp] = value
    events = []
    with signals.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            code = row.get("strategy_code") or row.get("raw_strategy_code")
            code = "CFCWIN01m" if code == "CFCWN01m" else code
            if code not in STRATEGIES or not row.get("received_at"):
                continue
            stamp = datetime.fromisoformat(row["received_at"])
            if not start < stamp < end or not calendar.is_open(stamp):
                continue
            if time(4, 59) <= stamp.time() < time(8, 45):
                continue
            target = integer(row["new_position"])
            if target not in {-1, 0, 1}:
                raise ValueError("EF 訊號部位超出 -1/0/1")
            events.append((stamp, index, code, target))
    day = start.date()
    while day <= end.date():
        closure = calendar.closure(day)
        if closure and start <= closure.start < end:
            events.append((closure.start, -1, "flat", 0))
        day += timedelta(days=1)
    legs = dict.fromkeys(STRATEGIES, 0)
    position, cash, ledger = 0, 0.0, []
    for stamp, _, code, new in sorted(events):
        if code == "flat":
            legs = dict.fromkeys(STRATEGIES, 0)
            fill = stamp
        else:
            legs[code] = new
            fill = stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        target = sum(legs.values()) * unit
        if target == position:
            continue
        if fill >= end:
            raise ValueError(f"{stamp} 的成交代理時間超出回放區間")
        if fill not in bars:
            raise ValueError(f"缺少精確成交代理 K 棒 {fill}；停止回放，不跳過缺口")
        price = bars[fill][1]
        delta = target - position
        cash -= delta * price + abs(delta) * cost
        ledger.append({"signal_time": stamp.isoformat(), "fill_time": fill.isoformat(),
                       "kind": code, "previous": position, "target": target, "price": price})
        position = target
    marks = [stamp for stamp in bars if start <= stamp < end]
    if position and not marks:
        raise ValueError("缺少期末評價 K 棒")
    mark = bars[max(marks)][1] if marks else 0
    return {"scope": "pure_ef_account2_morning_flat", "start_flat": True,
            "price_proxy": "MXF1! next-minute Open; flat exact 04:59 Open; not TMF fills",
            "single_side_cost_points": cost, "source_unit": unit,
            "net_twd": (cash + position * mark) * 10,
            "ending_position": position, "ending_mark": mark, "ledger": ledger}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cost", type=float, default=2)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--signals", type=Path, default=BASE.parent / "tv_doc/six_strategy_signal_events.csv")
    parser.add_argument("--prices", type=Path, default=BASE.parent / "tv_doc/webhook_data_1min.csv")
    parser.add_argument("--calendar", type=Path, default=BASE / "config/calendar.json")
    parser.add_argument("--output", type=Path, default=BASE / "records/pure_ef_backtest.json")
    args = parser.parse_args()
    result = run(args.signals, args.prices, Calendar.load(args.calendar),
                 datetime.fromisoformat(args.start), datetime.fromisoformat(args.end), args.cost, args.unit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "ledger"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
