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
class ConsensusDecision:
    event: SignalEvent
    execution_time: datetime
    execution_price: float
    e_net: int
    f_net: int
    previous_position: int
    target_position: int
    threshold: int
    relation: str
    reason: str
    e_positions: tuple[tuple[str, int], ...] = ()
    f_positions: tuple[tuple[str, int], ...] = ()


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
    code, new_position = parsed
    # Local receipt is the actionable time. Embedded message_time is not used
    # when received_at is absent because an untimed row cannot be replayed.
    received_at = str(row.get("received_at") or "").strip()
    if not received_at:
        return None
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
    for _, session_bars in sorted(night_sessions.items()):
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


def latest_morning_boundary(bars: list[PriceBar], cutoff: datetime) -> PriceBar | None:
    candidates = morning_boundaries(bars, through=cutoff)
    return candidates[-1] if candidates else None


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


def validate_threshold(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 6:
        raise ValueError(f"強共識門檻必須是1到6的整數，目前為{value!r}")
    return value


def consensus_target(
    positions: Mapping[str, int], threshold: int = 2
) -> tuple[int, int, int, str]:
    threshold = validate_threshold(threshold)
    normalized = normalized_positions(positions)
    e_net = sum(normalized[code] for code in PORTFOLIO_E)
    f_net = sum(normalized[code] for code in PORTFOLIO_F)
    if e_net >= threshold and f_net >= threshold:
        return 1, e_net, f_net, "strong_bull"
    if e_net <= -threshold and f_net <= -threshold:
        return -1, e_net, f_net, "strong_bear"
    return 0, e_net, f_net, "no_strong_consensus"


def signal_is_in_morning_block(event_time: datetime, execution_time: datetime) -> bool:
    return (
        MORNING_FLAT_TIME <= event_time.time() < DAY_REOPEN_TIME
        or MORNING_FLAT_TIME <= execution_time.time() < DAY_REOPEN_TIME
    )


def evaluate_event(
    positions: dict[str, int],
    current_position: int,
    event: SignalEvent,
    execution_bar: PriceBar,
    *,
    threshold: int = 2,
) -> ConsensusDecision:
    positions[event.strategy_code] = event.new_position
    target, e_net, f_net, relation = consensus_target(positions, threshold)
    if signal_is_in_morning_block(event.timestamp, execution_bar.bar_time):
        target = 0
        relation = "morning_block"
        reason = "04:59～08:45為早晨風控區間，不建立組合部位"
    elif relation == "strong_bull":
        reason = f"E淨部位{e_net}、F淨部位{f_net}，兩組皆達多方門檻{threshold}"
    elif relation == "strong_bear":
        reason = f"E淨部位{e_net}、F淨部位{f_net}，兩組皆達空方門檻-{threshold}"
    else:
        reason = f"E淨部位{e_net}、F淨部位{f_net}，未形成雙組同向強共識"
    return ConsensusDecision(
        event=event,
        execution_time=execution_bar.bar_time,
        execution_price=execution_bar.open,
        e_net=e_net,
        f_net=f_net,
        previous_position=current_position,
        target_position=target,
        threshold=threshold,
        relation=relation,
        reason=reason,
        e_positions=tuple((code, positions[code]) for code in PORTFOLIO_E),
        f_positions=tuple((code, positions[code]) for code in PORTFOLIO_F),
    )


def position_text(position: int) -> str:
    if position > 0:
        return f"多{position}口"
    if position < 0:
        return f"空{abs(position)}口"
    return "空手"
