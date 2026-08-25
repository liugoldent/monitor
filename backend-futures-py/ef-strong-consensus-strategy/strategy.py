from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping


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
MAX_POSITION_UNIT = 20


@dataclass(frozen=True)
class Decision:
    ready: bool
    e_net: int
    f_net: int
    target_position: int | None
    relation: str
    reason: str
    missing_strategies: tuple[str, ...]


def validate_position_unit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"U必須是1到{MAX_POSITION_UNIT}的整數，目前為{value!r}")
    if not 1 <= value <= MAX_POSITION_UNIT:
        raise ValueError(f"U必須是1到{MAX_POSITION_UNIT}的整數，目前為{value!r}")
    return value


def validate_min_group_net(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"強共識門檻必須是1到6的整數，目前為{value!r}")
    if not 1 <= value <= len(PORTFOLIO_E):
        raise ValueError(f"強共識門檻必須是1到6的整數，目前為{value!r}")
    return value


def evaluate_strategy(
    strategy_positions: Mapping[str, int],
    *,
    min_group_net: int = 2,
    position_unit: int = 1,
) -> Decision:
    """Evaluate the pure E/F strategy without reading or inferring H."""
    threshold = validate_min_group_net(min_group_net)
    unit = validate_position_unit(position_unit)
    missing = tuple(code for code in ALL_STRATEGIES if code not in strategy_positions)
    if missing:
        return Decision(
            ready=False,
            e_net=0,
            f_net=0,
            target_position=None,
            relation="not_ready",
            reason=f"尚缺少{len(missing)}套E/F策略目前倉位",
            missing_strategies=missing,
        )

    normalized: dict[str, int] = {}
    for code in ALL_STRATEGIES:
        position = int(strategy_positions[code])
        if position not in {-1, 0, 1}:
            raise ValueError(f"{code}持倉必須是-1、0或1，目前為{position}")
        normalized[code] = position

    e_net = sum(normalized[code] for code in PORTFOLIO_E)
    f_net = sum(normalized[code] for code in PORTFOLIO_F)
    if e_net >= threshold and f_net >= threshold:
        target = unit
        relation = "strong_bull"
        reason = f"E淨部位{e_net}、F淨部位{f_net}，兩組皆達多方強共識門檻{threshold}"
    elif e_net <= -threshold and f_net <= -threshold:
        target = -unit
        relation = "strong_bear"
        reason = f"E淨部位{e_net}、F淨部位{f_net}，兩組皆達空方強共識門檻-{threshold}"
    else:
        target = 0
        relation = "no_strong_consensus"
        reason = (
            f"E淨部位{e_net}、F淨部位{f_net}，未形成雙方至少{threshold}票的同向強共識"
        )

    return Decision(
        ready=True,
        e_net=e_net,
        f_net=f_net,
        target_position=target,
        relation=relation,
        reason=reason,
        missing_strategies=(),
    )


def load_latest_positions(path: Path) -> dict[str, int]:
    positions: dict[str, int] = {}
    if not path.exists():
        return positions
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_code = str(row.get("strategy_code") or row.get("raw_strategy_code") or "").strip()
            code = STRATEGY_ALIASES.get(raw_code, raw_code)
            if code not in ALL_STRATEGIES:
                continue
            try:
                position = int(float(str(row.get("new_position") or "").strip()))
            except ValueError:
                continue
            if position in {-1, 0, 1}:
                positions[code] = position
    return positions


def signal_event_key(row: Mapping[str, object], row_number: int) -> str:
    return "|".join(
        (
            str(row_number),
            str(row.get("received_at") or ""),
            str(row.get("message_time") or ""),
            str(row.get("strategy_code") or row.get("raw_strategy_code") or ""),
            str(row.get("previous_position") or ""),
            str(row.get("new_position") or ""),
        )
    )


def load_latest_recorded_close(path: Path, cutoff: datetime) -> float | None:
    latest_time: datetime | None = None
    latest_price: float | None = None
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                recorded_at = datetime.strptime(
                    str(row.get("Record Time") or "").strip(),
                    "%Y-%m-%d %H:%M:%S",
                )
                close = float(str(row.get("Close") or "").strip())
            except (TypeError, ValueError):
                continue
            comparable_cutoff = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
            if recorded_at <= comparable_cutoff and (
                latest_time is None or recorded_at > latest_time
            ):
                latest_time = recorded_at
                latest_price = close
    return latest_price


def build_trade_rows(
    *,
    timestamp: str,
    previous_position: int,
    target_position: int,
    price: float | None,
    previous_entry_price: float | None,
    point_value: int = 10,
) -> list[dict[str, object]]:
    """Close/open analytical segments and record total quantity-aware TWD PnL."""
    if previous_position == target_position:
        return []
    normalized_price: float | str = "" if price is None else float(price)
    rows: list[dict[str, object]] = []
    if previous_position:
        pnl: float | str = ""
        if price is not None and previous_entry_price is not None:
            direction = 1 if previous_position > 0 else -1
            pnl = round(
                (float(price) - float(previous_entry_price))
                * direction
                * abs(previous_position)
                * point_value,
                2,
            )
        rows.append(
            {
                "timestamp": timestamp,
                "action": "exiting",
                "side": "bull" if previous_position > 0 else "bear",
                "price": normalized_price,
                "pnl": pnl,
                "quantity": abs(previous_position),
            }
        )
    if target_position:
        rows.append(
            {
                "timestamp": timestamp,
                "action": "enter",
                "side": "bull" if target_position > 0 else "bear",
                "price": normalized_price,
                "pnl": "",
                "quantity": abs(target_position),
            }
        )
    return rows


def simulated_order_action(previous: int, target: int) -> tuple[str, str, int]:
    difference = target - previous
    if difference == 0:
        return "維持", "", 0
    side = "買進" if difference > 0 else "賣出"
    if previous == 0:
        action = "進場"
    elif target == 0:
        action = "平倉"
    elif (previous > 0) != (target > 0):
        action = "反向切換"
    elif abs(target) > abs(previous):
        action = "加碼"
    else:
        action = "減碼"
    return action, side, abs(difference)


def position_text(position: int | None) -> str:
    if position is None:
        return "未知"
    if position > 0:
        return f"多{position}口"
    if position < 0:
        return f"空{abs(position)}口"
    return "空手"
