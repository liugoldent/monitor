"""Offline same-clock comparison of Clamp and its opposite target book."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def score(ledger, bars, start, end, cost, direction):
    position = turnover = 0
    entry = entered = None
    realized = 0.0
    trades = []
    actions = [(datetime.fromisoformat(x["fill_time"]), 1, i, x) for i, x in enumerate(ledger)]
    actions.extend((t + timedelta(minutes=1), 0, i, {"close": v[2]})
                   for i, (t, v) in enumerate(sorted(bars.items())) if start <= t and t + timedelta(minutes=1) <= end)
    peak = dd = equity = 0.0
    daily = {}
    for stamp, kind, _, action in sorted(actions):
        if kind == 0:
            equity = realized + ((action["close"] - entry) * position if position else 0) - turnover * cost
        else:
            price = action["price"]
            target = action["target"] * direction
            if target == position:
                continue
            if position:
                gross = (price - entry) * position
                realized += gross
                trades.append({"entry": str(entered), "exit": str(stamp), "side": position,
                               "gross_points": gross, "net_points": gross - 2 * abs(position) * cost,
                               "entry_price": entry, "exit_price": price})
            turnover += abs(target - position)
            position, entry, entered = target, price if target else None, stamp if target else None
            equity = realized - turnover * cost
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        daily[str(stamp.date())] = equity
    net = [t["net_points"] for t in trades]
    gross_profit = sum(t["gross_points"] for t in trades if t["gross_points"] > 0)
    gross_loss = -sum(t["gross_points"] for t in trades if t["gross_points"] < 0)
    assert abs(gross_profit - gross_loss - realized) < 1e-8
    assert turnover == sum(2 * abs(t["side"]) for t in trades) + abs(position)
    increments, prior = {}, 0.0
    for day, value in daily.items():
        increments[day], prior = value - prior, value
    return {"closed_trades": len(trades), "gross_profit_points": gross_profit,
            "gross_loss_points": gross_loss, "realized_gross_points": realized,
            "gross_profit_factor": gross_profit / gross_loss if gross_loss else None,
            "realized_net_points": sum(net), "net_total_points": equity,
            "unrealized_points": equity + turnover * cost - realized,
            "expectancy_net_points": sum(net) / len(net) if net else None,
            "gross_failure_rate": sum(t["gross_points"] < 0 for t in trades) / len(trades) if trades else None,
            "net_win_rate": sum(t["net_points"] > 0 for t in trades) / len(trades) if trades else None,
            "one_way_turnover": turnover, "ending_position": position,
            "max_drawdown_net_points": dd, "daily_net_points": increments, "trades": trades}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-10-12 00:00:00")
    parser.add_argument("--end", required=True)
    parser.add_argument("--cost", type=float, default=2.4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start, end = map(datetime.fromisoformat, (args.start, args.end))
    root = Path(__file__).resolve().parents[1]
    folder = root / "backend-futures-py/ef-morning-weekend-hedge-strategy"
    prices = root / "backend-futures-py/tv_doc/webhook_data_1min.csv"
    signals = root / "backend-futures-py/tv_doc/six_strategy_signal_events.csv"
    notification_path = folder / "records/notifications.jsonl"
    frozen = {str(p.relative_to(root)): p.read_bytes() for p in (prices, signals, notification_path)}
    bars = {}
    with prices.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            t = datetime.fromisoformat(row["TradingView Time"])
            record = datetime.fromisoformat(row["Record Time"])
            if row["Symbol"] != "MXF1!" or t + timedelta(minutes=1) > end or record > end:
                continue
            value = record, float(row["Open"].replace(",", "")), float(row["Close"].replace(",", ""))
            if t not in bars or record < bars[t][0]:
                bars[t] = value
    notices, versions = [], []
    seen, missing, version = set(), [], "unknown"
    for line in frozen[str(notification_path.relative_to(root))].decode("utf-8-sig").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        stamp = datetime.fromisoformat(row["timestamp"])
        if stamp >= end:
            continue
        text = row.get("content", "")
        match = re.search(r"版本：([^；\n]+)", text)
        if match:
            version = match[1]
            if stamp >= start:
                versions.append({"at": str(stamp), "version": version,
                                 "mode": "simulation" if "模擬帳戶" in text else "live_label"})
        if stamp < start or row.get("status") != "sent":
            continue
        target = re.search(r"帳戶目標口數：([^\n]+)", text)
        trigger = re.search(r"觸發：([^\n]+)", text)
        if not target or not trigger or trigger[1] in seen:
            continue
        seen.add(trigger[1])
        label = target[1]
        if "空手" in label:
            target_position = 0
        else:
            match = re.search(r"(多|空)\s*(\d+)\s*口", label)
            if not match:
                raise ValueError(f"unrecognized target at {stamp}: {label}")
            target_position = int(match[2]) * (1 if match[1] == "多" else -1)
        fill = stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        if fill >= end or fill not in bars:
            missing.append({"at": str(stamp), "fill": str(fill), "trigger": trigger[1]})
            continue
        notices.append({"signal_time": str(stamp), "fill_time": str(fill), "target": target_position,
                        "price": bars[fill][1], "version": version, "trigger": trigger[1]})
    if missing:
        raise ValueError(f"missing exact next-open price: {missing}")
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location("clamp_replay_week", folder / "backtest.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    books = {"recorded_published_targets": notices}
    for flag, name in ((True, "current_v12_whole_week"), (False, "legacy_v9_whole_week")):
        result = native.run(signals, prices, native.Calendar.load(folder / "config/calendar.json"),
                            start, end, cost=0, short_exit_to_long=flag)
        books[name] = result["ledger"]
    result = {"period": [str(start), str(end)], "one_way_cost_points": args.cost,
              "contract": "one unit; MXF1! proxy, not confirmed TMF fills; same exit clock, opposite target",
              "published_target_fill": "first minute open after sent notification timestamp; start flat; include only recorded targets",
              "native_fill": "existing backtest: next minute after received_at; exact 01:00 flat; start flat",
              "limitations": ["week contains rule/version and live/simulation-label changes",
                              "published-target book fixes historical decisions, including their actual-inventory-dependent gates",
                              "order submissions and target notices are not confirmed execution records",
                              "counterfactual opposite book retains published target/exit path, does not re-run broker-dependent gating"],
              "versions": versions, "source_sha256": {p: hashlib.sha256(v).hexdigest() for p, v in frozen.items()},
              "books": {}}
    exported = []
    for name, ledger in books.items():
        original = score(ledger, bars, start, end, args.cost, 1)
        reverse = score(ledger, bars, start, end, args.cost, -1)
        assert original["closed_trades"] == reverse["closed_trades"]
        assert abs(original["realized_gross_points"] + reverse["realized_gross_points"]) < 1e-8
        assert original["one_way_turnover"] == reverse["one_way_turnover"]
        assert abs(original["net_total_points"] + reverse["net_total_points"] +
                   2 * args.cost * original["one_way_turnover"]) < 1e-8
        grouped = {}
        for side, label in ((1, "original_long"), (-1, "original_short")):
            legs = [t for t in original["trades"] if t["side"] * side > 0]
            gross = sum(t["gross_points"] for t in legs)
            costs = sum(2 * abs(t["side"]) * args.cost for t in legs)
            grouped[label] = {"trades": len(legs), "original_net_points": gross - costs,
                              "reverse_net_points": -gross - costs,
                              "reverse_net_expectancy_points": (-gross - costs) / len(legs) if legs else None,
                              "reverse_wins": sum(-t["gross_points"] - 2*abs(t["side"])*args.cost > 0 for t in legs)}
        result["books"][name] = {"original": original, "reverse": reverse,
                                 "by_original_direction": grouped, "target_events": len(ledger)}
        for side, scored in (("original", original), ("reverse", reverse)):
            exported.extend({"book": name, "mode": side, **t} for t in scored["trades"])
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.output / "trades.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(exported[0]))
        writer.writeheader()
        writer.writerows(exported)
    print(json.dumps({name: {side: {k:v for k,v in data.items() if k != "trades"}
                            for side,data in book.items() if isinstance(data,dict)}
                      for name,book in result["books"].items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
