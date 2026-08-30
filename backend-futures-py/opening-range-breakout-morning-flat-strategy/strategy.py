from __future__ import annotations

import bisect
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DAY_OPEN = time(8, 45)
DAY_SESSION_END = time(13, 45)
NIGHT_OPEN = time(15, 0)


@dataclass(frozen=True)
class PriceBar:
    bar_time: datetime
    record_time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class DailyBar:
    trading_day: date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Config:
    opening_minutes: int = 30
    daily_sma_length: int = 20
    retest_points: float = 50.0
    retest_expiry_minutes: int = 30
    limit_penetration_points: float = 1.0
    stop_points: float = 100.0
    target_points: float = 100.0
    force_flat_time: time = time(11, 0)
    minimum_opening_bars: int = 28

    def validate(self) -> None:
        if self.opening_minutes < 5:
            raise ValueError("opening_minutes至少要5")
        if self.daily_sma_length < 2:
            raise ValueError("daily_sma_length至少要2")
        if (
            self.retest_points < 0
            or self.limit_penetration_points < 0
            or self.stop_points <= 0
            or self.target_points <= 0
        ):
            raise ValueError("回踩、停損與停利點數必須有效")
        if self.retest_expiry_minutes < 1:
            raise ValueError("retest_expiry_minutes至少要1")
        if not 1 <= self.minimum_opening_bars <= self.opening_minutes:
            raise ValueError("minimum_opening_bars必須介於1與opening_minutes")


@dataclass(frozen=True)
class Trade:
    trading_day: date
    side: int
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    opening_high: float
    opening_low: float
    daily_close: float
    daily_sma: float
    gross_points: float
    reason: str


@dataclass(frozen=True)
class PendingRetest:
    side: int
    signal_time: datetime
    active_from: datetime
    expiry: datetime
    limit_price: float
    opening_high: float
    opening_low: float


@dataclass(frozen=True)
class Action:
    timestamp: datetime
    action: str
    side: int
    price: float
    reason: str
    pnl_points: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    signal_time: datetime | None = None


@dataclass(frozen=True)
class Decision:
    timestamp: datetime
    kind: str
    side: int
    accepted: bool
    reason: str
    opening_high: float | None = None
    opening_low: float | None = None
    daily_close: float | None = None
    daily_sma: float | None = None
    retest_limit: float | None = None


@dataclass
class EngineState:
    position: int = 0
    entry_price: float | None = None
    entry_time: datetime | None = None
    stop_price: float | None = None
    target_price: float | None = None
    pending: PendingRetest | None = None
    session_date: date | None = None
    opening_high: float | None = None
    opening_low: float | None = None
    opening_bars: int = 0
    previous_close: float | None = None
    signal_used_today: bool = False
    daily_bias: int = 0
    daily_close: float | None = None
    daily_sma: float | None = None
    last_bar_time: datetime | None = None


def parse_time(value: object) -> datetime:
    return datetime.strptime(str(value or "").strip(), TIME_FORMAT)


def _number(value: object) -> float:
    return float(str(value or "").replace(",", "").strip())


def load_price_bars(path: Path) -> list[PriceBar]:
    """Load the latest recorded version of each completed TradingView minute."""

    if not path.exists():
        return []
    values: dict[datetime, PriceBar] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                bar = PriceBar(
                    bar_time=parse_time(row["TradingView Time"]),
                    record_time=parse_time(row["Record Time"]),
                    open=_number(row["Open"]),
                    high=_number(row["High"]),
                    low=_number(row["Low"]),
                    close=_number(row["Close"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            current = values.get(bar.bar_time)
            if current is None or bar.record_time >= current.record_time:
                values[bar.bar_time] = bar
    return [values[key] for key in sorted(values)]


def trading_day_for(timestamp: datetime) -> date:
    """TAIFEX night-session rows belong to the following trading day."""

    trading_day = timestamp.date()
    if timestamp.time() >= NIGHT_OPEN:
        trading_day += timedelta(days=1)
    while trading_day.weekday() >= 5:
        trading_day += timedelta(days=1)
    return trading_day


def daily_bars_from_minutes(bars: Iterable[PriceBar]) -> list[DailyBar]:
    grouped: dict[date, list[PriceBar]] = defaultdict(list)
    for bar in bars:
        grouped[trading_day_for(bar.bar_time)].append(bar)
    result: list[DailyBar] = []
    for trading_day in sorted(grouped):
        rows = sorted(grouped[trading_day], key=lambda value: value.bar_time)
        result.append(
            DailyBar(
                trading_day=trading_day,
                open=rows[0].open,
                high=max(row.high for row in rows),
                low=min(row.low for row in rows),
                close=rows[-1].close,
            )
        )
    return result


def load_hourly_daily_bars(path: Path) -> list[DailyBar]:
    """Load the archived TradingView hourly export as daily-regime warm-up."""

    if not path.exists():
        return []
    synthetic: list[PriceBar] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                timestamp = parse_time(row["datetime"])
                synthetic.append(
                    PriceBar(
                        bar_time=timestamp,
                        record_time=timestamp + timedelta(hours=1),
                        open=_number(row["open"]),
                        high=_number(row["high"]),
                        low=_number(row["low"]),
                        close=_number(row["close"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return daily_bars_from_minutes(synthetic)


def merge_daily_history(
    archived: Iterable[DailyBar], minute_bars: Iterable[PriceBar]
) -> list[DailyBar]:
    """Use complete minute days in preference to overlapping hourly warm-up days."""

    archived_rows = list(archived)
    minute_values = list(minute_bars)
    minute_rows = daily_bars_from_minutes(minute_values)
    if not archived_rows:
        return minute_rows
    cutoff = max(row.trading_day for row in archived_rows)
    values = {row.trading_day: row for row in archived_rows}
    grouped: dict[date, list[PriceBar]] = defaultdict(list)
    for bar in minute_values:
        grouped[trading_day_for(bar.bar_time)].append(bar)
    latest_minute_day = max(grouped, default=cutoff)
    for row in minute_rows:
        day_rows = grouped[row.trading_day]
        day_session = [
            value
            for value in day_rows
            if DAY_OPEN <= value.bar_time.time() < DAY_SESSION_END
        ]
        complete_day = (
            len(day_session) >= 250
            and min(value.bar_time.time() for value in day_session) <= time(8, 50)
            and max(value.bar_time.time() for value in day_session) >= time(13, 40)
        )
        if complete_day or row.trading_day > cutoff or row.trading_day == latest_minute_day:
            values[row.trading_day] = row
    return [values[key] for key in sorted(values)]


def daily_regimes(
    daily_bars: Iterable[DailyBar], length: int
) -> dict[date, tuple[int, float, float]]:
    """Return each day's bias using only the previous completed daily bar."""

    rows = sorted(daily_bars, key=lambda value: value.trading_day)
    result: dict[date, tuple[int, float, float]] = {}
    for index in range(length, len(rows)):
        history = rows[index - length : index]
        previous_close = history[-1].close
        average = sum(row.close for row in history) / length
        bias = 1 if previous_close > average else -1 if previous_close < average else 0
        result[rows[index].trading_day] = (bias, previous_close, average)
    return result


def delayed_execution_index(
    bars: list[PriceBar], bar_times: list[datetime], signal: PriceBar
) -> int | None:
    """Conservative fill: the first bar one full minute after local record time."""

    execution_time = signal.record_time.replace(second=0, microsecond=0) + timedelta(
        minutes=1
    )
    index = bisect.bisect_left(bar_times, execution_time)
    return None if index >= len(bars) else index


def find_retest_entry(
    bars: list[PriceBar],
    *,
    start_index: int,
    side: int,
    limit_price: float,
    penetration_points: float,
    expiry: datetime,
) -> tuple[int, float] | None:
    """Find a conservative limit fill after the breakout signal is actionable."""

    if side not in {-1, 1}:
        raise ValueError("side必須是-1或1")
    trading_day = expiry.date()
    for index in range(start_index, len(bars)):
        bar = bars[index]
        if bar.bar_time.date() != trading_day or bar.bar_time >= expiry:
            break
        trigger_price = limit_price - side * penetration_points
        limit_hit = bar.low <= trigger_price if side > 0 else bar.high >= trigger_price
        if not limit_hit:
            continue
        fill_price = (
            min(limit_price, bar.open)
            if side > 0
            else max(limit_price, bar.open)
        )
        return index, fill_price
    return None


def session_bars(bars: Iterable[PriceBar]) -> dict[date, list[PriceBar]]:
    grouped: dict[date, list[PriceBar]] = defaultdict(list)
    for bar in bars:
        if DAY_OPEN <= bar.bar_time.time() < DAY_SESSION_END:
            grouped[bar.bar_time.date()].append(bar)
    for rows in grouped.values():
        rows.sort(key=lambda value: value.bar_time)
    return grouped


def opening_range(rows: Iterable[PriceBar], config: Config) -> tuple[float, float] | None:
    values = list(rows)
    if not values:
        return None
    day = values[0].bar_time.date()
    opening_end = (
        datetime.combine(day, DAY_OPEN) + timedelta(minutes=config.opening_minutes)
    ).time()
    opening = [row for row in values if DAY_OPEN <= row.bar_time.time() < opening_end]
    if len(opening) < config.minimum_opening_bars:
        return None
    return max(row.high for row in opening), min(row.low for row in opening)


def find_breakout_signal(
    rows: Iterable[PriceBar],
    *,
    side: int,
    opening_high: float,
    opening_low: float,
    config: Config,
) -> PriceBar | None:
    values = list(rows)
    if not values or side not in {-1, 1}:
        return None
    day = values[0].bar_time.date()
    opening_end = (
        datetime.combine(day, DAY_OPEN) + timedelta(minutes=config.opening_minutes)
    ).time()
    previous: PriceBar | None = None
    for row in values:
        if row.bar_time.time() < opening_end:
            previous = row
            continue
        if row.bar_time.time() >= config.force_flat_time:
            break
        if previous is None:
            previous = row
            continue
        if side > 0 and row.close > opening_high and previous.close <= opening_high:
            return row
        if side < 0 and row.close < opening_low and previous.close >= opening_low:
            return row
        previous = row
    return None


def text_time(value: datetime | None) -> str:
    return "" if value is None else value.strftime(TIME_FORMAT)


def _optional_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    return parse_time(text) if text else None


def pending_to_dict(value: PendingRetest | None) -> dict | None:
    if value is None:
        return None
    return {
        "side": value.side,
        "signal_time": text_time(value.signal_time),
        "active_from": text_time(value.active_from),
        "expiry": text_time(value.expiry),
        "limit_price": value.limit_price,
        "opening_high": value.opening_high,
        "opening_low": value.opening_low,
    }


def pending_from_dict(value: object) -> PendingRetest | None:
    if not isinstance(value, dict):
        return None
    try:
        return PendingRetest(
            side=int(value["side"]),
            signal_time=parse_time(value["signal_time"]),
            active_from=parse_time(value["active_from"]),
            expiry=parse_time(value["expiry"]),
            limit_price=float(value["limit_price"]),
            opening_high=float(value["opening_high"]),
            opening_low=float(value["opening_low"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def state_to_dict(state: EngineState) -> dict:
    return {
        "position": state.position,
        "entry_price": state.entry_price,
        "entry_time": text_time(state.entry_time),
        "stop_price": state.stop_price,
        "target_price": state.target_price,
        "pending": pending_to_dict(state.pending),
        "session_date": "" if state.session_date is None else state.session_date.isoformat(),
        "opening_high": state.opening_high,
        "opening_low": state.opening_low,
        "opening_bars": state.opening_bars,
        "previous_close": state.previous_close,
        "signal_used_today": state.signal_used_today,
        "daily_bias": state.daily_bias,
        "daily_close": state.daily_close,
        "daily_sma": state.daily_sma,
        "last_bar_time": text_time(state.last_bar_time),
    }


def state_from_dict(value: object) -> EngineState:
    if not isinstance(value, dict):
        return EngineState()
    session_text = str(value.get("session_date") or "").strip()
    try:
        session_date = date.fromisoformat(session_text) if session_text else None
    except ValueError:
        session_date = None
    return EngineState(
        position=int(value.get("position") or 0),
        entry_price=(
            None if value.get("entry_price") is None else float(value["entry_price"])
        ),
        entry_time=_optional_time(value.get("entry_time")),
        stop_price=(
            None if value.get("stop_price") is None else float(value["stop_price"])
        ),
        target_price=(
            None if value.get("target_price") is None else float(value["target_price"])
        ),
        pending=pending_from_dict(value.get("pending")),
        session_date=session_date,
        opening_high=(
            None if value.get("opening_high") is None else float(value["opening_high"])
        ),
        opening_low=(
            None if value.get("opening_low") is None else float(value["opening_low"])
        ),
        opening_bars=int(value.get("opening_bars") or 0),
        previous_close=(
            None if value.get("previous_close") is None else float(value["previous_close"])
        ),
        signal_used_today=bool(value.get("signal_used_today", False)),
        daily_bias=int(value.get("daily_bias") or 0),
        daily_close=(
            None if value.get("daily_close") is None else float(value["daily_close"])
        ),
        daily_sma=(
            None if value.get("daily_sma") is None else float(value["daily_sma"])
        ),
        last_bar_time=_optional_time(value.get("last_bar_time")),
    )


class BreakRetestEngine:
    """Replayable day-session state machine used by the monitor."""

    def __init__(self, config: Config, state: EngineState | None = None):
        config.validate()
        self.config = config
        self.state = state or EngineState()

    def _reset_day(
        self,
        bar: PriceBar,
        regime: tuple[int, float, float] | None,
    ) -> list[Action]:
        actions: list[Action] = []
        if self.state.position and self.state.entry_price is not None:
            pnl = (bar.open - self.state.entry_price) * self.state.position
            actions.append(
                Action(
                    bar.bar_time,
                    "exit",
                    self.state.position,
                    bar.open,
                    "session_roll_flat",
                    pnl,
                )
            )
        self.state.position = 0
        self.state.entry_price = None
        self.state.entry_time = None
        self.state.stop_price = None
        self.state.target_price = None
        self.state.pending = None
        self.state.session_date = bar.bar_time.date()
        self.state.opening_high = None
        self.state.opening_low = None
        self.state.opening_bars = 0
        self.state.previous_close = None
        self.state.signal_used_today = False
        if regime is None:
            self.state.daily_bias = 0
            self.state.daily_close = None
            self.state.daily_sma = None
        else:
            self.state.daily_bias, self.state.daily_close, self.state.daily_sma = regime
        return actions

    def _flat_action(self, bar: PriceBar, reason: str) -> Action | None:
        if not self.state.position or self.state.entry_price is None:
            return None
        side = self.state.position
        pnl = (bar.open - self.state.entry_price) * side
        action = Action(bar.bar_time, "exit", side, bar.open, reason, pnl)
        self.state.position = 0
        self.state.entry_price = None
        self.state.entry_time = None
        self.state.stop_price = None
        self.state.target_price = None
        return action

    def _position_exit(self, bar: PriceBar) -> Action | None:
        state = self.state
        if (
            state.position == 0
            or state.entry_price is None
            or state.stop_price is None
            or state.target_price is None
        ):
            return None
        side = state.position
        stop_hit = bar.low <= state.stop_price if side > 0 else bar.high >= state.stop_price
        target_hit = bar.high >= state.target_price if side > 0 else bar.low <= state.target_price
        if stop_hit:
            price = state.stop_price
            reason = "stop"
        elif target_hit:
            price = state.target_price
            reason = "target"
        else:
            return None
        pnl = (price - state.entry_price) * side
        action = Action(bar.bar_time, "exit", side, price, reason, pnl)
        state.position = 0
        state.entry_price = None
        state.entry_time = None
        state.stop_price = None
        state.target_price = None
        return action

    def process_bar(
        self,
        bar: PriceBar,
        regime: tuple[int, float, float] | None,
    ) -> tuple[list[Action], list[Decision]]:
        state = self.state
        actions: list[Action] = []
        decisions: list[Decision] = []
        if state.last_bar_time is not None and bar.bar_time <= state.last_bar_time:
            return actions, decisions
        bar_clock = bar.bar_time.time()
        in_day_session = DAY_OPEN <= bar_clock < DAY_SESSION_END
        if not in_day_session:
            state.last_bar_time = bar.bar_time
            return actions, decisions
        if state.session_date != bar.bar_time.date():
            actions.extend(self._reset_day(bar, regime))

        state.daily_bias = 0 if regime is None else regime[0]
        state.daily_close = None if regime is None else regime[1]
        state.daily_sma = None if regime is None else regime[2]

        if bar_clock >= self.config.force_flat_time:
            action = self._flat_action(bar, "11:00_flat")
            if action is not None:
                actions.append(action)
            if state.pending is not None:
                decisions.append(
                    Decision(
                        bar.bar_time,
                        "retest_expired",
                        state.pending.side,
                        False,
                        "11:00前未回踩，取消限價",
                        state.pending.opening_high,
                        state.pending.opening_low,
                        state.daily_close,
                        state.daily_sma,
                        state.pending.limit_price,
                    )
                )
                state.pending = None
            state.previous_close = bar.close
            state.last_bar_time = bar.bar_time
            return actions, decisions

        opening_end = (
            datetime.combine(bar.bar_time.date(), DAY_OPEN)
            + timedelta(minutes=self.config.opening_minutes)
        ).time()
        if bar_clock < opening_end:
            state.opening_high = (
                bar.high if state.opening_high is None else max(state.opening_high, bar.high)
            )
            state.opening_low = (
                bar.low if state.opening_low is None else min(state.opening_low, bar.low)
            )
            state.opening_bars += 1
            state.previous_close = bar.close
            state.last_bar_time = bar.bar_time
            return actions, decisions

        existing_exit = self._position_exit(bar)
        if existing_exit is not None:
            actions.append(existing_exit)

        pending = state.pending
        if pending is not None and state.position == 0:
            if bar.bar_time >= pending.expiry:
                decisions.append(
                    Decision(
                        bar.bar_time,
                        "retest_expired",
                        pending.side,
                        False,
                        "突破後30分鐘未回踩，取消限價",
                        pending.opening_high,
                        pending.opening_low,
                        state.daily_close,
                        state.daily_sma,
                        pending.limit_price,
                    )
                )
                state.pending = None
            elif bar.bar_time >= pending.active_from:
                trigger = pending.limit_price - pending.side * self.config.limit_penetration_points
                hit = bar.low <= trigger if pending.side > 0 else bar.high >= trigger
                if hit:
                    fill_price = (
                        min(pending.limit_price, bar.open)
                        if pending.side > 0
                        else max(pending.limit_price, bar.open)
                    )
                    state.position = pending.side
                    state.entry_price = fill_price
                    state.entry_time = bar.bar_time
                    state.stop_price = fill_price - pending.side * self.config.stop_points
                    state.target_price = fill_price + pending.side * self.config.target_points
                    state.pending = None
                    actions.append(
                        Action(
                            bar.bar_time,
                            "enter",
                            pending.side,
                            fill_price,
                            "opening_break_retest",
                            target_price=state.target_price,
                            stop_price=state.stop_price,
                            signal_time=pending.signal_time,
                        )
                    )
                    decisions.append(
                        Decision(
                            bar.bar_time,
                            "retest_filled",
                            pending.side,
                            True,
                            "限價至少穿過設定點數，影子成交",
                            pending.opening_high,
                            pending.opening_low,
                            state.daily_close,
                            state.daily_sma,
                            pending.limit_price,
                        )
                    )
                    # The entry bar may have reached the target before the limit
                    # fill. Only an adverse stop is safe to credit on this bar.
                    stop_hit = (
                        bar.low <= state.stop_price
                        if pending.side > 0
                        else bar.high >= state.stop_price
                    )
                    if stop_hit:
                        price = state.stop_price
                        pnl = (price - fill_price) * pending.side
                        actions.append(
                            Action(
                                bar.bar_time,
                                "exit",
                                pending.side,
                                price,
                                "entry_bar_stop",
                                pnl,
                            )
                        )
                        state.position = 0
                        state.entry_price = None
                        state.entry_time = None
                        state.stop_price = None
                        state.target_price = None

        if (
            state.position == 0
            and state.pending is None
            and not state.signal_used_today
            and state.opening_bars >= self.config.minimum_opening_bars
            and state.opening_high is not None
            and state.opening_low is not None
            and state.previous_close is not None
            and state.daily_bias in {-1, 1}
        ):
            side = state.daily_bias
            crossed = (
                bar.close > state.opening_high and state.previous_close <= state.opening_high
                if side > 0
                else bar.close < state.opening_low and state.previous_close >= state.opening_low
            )
            if crossed:
                edge = state.opening_high if side > 0 else state.opening_low
                limit_price = edge - side * self.config.retest_points
                active_from = bar.record_time.replace(second=0, microsecond=0) + timedelta(
                    minutes=1
                )
                expiry = min(
                    datetime.combine(bar.bar_time.date(), self.config.force_flat_time),
                    bar.bar_time + timedelta(minutes=self.config.retest_expiry_minutes),
                )
                state.pending = PendingRetest(
                    side,
                    bar.bar_time,
                    active_from,
                    expiry,
                    limit_price,
                    state.opening_high,
                    state.opening_low,
                )
                state.signal_used_today = True
                decisions.append(
                    Decision(
                        bar.bar_time,
                        "breakout_pending",
                        side,
                        True,
                        "日線方向一致，突破成立並等待回踩",
                        state.opening_high,
                        state.opening_low,
                        state.daily_close,
                        state.daily_sma,
                        limit_price,
                    )
                )

        state.previous_close = bar.close
        state.last_bar_time = bar.bar_time
        return actions, decisions
