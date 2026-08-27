from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Mapping


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
MORNING_FLAT_TIME = time(4, 59)
DAY_REOPEN_TIME = time(8, 45)
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
class ShadowDecision:
    event: SignalEvent
    execution_time: datetime
    execution_price: float
    previous_shadow_position: int
    shadow_position: int
    previous_net_position: int
    net_position: int
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


def parse_signal_row(row: Mapping[str, object], row_number: int) -> SignalEvent | None:
    code = normalize_strategy_code(row.get("strategy_code") or row.get("raw_strategy_code"))
    if code not in ALL_STRATEGIES:
        return None
    # Use the actionable local receipt time.  message_time may predate local
    # receipt when Telegram delivery is delayed.
    timestamp_text = row.get("received_at") or row.get("message_time") or ""
    try:
        return SignalEvent(
            row_number=row_number,
            timestamp=parse_time(timestamp_text),
            strategy_code=code,
            previous_position=parse_position(row.get("previous_position")),
            new_position=parse_position(row.get("new_position")),
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
            values[bar.bar_time] = bar
    return [values[key] for key in sorted(values)]


def next_minute_open(bars: list[PriceBar], timestamp: datetime) -> PriceBar | None:
    if not bars:
        return None
    target = timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
    times = [bar.bar_time for bar in bars]
    index = bisect.bisect_left(times, target)
    return None if index >= len(bars) else bars[index]


def exact_bar(bars: list[PriceBar], timestamp: datetime) -> PriceBar | None:
    times = [bar.bar_time for bar in bars]
    index = bisect.bisect_left(times, timestamp)
    if index >= len(bars) or bars[index].bar_time != timestamp:
        return None
    return bars[index]


def morning_boundaries(
    bars: list[PriceBar],
    *,
    after: datetime | None = None,
    through: datetime | None = None,
) -> list[PriceBar]:
    night_sessions: dict[datetime.date, list[PriceBar]] = {}
    for bar in bars:
        clock = bar.bar_time.time()
        if clock >= time(15, 0):
            session_date = bar.bar_time.date()
        elif clock < time(5, 0):
            session_date = (bar.bar_time - timedelta(days=1)).date()
        else:
            continue
        night_sessions.setdefault(session_date, []).append(bar)

    result: list[PriceBar] = []
    for session_date, session_bars in sorted(night_sessions.items()):
        morning = [bar for bar in session_bars if bar.bar_time.time() < time(5, 0)]
        if not morning:
            continue
        source = morning[-1]
        scheduled_time = datetime.combine(source.bar_time.date(), MORNING_FLAT_TIME)
        if source.bar_time > scheduled_time:
            continue
        boundary = PriceBar(
            bar_time=scheduled_time,
            record_time=max(source.record_time, scheduled_time),
            open=source.open,
            close=source.close,
        )
        if after is not None and boundary.bar_time <= after:
            continue
        if through is not None and boundary.record_time > through:
            continue
        result.append(boundary)
    return result


def latest_morning_boundary(
    bars: list[PriceBar], cutoff: datetime
) -> PriceBar | None:
    candidates = morning_boundaries(bars, through=cutoff)
    return candidates[-1] if candidates else None


def execution_is_in_morning_block(execution_time: datetime) -> bool:
    return MORNING_FLAT_TIME <= execution_time.time() < DAY_REOPEN_TIME


def signal_is_in_morning_block(event_time: datetime, execution_time: datetime) -> bool:
    return (
        MORNING_FLAT_TIME <= event_time.time() < DAY_REOPEN_TIME
        or execution_is_in_morning_block(execution_time)
    )


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


def apply_signal(
    shadow_positions: dict[str, int],
    event: SignalEvent,
    execution_bar: PriceBar,
) -> ShadowDecision:
    previous = int(shadow_positions.get(event.strategy_code, 0))
    previous_net = net_position(shadow_positions)
    if signal_is_in_morning_block(event.timestamp, execution_bar.bar_time):
        target = 0
        reason = "04:59已進入早晨休盤風控，不建立影子部位"
    elif event.new_position == 0:
        target = 0
        reason = "原EF策略出場，同步關閉影子部位"
    else:
        target = event.new_position
        reason = "08:45後收到新進場／反轉訊號，建立影子部位"
    shadow_positions[event.strategy_code] = target
    return ShadowDecision(
        event=event,
        execution_time=execution_bar.bar_time,
        execution_price=execution_bar.open,
        previous_shadow_position=previous,
        shadow_position=target,
        previous_net_position=previous_net,
        net_position=net_position(shadow_positions),
        reason=reason,
    )


def position_text(position: int) -> str:
    if position > 0:
        return f"多{position}口"
    if position < 0:
        return f"空{abs(position)}口"
    return "空手"
