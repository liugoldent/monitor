"""Pure EF closure hedge: signed micro-Taiwan-futures contracts, Taipei time."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

STRATEGIES = (
    "CFC07m", "CFCTX17m", "CFCTX18m", "CFCTX19m", "CFCTX20m", "CFCTX21m",
    "CFCWIN01m", "CFCPW3m", "CFCCPm", "CFCTX16m", "CFCTX22m", "CFCTX23m",
)


def integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("口數不能是布林值")
    number = Decimal(str(value))
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError("口數必須是有限整數")
    return int(number)


def hedge_target(net: int, cap: int) -> int:
    net, cap = integer(net), integer(cap)
    if cap < 0:
        raise ValueError("保留口數不可為負數")
    return -(1 if net > 0 else -1) * max(abs(net) - cap, 0)


@dataclass(frozen=True)
class Closure:
    start: datetime
    reopen: datetime
    cap: int


class Calendar:
    def __init__(self, config: dict):
        self.first = date.fromisoformat(config["valid_from"])
        self.last = date.fromisoformat(config["valid_through"])
        self.closed = {date.fromisoformat(d) for d in config["closed_dates"]}
        self.opened = {date.fromisoformat(d) for d in config.get("open_dates", [])}
        self.no_night = {date.fromisoformat(d) for d in config.get("no_night_dates", [])}

    @classmethod
    def load(cls, path: Path) -> "Calendar":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def trading_day(self, day: date) -> bool:
        if not self.first <= day <= self.last:
            raise ValueError(f"交易日曆未涵蓋 {day}；請更新日曆")
        return day in self.opened or (day.weekday() < 5 and day not in self.closed)

    def night(self, day: date) -> bool:
        return self.trading_day(day) and day not in self.no_night

    def closure(self, day: date, weekday_cap: int = 2, holiday_cap: int = 1) -> Closure | None:
        # The 01:00 session belongs to the previous civil day's afternoon.
        if not self.night(day - timedelta(days=1)):
            return None
        reopen = day
        while not self.trading_day(reopen):
            reopen += timedelta(days=1)
        return Closure(datetime.combine(day, time(1, 0)),
                       datetime.combine(reopen, time(8, 45)),
                       weekday_cap if reopen == day else holiday_cap)

    def is_open(self, now: datetime) -> bool:
        day, clock = now.date(), now.time()
        if clock < time(5):
            return self.night(day - timedelta(days=1))
        if time(8, 45) <= clock < time(13, 45):
            return self.trading_day(day)
        if clock >= time(15):
            return self.night(day)
        return False


def signal_position(path: Path, now: datetime, unit: int = 1) -> dict:
    """Estimate from all 12 latest dated states; never infer missing legs as flat."""
    unit = integer(unit)
    if not 1 <= unit <= 20:
        raise ValueError("每策略口數必須介於 1 與 20")
    positions, events, mismatches = {}, [], 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            code = row.get("strategy_code") or row.get("raw_strategy_code")
            code = "CFCWIN01m" if code == "CFCWN01m" else code
            if code not in STRATEGIES or not row.get("received_at"):
                continue
            timestamp = datetime.strptime(row["received_at"], "%Y-%m-%d %H:%M:%S")
            if timestamp > now:
                continue
            previous, new = integer(row["previous_position"]), integer(row["new_position"])
            if previous not in {-1, 0, 1} or new not in {-1, 0, 1}:
                raise ValueError(f"{code} 訊號部位超出 -1/0/1")
            events.append((timestamp, index, code, previous, new))
    for timestamp, _, code, previous, new in sorted(events):
        if code in positions and positions[code] != previous:
            mismatches += 1
        positions[code] = new
    missing = set(STRATEGIES) - positions.keys()
    if missing:
        raise ValueError(f"缺少 EF 策略狀態：{', '.join(sorted(missing))}")
    return {"net_position": sum(positions.values()) * unit, "positions": positions,
            "source": "signal_csv_estimate", "mismatches": mismatches,
            "observed_at": max(e[0] for e in events).isoformat(), "unit": unit}


def snapshot_position(path: Path, now: datetime, max_age: int = 120) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("source") != "capital_pure_ef":
        raise ValueError("庫存快照 source 必須是 capital_pure_ef")
    stamp = datetime.fromisoformat(value["observed_at"])
    if stamp.tzinfo is not None:
        from zoneinfo import ZoneInfo
        stamp = stamp.astimezone(ZoneInfo("Asia/Taipei")).replace(tzinfo=None)
    if not 0 <= (now - stamp).total_seconds() <= max_age:
        raise ValueError("群益 EF 庫存快照過期或來自未來")
    value["net_position"] = integer(value["net_position"])
    if not value.get("contract"):
        raise ValueError("庫存快照缺少實際合約代碼 contract")
    return value


def latest_closure(calendar: Calendar, now: datetime) -> Closure | None:
    day = now.date()
    while day > calendar.first:
        closure = calendar.closure(day)
        if closure and closure.start <= now:
            return closure
        day -= timedelta(days=1)
    return None


def pure_position(path: Path, now: datetime, since: datetime, calendar: Calendar,
                  unit: int = 1, boot: datetime | None = None,
                  initial_from_signal: bool = False, start_index: int | None = None) -> dict:
    """Only post-reopen signals update their own leg; old legs never restore."""
    unit = integer(unit)
    if not 1 <= unit <= 20:
        raise ValueError("每策略口數必須介於 1 與 20")
    events = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            code = row.get("strategy_code") or row.get("raw_strategy_code")
            code = "CFCWIN01m" if code == "CFCWN01m" else code
            if code not in STRATEGIES or not row.get("received_at"):
                continue
            stamp = datetime.strptime(row["received_at"], "%Y-%m-%d %H:%M:%S")
            if not since <= stamp <= now:
                continue
            if start_index is not None:
                if index < start_index:
                    continue
            elif boot is not None and stamp <= boot:
                continue
            if not calendar.is_open(stamp) or time(1, 0) <= stamp.time() < time(8, 45):
                continue
            new = integer(row["new_position"])
            if new not in {-1, 0, 1}:
                raise ValueError(f"{code} 訊號部位超出 -1/0/1")
            events.append((stamp, index, code, new, integer(row.get("previous_position") or 0),
                           (row.get("strategy_name") or "").strip()))
    positions = dict.fromkeys(STRATEGIES, 0)
    events.sort()
    steps = []
    seen = set()
    for stamp, index, code, new, reported_previous, name in events:
        if initial_from_signal and code not in seen:
            if reported_previous not in {-1, 0, 1}:
                raise ValueError(f"{code} 訊號部位超出 -1/0/1")
            positions[code] = reported_previous
        seen.add(code)
        previous = positions[code]
        positions[code] = new
        steps.append({"net_position": sum(positions.values()) * unit,
                      "positions": positions.copy(), "source": "pure_ef_new_signals",
                      "unit": unit, "last_signal": f"{stamp.isoformat()}/{index}",
                      "strategy_code": code, "strategy_name": name,
                      "signal_previous_position": reported_previous, "previous_position": previous,
                      "new_position": new})
    return {"steps": steps, "net_position": sum(positions.values()) * unit, "positions": positions,
            "source": "pure_ef_new_signals", "unit": unit,
            "last_signal": (f"{events[-1][0].isoformat()}/{events[-1][1]}" if events else None)}
