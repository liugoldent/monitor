from __future__ import annotations

import bisect
import csv
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Mapping


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ENTRY_START = time(8, 50)
LAST_ENTRY_TIME = time(13, 19)
FORCE_FLAT_TIME = time(13, 20)
DAY_SESSION_END = time(13, 45)

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
ALL_EF_STRATEGIES = PORTFOLIO_E + PORTFOLIO_F
STRATEGY_ALIASES = {"CFCWN01m": "CFCWIN01m"}


@dataclass(frozen=True)
class PriceBar:
    bar_time: datetime
    record_time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class RegressionChannel:
    center: float
    upper: float
    lower: float
    slope: float
    residual_std: float


@dataclass(frozen=True)
class RiskContext:
    h_position: int = 0
    ef_target: int = 0
    e_net: int = 0
    f_net: int = 0
    relation: str = "neutral_or_unknown"

    @property
    def aligned_bias(self) -> int:
        if self.h_position > 0 and self.ef_target > 0:
            return 1
        if self.h_position < 0 and self.ef_target < 0:
            return -1
        return 0


@dataclass(frozen=True)
class Config:
    regression_length: int = 60
    channel_width: float = 2.0
    stop_points: float = 100.0
    max_abs_slope: float = 2.5
    volatility_lookback: int = 30
    abnormal_range_multiple: float = 3.0
    min_reward_risk: float = 1.2
    gap_points: float = 100.0
    gap_expansion_points: float = 50.0
    ef_threshold: int = 2
    max_entries_per_day: int = 4

    def validate(self) -> None:
        if self.regression_length < 3:
            raise ValueError("regression_length至少要3")
        if self.channel_width <= 0 or self.stop_points <= 0:
            raise ValueError("通道寬度與停損必須大於0")
        if self.volatility_lookback < 3:
            raise ValueError("volatility_lookback至少要3")
        if not 1 <= self.ef_threshold <= 6:
            raise ValueError("ef_threshold必須是1到6")
        if self.max_entries_per_day < 1:
            raise ValueError("max_entries_per_day至少要1")


@dataclass(frozen=True)
class PendingEntry:
    side: int
    signal_time: datetime
    execution_time: datetime
    center: float
    upper: float
    lower: float
    slope: float
    residual_std: float
    context_relation: str


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
    center: float | None = None
    upper: float | None = None
    lower: float | None = None
    slope: float | None = None
    residual_std: float | None = None
    h_position: int = 0
    ef_target: int = 0
    e_net: int = 0
    f_net: int = 0


@dataclass
class EngineState:
    position: int = 0
    entry_price: float | None = None
    entry_time: datetime | None = None
    target_price: float | None = None
    stop_price: float | None = None
    pending: PendingEntry | None = None
    session_date: date | None = None
    long_locked: bool = False
    short_locked: bool = False
    entries_today: int = 0
    day_reference_close: float | None = None
    day_open: float | None = None
    gap_direction: int = 0
    last_bar_time: datetime | None = None


def parse_time(value: object) -> datetime:
    return datetime.strptime(str(value or "").strip(), TIME_FORMAT)


def text_time(value: datetime | None) -> str:
    return "" if value is None else value.strftime(TIME_FORMAT)


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
                    high=float(str(row["High"]).replace(",", "")),
                    low=float(str(row["Low"]).replace(",", "")),
                    close=float(str(row["Close"]).replace(",", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            current = values.get(bar.bar_time)
            if current is None or bar.record_time >= current.record_time:
                values[bar.bar_time] = bar
    return [values[key] for key in sorted(values)]


def regression_channel(closes: Iterable[float], width: float) -> RegressionChannel:
    values = [float(value) for value in closes]
    if len(values) < 3:
        raise ValueError("回歸通道至少需要3筆收盤價")
    n = len(values)
    sum_x = n * (n - 1) / 2.0
    sum_x2 = n * (n - 1) * (2 * n - 1) / 6.0
    sum_y = sum(values)
    sum_xy = sum(index * value for index, value in enumerate(values))
    denominator = n * sum_x2 - sum_x * sum_x
    slope = 0.0 if denominator == 0 else (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    fitted = [intercept + slope * index for index in range(n)]
    residual_std = math.sqrt(
        sum((value - estimate) ** 2 for value, estimate in zip(values, fitted)) / n
    )
    center = fitted[-1]
    deviation = residual_std * width
    return RegressionChannel(
        center=center,
        upper=center + deviation,
        lower=center - deviation,
        slope=slope,
        residual_std=residual_std,
    )


def true_range(bar: PriceBar, previous_close: float | None) -> float:
    if previous_close is None:
        return bar.high - bar.low
    return max(
        bar.high - bar.low,
        abs(bar.high - previous_close),
        abs(bar.low - previous_close),
    )


def ef_consensus(positions: Mapping[str, int], threshold: int) -> tuple[int, int, int]:
    e_net = sum(int(positions.get(code, 0)) for code in PORTFOLIO_E)
    f_net = sum(int(positions.get(code, 0)) for code in PORTFOLIO_F)
    if e_net >= threshold and f_net >= threshold:
        return 1, e_net, f_net
    if e_net <= -threshold and f_net <= -threshold:
        return -1, e_net, f_net
    return 0, e_net, f_net


@dataclass(frozen=True)
class _ContextEvent:
    timestamp: datetime
    order: int
    source: str
    code: str
    position: int


def _load_h_events(path: Path) -> list[_ContextEvent]:
    if not path.exists():
        return []
    result: list[_ContextEvent] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for order, row in enumerate(csv.DictReader(handle)):
            try:
                timestamp = parse_time(row.get("timestamp"))
            except ValueError:
                continue
            action = str(row.get("action") or "").strip().lower()
            side = str(row.get("side") or "").strip().lower()
            if action == "enter":
                position = 1 if side == "bull" else -1 if side == "bear" else 0
            elif action in {"exit", "exiting"}:
                position = 0
            else:
                continue
            result.append(_ContextEvent(timestamp, order, "h", "H", position))
    return result


def _load_ef_events(path: Path) -> list[_ContextEvent]:
    if not path.exists():
        return []
    result: list[_ContextEvent] = []
    seen: set[tuple[object, ...]] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for order, row in enumerate(csv.DictReader(handle)):
            received_at = str(row.get("received_at") or "").strip()
            if not received_at:
                continue
            code = STRATEGY_ALIASES.get(
                str(row.get("strategy_code") or row.get("raw_strategy_code") or "").strip(),
                str(row.get("strategy_code") or row.get("raw_strategy_code") or "").strip(),
            )
            if code not in ALL_EF_STRATEGIES:
                continue
            try:
                timestamp = parse_time(received_at)
                position = int(float(str(row.get("new_position") or "")))
            except (TypeError, ValueError):
                continue
            if position not in {-1, 0, 1}:
                continue
            key = (timestamp, code, position, str(row.get("account") or ""))
            if key in seen:
                continue
            seen.add(key)
            result.append(_ContextEvent(timestamp, order, "ef", code, position))
    return result


class RiskContextTracker:
    """Point-in-time H/EF context; untimed EF rows are deliberately ignored."""

    def __init__(self, h_path: Path, ef_path: Path, *, ef_threshold: int = 2):
        self.ef_threshold = ef_threshold
        self.events = sorted(
            _load_h_events(h_path) + _load_ef_events(ef_path),
            key=lambda event: (event.timestamp, 0 if event.source == "h" else 1, event.order),
        )
        self.times = [event.timestamp for event in self.events]

    def at(self, timestamp: datetime) -> RiskContext:
        h_position = 0
        positions = {code: 0 for code in ALL_EF_STRATEGIES}
        limit = bisect.bisect_right(self.times, timestamp)
        for event in self.events[:limit]:
            if event.source == "h":
                h_position = event.position
            else:
                positions[event.code] = event.position
        target, e_net, f_net = ef_consensus(positions, self.ef_threshold)
        aligned = h_position == target and target != 0
        relation = (
            "h_ef_aligned_bull"
            if aligned and target > 0
            else "h_ef_aligned_bear"
            if aligned
            else "neutral_or_conflicting"
        )
        return RiskContext(h_position, target, e_net, f_net, relation)


class MeanReversionEngine:
    def __init__(self, config: Config | None = None, state: EngineState | None = None):
        self.config = config or Config()
        self.config.validate()
        self.state = state or EngineState()
        self.history: list[PriceBar] = []

    def warm(self, bars: Iterable[PriceBar]) -> None:
        keep = max(self.config.regression_length, self.config.volatility_lookback + 1) + 2
        self.history = list(bars)[-keep:]

    def _reset_day(self, bar: PriceBar) -> None:
        state = self.state
        if state.session_date == bar.bar_time.date():
            return
        state.session_date = bar.bar_time.date()
        state.long_locked = False
        state.short_locked = False
        state.entries_today = 0
        state.day_open = None
        state.gap_direction = 0
        prior = [candidate for candidate in self.history if candidate.bar_time < bar.bar_time]
        state.day_reference_close = prior[-1].close if prior else None

    def _update_day_open(self, bar: PriceBar) -> None:
        state = self.state
        clock = bar.bar_time.time()
        if clock < time(8, 45):
            state.day_reference_close = bar.close
            return
        if not time(8, 45) <= clock < DAY_SESSION_END or state.day_open is not None:
            return
        state.day_open = bar.open
        reference = state.day_reference_close
        if reference is None:
            return
        gap = bar.open - reference
        if abs(gap) >= self.config.gap_points:
            state.gap_direction = 1 if gap > 0 else -1

    def _exit(self, bar: PriceBar, price: float, reason: str) -> Action:
        state = self.state
        assert state.position in {-1, 1} and state.entry_price is not None
        side = state.position
        pnl = (price - state.entry_price) * side
        if reason == "fixed_stop":
            if side > 0:
                state.long_locked = True
            else:
                state.short_locked = True
        action = Action(bar.bar_time, "exit", side, price, reason, pnl)
        state.position = 0
        state.entry_price = None
        state.entry_time = None
        state.target_price = None
        state.stop_price = None
        return action

    def _execute_pending(self, bar: PriceBar) -> tuple[list[Action], list[Decision]]:
        state = self.state
        pending = state.pending
        if pending is None or state.position != 0:
            return [], []
        if bar.bar_time < pending.execution_time:
            return [], []
        state.pending = None
        clock = bar.bar_time.time()
        if not ENTRY_START <= clock <= FORCE_FLAT_TIME:
            return [], [Decision(bar.bar_time, "entry", pending.side, False, "下一根K已超出進場時段")]
        if state.entries_today >= self.config.max_entries_per_day:
            return [], [Decision(bar.bar_time, "entry", pending.side, False, "已達每日進場次數上限")]
        if (pending.side > 0 and state.long_locked) or (pending.side < 0 and state.short_locked):
            return [], [Decision(bar.bar_time, "entry", pending.side, False, "同方向當日停損後鎖定")]
        reward = (pending.center - bar.open) * pending.side
        if reward <= 0:
            return [], [Decision(bar.bar_time, "entry", pending.side, False, "開盤已回到中心，取消追價")]
        if reward / self.config.stop_points < self.config.min_reward_risk:
            return [], [Decision(bar.bar_time, "entry", pending.side, False, "中心目標距離不足")]
        state.position = pending.side
        state.entry_price = bar.open
        state.entry_time = bar.bar_time
        state.target_price = pending.center
        state.stop_price = bar.open - pending.side * self.config.stop_points
        state.entries_today += 1
        action = Action(
            bar.bar_time,
            "enter",
            pending.side,
            bar.open,
            "regression_channel_deviation",
            target_price=state.target_price,
            stop_price=state.stop_price,
            signal_time=pending.signal_time,
        )
        return [action], [Decision(bar.bar_time, "entry", pending.side, True, "下一分鐘開盤成交")]

    def _manage_position(self, bar: PriceBar) -> list[Action]:
        state = self.state
        if state.position == 0:
            return []
        assert state.target_price is not None and state.stop_price is not None
        side = state.position
        if side > 0:
            stop_at_open = bar.open <= state.stop_price
            target_at_open = bar.open >= state.target_price
            stop_hit = bar.low <= state.stop_price
            target_hit = bar.high >= state.target_price
        else:
            stop_at_open = bar.open >= state.stop_price
            target_at_open = bar.open <= state.target_price
            stop_hit = bar.high >= state.stop_price
            target_hit = bar.low <= state.target_price
        if stop_at_open:
            return [self._exit(bar, bar.open, "fixed_stop")]
        if target_at_open:
            return [self._exit(bar, bar.open, "regression_center")]
        # OHLC cannot reveal intrabar order. Use the adverse-first assumption.
        if stop_hit:
            return [self._exit(bar, state.stop_price, "fixed_stop")]
        if target_hit:
            return [self._exit(bar, state.target_price, "regression_center")]
        return []

    def _signal(self, bar: PriceBar, context: RiskContext) -> Decision | None:
        state = self.state
        clock = bar.bar_time.time()
        if state.position != 0 or state.pending is not None:
            return None
        if not ENTRY_START <= clock <= LAST_ENTRY_TIME:
            return None
        if len(self.history) < self.config.regression_length:
            return None
        channel = regression_channel(
            [item.close for item in self.history[-self.config.regression_length :]],
            self.config.channel_width,
        )
        side = 1 if bar.close <= channel.lower else -1 if bar.close >= channel.upper else 0
        if side == 0:
            return None
        base = dict(
            timestamp=bar.bar_time,
            kind="signal",
            side=side,
            center=channel.center,
            upper=channel.upper,
            lower=channel.lower,
            slope=channel.slope,
            residual_std=channel.residual_std,
            h_position=context.h_position,
            ef_target=context.ef_target,
            e_net=context.e_net,
            f_net=context.f_net,
        )
        if abs(channel.slope) > self.config.max_abs_slope:
            return Decision(**base, accepted=False, reason="回歸斜率過大")
        if (side > 0 and state.long_locked) or (side < 0 and state.short_locked):
            return Decision(**base, accepted=False, reason="同方向當日停損後鎖定")
        if state.entries_today >= self.config.max_entries_per_day:
            return Decision(**base, accepted=False, reason="已達每日進場次數上限")
        aligned = context.aligned_bias
        if aligned and side == -aligned:
            return Decision(**base, accepted=False, reason=f"H與EF高度同向：{context.relation}")
        recent = self.history[-(self.config.volatility_lookback + 1) : -1]
        ranges = [
            true_range(item, recent[index - 1].close if index else None)
            for index, item in enumerate(recent)
        ]
        baseline_range = statistics.median(ranges) if ranges else 0.0
        current_range = true_range(bar, self.history[-2].close if len(self.history) >= 2 else None)
        if baseline_range > 0 and current_range > baseline_range * self.config.abnormal_range_multiple:
            return Decision(**base, accepted=False, reason="波動突然異常放大")
        if state.gap_direction and state.day_open is not None:
            expansion = (bar.close - state.day_open) * state.gap_direction
            if expansion >= self.config.gap_expansion_points and side == -state.gap_direction:
                return Decision(**base, accepted=False, reason="開盤跳空後同方向持續擴張")
        state.pending = PendingEntry(
            side=side,
            signal_time=bar.bar_time,
            execution_time=bar.record_time.replace(second=0, microsecond=0) + timedelta(minutes=1),
            center=channel.center,
            upper=channel.upper,
            lower=channel.lower,
            slope=channel.slope,
            residual_std=channel.residual_std,
            context_relation=context.relation,
        )
        return Decision(**base, accepted=True, reason="收盤價超出回歸外通道")

    def process_bar(self, bar: PriceBar, context: RiskContext | None = None) -> tuple[list[Action], list[Decision]]:
        context = context or RiskContext()
        state = self.state
        if state.last_bar_time is not None and bar.bar_time <= state.last_bar_time:
            return [], []
        self._reset_day(bar)
        self._update_day_open(bar)
        actions: list[Action] = []
        decisions: list[Decision] = []
        clock = bar.bar_time.time()
        if FORCE_FLAT_TIME <= clock < DAY_SESSION_END:
            state.pending = None
            if state.position:
                actions.append(self._exit(bar, bar.open, "13:20_force_flat"))
        else:
            entered, entry_decisions = self._execute_pending(bar)
            actions.extend(entered)
            decisions.extend(entry_decisions)
            actions.extend(self._manage_position(bar))
        self.history.append(bar)
        keep = max(self.config.regression_length, self.config.volatility_lookback + 1) + 2
        self.history = self.history[-keep:]
        decision = self._signal(bar, context)
        if decision is not None:
            decisions.append(decision)
        state.last_bar_time = bar.bar_time
        return actions, decisions


def state_to_dict(state: EngineState) -> dict[str, object]:
    value = asdict(state)
    for key in ("entry_time", "last_bar_time"):
        value[key] = text_time(getattr(state, key))
    value["session_date"] = "" if state.session_date is None else state.session_date.isoformat()
    if state.pending is not None:
        value["pending"]["signal_time"] = text_time(state.pending.signal_time)  # type: ignore[index]
        value["pending"]["execution_time"] = text_time(state.pending.execution_time)  # type: ignore[index]
    return value


def state_from_dict(value: object) -> EngineState:
    source = value if isinstance(value, dict) else {}
    pending_value = source.get("pending")
    pending = None
    if isinstance(pending_value, dict):
        try:
            pending = PendingEntry(
                side=int(pending_value["side"]),
                signal_time=parse_time(pending_value["signal_time"]),
                execution_time=parse_time(pending_value["execution_time"]),
                center=float(pending_value["center"]),
                upper=float(pending_value["upper"]),
                lower=float(pending_value["lower"]),
                slope=float(pending_value["slope"]),
                residual_std=float(pending_value["residual_std"]),
                context_relation=str(pending_value.get("context_relation") or ""),
            )
        except (KeyError, TypeError, ValueError):
            pending = None
    def optional_time(key: str) -> datetime | None:
        try:
            return parse_time(source.get(key)) if source.get(key) else None
        except ValueError:
            return None
    try:
        session_date = date.fromisoformat(str(source.get("session_date"))) if source.get("session_date") else None
    except ValueError:
        session_date = None
    return EngineState(
        position=int(source.get("position") or 0),
        entry_price=float(source["entry_price"]) if source.get("entry_price") is not None else None,
        entry_time=optional_time("entry_time"),
        target_price=float(source["target_price"]) if source.get("target_price") is not None else None,
        stop_price=float(source["stop_price"]) if source.get("stop_price") is not None else None,
        pending=pending,
        session_date=session_date,
        long_locked=bool(source.get("long_locked", False)),
        short_locked=bool(source.get("short_locked", False)),
        entries_today=int(source.get("entries_today") or 0),
        day_reference_close=float(source["day_reference_close"]) if source.get("day_reference_close") is not None else None,
        day_open=float(source["day_open"]) if source.get("day_open") is not None else None,
        gap_direction=int(source.get("gap_direction") or 0),
        last_bar_time=optional_time("last_bar_time"),
    )
