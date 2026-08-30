from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Mapping


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
MORNING_FLAT_TIME = time(4, 59)
DAY_OPEN_TIME = time(8, 45)
DAY_FLAT_TIME = time(13, 44)
NIGHT_OPEN_TIME = time(15, 0)

PORTFOLIO_E = (
    "CFC07m",
    "CFCTX17m",
    "CFCTX18m",
    "CFCTX19m",
    "CFCTX20m",
    "CFCTX21m",
)
PORTFOLIO_F = (
    "CFCWIN01m",
    "CFCPW3m",
    "CFCCPm",
    "CFCTX16m",
    "CFCTX22m",
    "CFCTX23m",
)
ALL_STRATEGIES = PORTFOLIO_E + PORTFOLIO_F
STRATEGY_ALIASES = {"CFCWN01m": "CFCWIN01m"}


@dataclass(frozen=True)
class SignalEvent:
    row_number: int
    timestamp: datetime
    strategy_code: str
    previous_position: int
    new_position: int
    strategy_name: str = ""


@dataclass(frozen=True)
class PriceBar:
    bar_time: datetime
    record_time: datetime
    open: float
    close: float


@dataclass(frozen=True)
class BoundaryEvent:
    key: str
    kind: str
    timestamp: datetime
    record_time: datetime
    price: float


@dataclass(frozen=True)
class SignalDecision:
    event: SignalEvent
    execution_time: datetime
    execution_price: float
    previous_position: int
    target_position: int
    previous_net_position: int
    net_position: int
    phase: str
    reason: str


def parse_time(value: object) -> datetime:
    return datetime.strptime(str(value or "").strip(), TIME_FORMAT)


def parse_position(value: object) -> int:
    position = int(float(str(value).strip()))
    if position not in {-1, 0, 1}:
        raise ValueError(f"策略倉位必須是-1、0或1，目前為{position}")
    return position


def normalize_strategy_code(value: object) -> str:
    code = str(value or "").strip()
    return STRATEGY_ALIASES.get(code, code)


def parse_position_row(row: Mapping[str, object]) -> tuple[str, int] | None:
    code = normalize_strategy_code(row.get("strategy_code") or row.get("raw_strategy_code"))
    if code not in ALL_STRATEGIES:
        return None
    try:
        return code, parse_position(row.get("new_position"))
    except (TypeError, ValueError):
        return None


def parse_signal_row(row: Mapping[str, object], row_number: int) -> SignalEvent | None:
    parsed = parse_position_row(row)
    if parsed is None:
        return None
    received_at = str(row.get("received_at") or "").strip()
    if not received_at:
        return None
    code, new_position = parsed
    try:
        return SignalEvent(
            row_number=row_number,
            timestamp=parse_time(received_at),
            strategy_code=code,
            previous_position=parse_position(row.get("previous_position")),
            new_position=new_position,
            strategy_name=str(row.get("strategy_name") or "").strip(),
        )
    except (TypeError, ValueError):
        return None


def load_signal_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def load_price_bars(path: Path) -> list[PriceBar]:
    if not path.exists():
        return []
    values: dict[datetime, PriceBar] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                bar = PriceBar(
                    bar_time=parse_time(row["TradingView Time"]),
                    record_time=parse_time(row["Record Time"]),
                    open=float(str(row["Open"]).replace(",", "")),
                    close=float(str(row["Close"]).replace(",", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            current = values.get(bar.bar_time)
            if current is None or bar.record_time >= current.record_time:
                values[bar.bar_time] = bar
    return [values[key] for key in sorted(values)]


def next_minute_open(bars: list[PriceBar], timestamp: datetime) -> PriceBar | None:
    if not bars:
        return None
    target = timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
    times = [bar.bar_time for bar in bars]
    index = bisect.bisect_left(times, target)
    return None if index >= len(bars) else bars[index]


def normalized_positions(value: object) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    result = {code: 0 for code in ALL_STRATEGIES}
    for code in ALL_STRATEGIES:
        try:
            position = int(source.get(code, 0))
        except (TypeError, ValueError):
            position = 0
        result[code] = position if position in {-1, 0, 1} else 0
    return result


def net_position(positions: Mapping[str, int]) -> int:
    return sum(int(positions.get(code, 0)) for code in ALL_STRATEGIES)


def trading_phase(value: datetime) -> str:
    clock = value.time()
    if MORNING_FLAT_TIME <= clock < DAY_OPEN_TIME:
        return "morning_block"
    if DAY_OPEN_TIME <= clock < DAY_FLAT_TIME:
        return "day_active"
    if DAY_FLAT_TIME <= clock < NIGHT_OPEN_TIME:
        return "day_break"
    return "night_active"


def signal_is_blocked(event_time: datetime, execution_time: datetime) -> bool:
    return trading_phase(event_time) in {"morning_block", "day_break"} or trading_phase(
        execution_time
    ) in {"morning_block", "day_break"}


def apply_signal(
    raw_positions: dict[str, int],
    active_positions: dict[str, int],
    event: SignalEvent,
    execution_bar: PriceBar,
) -> SignalDecision:
    previous = int(active_positions.get(event.strategy_code, 0))
    previous_net = net_position(active_positions)
    raw_positions[event.strategy_code] = event.new_position
    phase = trading_phase(execution_bar.bar_time)
    if signal_is_blocked(event.timestamp, execution_bar.bar_time):
        target = 0
        reason = (
            "04:59～08:45早晨風控期間不恢復舊倉"
            if phase == "morning_block"
            else "13:44～15:00日夜盤休市風控期間維持空手"
        )
    else:
        target = event.new_position
        reason = "交易時段內同步最新EF訊號"
    active_positions[event.strategy_code] = target
    return SignalDecision(
        event=event,
        execution_time=execution_bar.bar_time,
        execution_price=execution_bar.open,
        previous_position=previous,
        target_position=target,
        previous_net_position=previous_net,
        net_position=net_position(active_positions),
        phase=phase,
        reason=reason,
    )


def flatten_positions(active_positions: dict[str, int]) -> tuple[dict[str, int], int, int]:
    previous = normalized_positions(active_positions)
    target = {code: 0 for code in ALL_STRATEGIES}
    return target, net_position(previous), 0


def restore_latest_positions(
    raw_positions: Mapping[str, int], active_positions: Mapping[str, int]
) -> tuple[dict[str, int], int, int]:
    previous = normalized_positions(active_positions)
    target = normalized_positions(raw_positions)
    return target, net_position(previous), net_position(target)


def _latest_at_or_before(bars: list[PriceBar], target: datetime) -> PriceBar | None:
    times = [bar.bar_time for bar in bars]
    index = bisect.bisect_right(times, target) - 1
    return None if index < 0 else bars[index]


def session_boundaries(bars: list[PriceBar]) -> list[BoundaryEvent]:
    if not bars:
        return []
    by_date: dict[date, list[PriceBar]] = {}
    for bar in bars:
        by_date.setdefault(bar.bar_time.date(), []).append(bar)

    result: list[BoundaryEvent] = []
    for calendar_date, dated_bars in sorted(by_date.items()):
        day_bars = [
            bar
            for bar in dated_bars
            if DAY_OPEN_TIME <= bar.bar_time.time() < time(13, 45)
        ]
        if day_bars:
            scheduled = datetime.combine(calendar_date, DAY_FLAT_TIME)
            source = _latest_at_or_before(day_bars, scheduled)
            if source is not None:
                result.append(
                    BoundaryEvent(
                        key=f"{calendar_date}:day_flat",
                        kind="day_flat",
                        timestamp=scheduled,
                        record_time=max(source.record_time, scheduled),
                        price=source.open,
                    )
                )

        # The webhook occasionally misses the exact 15:00 bar.  Use the first
        # actually available night-session bar rather than silently skipping
        # that day's restore.
        night_open = next(
            (
                bar
                for bar in dated_bars
                if bar.bar_time.time() >= NIGHT_OPEN_TIME
            ),
            None,
        )
        if night_open is not None and day_bars:
            result.append(
                BoundaryEvent(
                    key=f"{calendar_date}:night_restore",
                    kind="night_restore",
                    timestamp=night_open.bar_time,
                    record_time=night_open.record_time,
                    price=night_open.open,
                )
            )

        morning = [bar for bar in dated_bars if bar.bar_time.time() < time(5, 0)]
        if morning:
            scheduled = datetime.combine(calendar_date, MORNING_FLAT_TIME)
            source = _latest_at_or_before(morning, scheduled)
            if source is not None:
                result.append(
                    BoundaryEvent(
                        key=f"{calendar_date}:morning_flat",
                        kind="morning_flat",
                        timestamp=scheduled,
                        record_time=max(source.record_time, scheduled),
                        price=source.open,
                    )
                )
    return sorted(result, key=lambda event: (event.timestamp, event.kind))


def observed_day_session(bars: list[PriceBar], calendar_date: date) -> bool:
    return any(
        bar.bar_time.date() == calendar_date
        and DAY_OPEN_TIME <= bar.bar_time.time() < time(13, 45)
        for bar in bars
    )


def observed_night_session(bars: list[PriceBar], current: datetime) -> bool:
    session_date = current.date() if current.time() >= NIGHT_OPEN_TIME else current.date() - timedelta(days=1)
    return any(
        (bar.bar_time.date() == session_date and bar.bar_time.time() >= NIGHT_OPEN_TIME)
        or (
            bar.bar_time.date() == session_date + timedelta(days=1)
            and bar.bar_time.time() < time(5, 0)
        )
        for bar in bars
    )


def position_text(position: int) -> str:
    if position > 0:
        return f"多{position}口"
    if position < 0:
        return f"空{abs(position)}口"
    return "空手"
