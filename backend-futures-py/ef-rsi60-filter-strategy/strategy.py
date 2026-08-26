from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Mapping


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
RSI_PERIOD = 14
RSI_THRESHOLD = 50.0
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
class RsiSnapshot:
    bar_time: datetime
    available_time: datetime
    close: float
    rsi: float


@dataclass(frozen=True)
class FilterDecision:
    event: SignalEvent
    previous_filtered_position: int
    filtered_position: int
    previous_net_position: int
    net_position: int
    rsi: float | None
    rsi_bar_time: datetime | None
    allowed: bool | None
    reason: str


def normalize_strategy_code(value: object) -> str:
    code = str(value or "").strip()
    return STRATEGY_ALIASES.get(code, code)


def parse_position(value: object) -> int:
    position = int(float(str(value).strip()))
    if position not in {-1, 0, 1}:
        raise ValueError(f"策略倉位必須是-1、0或1，目前為{position}")
    return position


def parse_signal_row(row: Mapping[str, object], row_number: int) -> SignalEvent | None:
    code = normalize_strategy_code(row.get("strategy_code") or row.get("raw_strategy_code"))
    if code not in ALL_STRATEGIES:
        return None
    timestamp_text = str(row.get("message_time") or row.get("received_at") or "").strip()
    try:
        timestamp = datetime.strptime(timestamp_text, TIME_FORMAT)
        previous = parse_position(row.get("previous_position"))
        new = parse_position(row.get("new_position"))
    except (TypeError, ValueError):
        return None
    return SignalEvent(
        row_number=row_number,
        timestamp=timestamp,
        strategy_code=code,
        previous_position=previous,
        new_position=new,
        strategy_name=str(row.get("strategy_name") or "").strip(),
    )


def load_signal_rows(path: Path) -> tuple[list[dict[str, str]], list[SignalEvent]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    events = [
        event
        for index, row in enumerate(rows, start=1)
        if (event := parse_signal_row(row, index)) is not None
    ]
    return rows, events


def trading_session_start(timestamp: datetime) -> datetime | None:
    """Return the TradingView MXF session anchor for a one-minute bar."""
    clock = timestamp.time()
    if time(8, 45) <= clock < time(13, 45):
        return datetime.combine(timestamp.date(), time(8, 45))
    if clock >= time(15, 0):
        return datetime.combine(timestamp.date(), time(15, 0))
    if clock < time(5, 0):
        return datetime.combine(timestamp.date() - timedelta(days=1), time(15, 0))
    return None


def sixty_minute_bar_start(timestamp: datetime) -> datetime | None:
    session_start = trading_session_start(timestamp)
    if session_start is None:
        return None
    elapsed = int((timestamp - session_start).total_seconds() // 60)
    return session_start + timedelta(minutes=(elapsed // 60) * 60)


def wilder_rsi(closes: Iterable[float], period: int = RSI_PERIOD) -> list[float | None]:
    values = [float(value) for value in closes]
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    def value() -> float:
        if average_loss == 0:
            return 100.0 if average_gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + average_gain / average_loss)

    result[period] = value()
    for index in range(period, len(gains)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index + 1] = value()
    return result


def load_rsi_snapshots(path: Path, period: int = RSI_PERIOD) -> list[RsiSnapshot]:
    """Build TradingView-aligned completed 60-minute RSI snapshots from 1m data."""
    if not path.exists():
        return []
    minute_rows: dict[datetime, tuple[datetime, float]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                bar_time = datetime.strptime(str(row["TradingView Time"]).strip(), TIME_FORMAT)
                record_time = datetime.strptime(str(row["Record Time"]).strip(), TIME_FORMAT)
                close = float(str(row["Close"]).replace(",", "").strip())
            except (KeyError, TypeError, ValueError):
                continue
            minute_rows[bar_time] = (record_time, close)

    hourly: dict[datetime, tuple[datetime, datetime, float]] = {}
    for bar_time, (record_time, close) in sorted(minute_rows.items()):
        hour_start = sixty_minute_bar_start(bar_time)
        if hour_start is None:
            continue
        current = hourly.get(hour_start)
        if current is None or bar_time >= current[0]:
            hourly[hour_start] = (bar_time, record_time, close)

    ordered = [
        (bar_start, value)
        for bar_start, value in sorted(hourly.items())
        if value[0] >= bar_start + timedelta(minutes=59)
    ]
    rsi_values = wilder_rsi((value[2] for _, value in ordered), period)
    snapshots: list[RsiSnapshot] = []
    for (bar_start, (_, record_time, close)), rsi_value in zip(ordered, rsi_values):
        if rsi_value is None:
            continue
        snapshots.append(
            RsiSnapshot(
                bar_time=bar_start,
                available_time=max(bar_start + timedelta(hours=1), record_time),
                close=close,
                rsi=rsi_value,
            )
        )
    return snapshots


def latest_rsi_snapshot(
    snapshots: list[RsiSnapshot],
    cutoff: datetime,
) -> RsiSnapshot | None:
    available = [snapshot for snapshot in snapshots if snapshot.available_time <= cutoff]
    if not available:
        return None
    candidate = max(available, key=lambda snapshot: snapshot.bar_time)
    return candidate if rsi_snapshot_is_fresh(candidate, cutoff) else None


def rsi_snapshot_is_fresh(
    snapshot: RsiSnapshot,
    cutoff: datetime,
    *,
    boundary_grace: timedelta = timedelta(minutes=2),
) -> bool:
    """Reject stale RSI when the live 1m webhook missed an expected hour."""
    session_start = trading_session_start(cutoff)
    current_bar = sixty_minute_bar_start(cutoff)
    if session_start is None or current_bar is None:
        return False

    def is_previous_session_close() -> bool:
        if not timedelta(0) < session_start - snapshot.bar_time <= timedelta(days=4):
            return False
        expected_clock = time(4, 0) if session_start.time() == time(8, 45) else time(12, 45)
        return snapshot.bar_time.time() == expected_clock

    if current_bar == session_start:
        return is_previous_session_close()

    expected_bar = current_bar - timedelta(hours=1)
    if snapshot.bar_time == expected_bar:
        return True
    if cutoff - current_bar > boundary_grace:
        return False
    if current_bar == session_start + timedelta(hours=1):
        return is_previous_session_close()
    return snapshot.bar_time == expected_bar - timedelta(hours=1)


def direction_is_allowed(
    position: int,
    rsi: float,
    threshold: float = RSI_THRESHOLD,
) -> bool:
    if not 0 < threshold < 100:
        raise ValueError(f"RSI門檻必須介於0與100之間，目前為{threshold}")
    if position == 1:
        return rsi >= threshold
    if position == -1:
        return rsi <= threshold
    raise ValueError("只有新多單或新空單能檢查RSI方向")


def net_position(filtered_positions: Mapping[str, int]) -> int:
    return sum(int(filtered_positions.get(code, 0)) for code in ALL_STRATEGIES)


def apply_event(
    filtered_positions: dict[str, int],
    event: SignalEvent,
    snapshot: RsiSnapshot | None,
    *,
    threshold: float = RSI_THRESHOLD,
) -> FilterDecision:
    previous_filtered = int(filtered_positions.get(event.strategy_code, 0))
    previous_net = net_position(filtered_positions)
    is_new_direction = event.new_position in {-1, 1} and (
        event.previous_position != event.new_position
    )

    allowed: bool | None
    if event.new_position == 0:
        filtered = 0
        allowed = None
        reason = "原策略出場，無條件關閉此策略的影子部位"
    elif not is_new_direction:
        filtered = previous_filtered
        allowed = None
        reason = "不是新方向進場，維持既有影子部位"
    elif snapshot is None:
        filtered = 0
        allowed = False
        reason = "沒有已完成且已記錄的60分RSI14，阻擋新進場"
    else:
        allowed = direction_is_allowed(event.new_position, snapshot.rsi, threshold)
        filtered = event.new_position if allowed else 0
        comparison = "≥" if event.new_position > 0 else "≤"
        outcome = "允許" if allowed else "阻擋"
        reason = f"60分RSI14={snapshot.rsi:.2f}，需{comparison}{threshold:g}，{outcome}新進場"

    filtered_positions[event.strategy_code] = filtered
    return FilterDecision(
        event=event,
        previous_filtered_position=previous_filtered,
        filtered_position=filtered,
        previous_net_position=previous_net,
        net_position=net_position(filtered_positions),
        rsi=None if snapshot is None else snapshot.rsi,
        rsi_bar_time=None if snapshot is None else snapshot.bar_time,
        allowed=allowed,
        reason=reason,
    )


def replay_events(
    events: Iterable[SignalEvent],
    snapshots: list[RsiSnapshot],
    *,
    threshold: float = RSI_THRESHOLD,
) -> tuple[dict[str, int], list[FilterDecision]]:
    positions = {code: 0 for code in ALL_STRATEGIES}
    decisions: list[FilterDecision] = []
    for event in sorted(events, key=lambda value: (value.timestamp, value.row_number)):
        snapshot = latest_rsi_snapshot(snapshots, event.timestamp)
        decisions.append(apply_event(positions, event, snapshot, threshold=threshold))
    return positions, decisions


def load_recorded_prices(path: Path) -> tuple[list[datetime], list[float]]:
    values: dict[datetime, float] = {}
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                recorded_at = datetime.strptime(str(row["Record Time"]).strip(), TIME_FORMAT)
                close = float(str(row["Close"]).replace(",", "").strip())
            except (KeyError, TypeError, ValueError):
                continue
            values[recorded_at] = close
    ordered = sorted(values.items())
    return [value[0] for value in ordered], [value[1] for value in ordered]


def latest_recorded_price(
    price_times: list[datetime],
    prices: list[float],
    cutoff: datetime,
    *,
    tolerance: timedelta = timedelta(minutes=5),
) -> float | None:
    index = bisect.bisect_right(price_times, cutoff) - 1
    if index < 0 or cutoff - price_times[index] > tolerance:
        return None
    return prices[index]


def position_text(position: int) -> str:
    if position > 0:
        return f"多{position}口"
    if position < 0:
        return f"空{abs(position)}口"
    return "空手"
