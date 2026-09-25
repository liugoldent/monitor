"""Compare the three EF account candidates with one event-replay contract."""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path


E = ("CFC07m", "CFCTX17m", "CFCTX18m", "CFCTX19m", "CFCTX20m", "CFCTX21m", "CFCTX15m")
F = ("CFCWIN01m", "CFCPW3m", "CFCCPm", "CFCTX16m", "CFCTX22m", "CFCTX23m")
ALL = E + F


@dataclass
class Result:
    gross_profit: float = 0
    gross_loss: float = 0
    realized: float = 0
    unrealized: float = 0
    turnover: int = 0
    closed_legs: int = 0
    ending_position: int = 0
    max_drawdown: float = 0
    trade_pnls: list[float] = field(default_factory=list)
    month_end_net: dict[str, float] = field(default_factory=dict)
    delayed_fills: int = 0
    max_fill_delay_minutes: int = 0

    @property
    def total(self):
        return self.realized + self.unrealized

    @property
    def pf(self):
        return self.gross_profit / self.gross_loss if self.gross_loss else math.inf


def load_data(root: Path):
    prices = root / "backend-futures-py/tv_doc/webhook_data_1min.csv"
    signals = root / "backend-futures-py/tv_doc/six_strategy_signal_events.csv"
    bars = {}
    with prices.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Symbol") != "MXF1!":
                continue
            stamp = datetime.fromisoformat(row["TradingView Time"])
            record = datetime.fromisoformat(row["Record Time"])
            value = (record, float(row["Open"].replace(",", "")), float(row["Close"].replace(",", "")))
            if stamp not in bars or record >= bars[stamp][0]:
                bars[stamp] = value
    events, seen, untimed, duplicates = [], set(), 0, 0
    with signals.open(encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=1):
            code = row.get("strategy_code") or row.get("raw_strategy_code")
            code = "CFCWIN01m" if code == "CFCWN01m" else code
            received = (row.get("received_at") or "").strip()
            if not received:
                untimed += 1
                continue
            if code not in ALL:
                continue
            stamp = datetime.fromisoformat(received)
            old, new = int(float(row["previous_position"])), int(float(row["new_position"]))
            key = (stamp, code, old, new, row.get("account") or "")
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            events.append((stamp, row_number, code, new))
    events.sort()
    calendar_path = root / "backend-futures-py/ef-morning-weekend-hedge-strategy/config/calendar.json"
    calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
    return bars, events, untimed, duplicates, calendar


def calendar_open(stamp: datetime, calendar: dict) -> bool:
    closed = set(calendar["closed_dates"])
    opened = set(calendar.get("open_dates", []))

    def trading_day(day):
        text = day.isoformat()
        return text in opened or (day.weekday() < 5 and text not in closed)

    clock = stamp.time()
    if clock < time(5):
        return trading_day(stamp.date() - timedelta(days=1))
    if time(8, 45) <= clock < time(13, 45):
        return trading_day(stamp.date())
    if clock >= time(15):
        return trading_day(stamp.date())
    return False


def nets(positions):
    return sum(positions[c] for c in E), sum(positions[c] for c in F)


def night_close_baseline(events, trading_date):
    """Return E+F as known from receipts strictly before 05:00."""
    cutoff = datetime.combine(trading_date, time(5))
    positions = dict.fromkeys(ALL, 0)
    for stamp, _, code, new in events:
        if stamp >= cutoff:
            break
        positions[code] = new
    e_net, f_net = nets(positions)
    total = e_net + f_net
    return max(total, 0), max(-total, 0)


def run(name, bars, events, start, end, cost, calendar):
    times = sorted(t for t in bars if start <= t <= end)
    if not times:
        raise RuntimeError("no bars")
    all_bar_times = sorted(bars)
    actions = []
    for stamp, row, code, new in events:
        if start <= stamp <= end:
            fill = stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
            actions.append((fill, 1, row, stamp, code, new))
    for stamp in times:
        if stamp.time() == time(1, 0):
            actions.append((stamp, 0, -1, stamp, "flat", 0))
    actions.sort(key=lambda x: (x[0], x[1], x[2]))

    positions = dict.fromkeys(ALL, 0)
    for stamp, _, code, new in events:
        if stamp >= start:
            break
        positions[code] = new
    position = 0
    entry = None
    realized = peak = 0.0
    result = Result()
    long_locked = short_locked = False
    lock_cycle = None
    baseline = None
    ai = 0

    for bar_time in times:
        while ai < len(actions) and actions[ai][0] <= bar_time:
            fill_time, kind, _, event_time, code, new = actions[ai]
            index = bisect.bisect_left(all_bar_times, fill_time)
            if index >= len(all_bar_times) or all_bar_times[index] > times[-1]:
                ai += 1
                continue
            actual_fill = all_bar_times[index]
            delay = int((actual_fill - fill_time).total_seconds() // 60)
            price = bars[actual_fill][1]
            if kind == 0:
                target = 0
                long_locked = short_locked = False
                lock_cycle = None
                baseline = None
                if name == "pure_ef_clamp":
                    positions = dict.fromkeys(ALL, 0)
            elif name == "pure_ef_clamp":
                if not calendar_open(event_time, calendar):
                    ai += 1
                    continue
                positions[code] = new
                total = sum(positions.values())
                target = 1 if total > 0 else -1 if total < 0 else 0
            else:
                morning = time(1, 0) <= event_time.time() < time(8, 45)
                cycle = (event_time.date() if event_time.time() >= time(1) else
                         event_time.date() - timedelta(days=1))
                if not morning and lock_cycle != cycle:
                    e0, f0 = nets(positions)
                    if name == "hysteresis_again":
                        long_locked = e0 >= 2 and f0 >= 2
                        short_locked = e0 <= -2 and f0 <= -2
                    else:
                        baseline = night_close_baseline(events, cycle)
                    lock_cycle = cycle
                positions[code] = new
                e_net, f_net = nets(positions)
                bull, bear = e_net >= 2 and f_net >= 2, e_net <= -2 and f_net <= -2
                if morning:
                    target = 0
                elif name == "hysteresis_again":
                    if long_locked and not bull:
                        long_locked = False
                    if short_locked and not bear:
                        short_locked = False
                    if bear:
                        target = 0 if short_locked and position >= 0 else -1
                    elif bull:
                        target = 0 if long_locked and position <= 0 else 1
                    elif position > 0 and e_net >= 1 and f_net >= 1:
                        target = 1
                    elif position < 0 and e_net <= -1 and f_net <= -1:
                        target = -1
                    else:
                        target = 0
                else:
                    bull_base, bear_base = baseline or (0, 0)
                    total = e_net + f_net
                    if bull:
                        target = 1 if position > 0 or total > bull_base else 0
                    elif bear:
                        target = -1 if position < 0 or -total > bear_base else 0
                    elif position > 0 and e_net >= 1 and f_net >= 1:
                        target = 1
                    elif position < 0 and e_net <= -1 and f_net <= -1:
                        target = -1
                    else:
                        target = 0
            if target != position:
                if delay > 0:
                    result.delayed_fills += 1
                    result.max_fill_delay_minutes = max(result.max_fill_delay_minutes, delay)
                if position:
                    pnl = (price - entry) * position
                    result.trade_pnls.append(pnl)
                    result.closed_legs += 1
                    realized += pnl
                    if pnl > 0:
                        result.gross_profit += pnl
                    elif pnl < 0:
                        result.gross_loss += -pnl
                result.turnover += abs(target - position)
                position = target
                entry = price if target else None
            ai += 1
        close = bars[bar_time][2]
        unrealized = (close - entry) * position if position else 0
        gross_equity = realized + unrealized
        net_equity = gross_equity - result.turnover * cost
        peak = max(peak, net_equity)
        result.max_drawdown = min(result.max_drawdown, net_equity - peak)
        result.unrealized = unrealized
        result.month_end_net[bar_time.strftime("%Y-%m")] = net_equity
    result.realized = realized
    result.ending_position = position
    assert abs(result.gross_profit - result.gross_loss - result.realized) < 1e-9
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-06-24 00:00:00")
    parser.add_argument("--end", required=True)
    parser.add_argument("--cost", type=float, default=2.4)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    bars, events, untimed, duplicates, calendar = load_data(args.root)
    output = {"period": [str(start), str(end)], "cost_points_one_way": args.cost,
              "point_value_twd": 10, "untimed_skipped": untimed, "duplicates": duplicates,
              "fill": "received_at -> first MXF1! bar open at/after next minute; 01:00 exact open", "strategies": {}}
    for name in ("hysteresis_again", "pure_ef_clamp", "total_breakout"):
        r = run(name, bars, events, start, end, args.cost, calendar)
        months, prior = {}, 0.0
        for month, value in r.month_end_net.items():
            months[month] = value - prior
            prior = value
        output["strategies"][name] = {
            "gross_profit_points": r.gross_profit, "gross_loss_points": r.gross_loss,
            "profit_factor": r.pf, "closed_legs": r.closed_legs,
            "win_rate": sum(x > 0 for x in r.trade_pnls) / len(r.trade_pnls) if r.trade_pnls else None,
            "realized_points": r.realized, "unrealized_points": r.unrealized,
            "gross_total_points": r.total, "turnover_one_way": r.turnover,
            "estimated_net_points": r.total - r.turnover * args.cost,
            "estimated_net_twd": (r.total - r.turnover * args.cost) * 10,
            "max_drawdown_net_points": r.max_drawdown,
            "ending_position": r.ending_position, "monthly_net_points": months,
            "delayed_fills": r.delayed_fills,
            "max_fill_delay_minutes": r.max_fill_delay_minutes,
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
