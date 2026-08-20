from __future__ import annotations

import csv
import re
from dataclasses import dataclass
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

STRATEGY_ALIASES = {
    # The provider has previously emitted this typo for CFCWIN01m.
    "CFCWN01m": "CFCWIN01m",
}

H_REQUIRED_MARKER = "浩克3V3訊號通知"
H_POSITION_PATTERN = re.compile(
    r"小型台指近一訊號部位為\s*[:：]\s*(?P<side>多|空)\s*(?P<quantity>\d+)\s*口"
)
SIX_POSITION_PATTERN = re.compile(
    r"《策略》\s*(?P<strategy>[A-Za-z0-9]+)\s*"
    r"《倉位》\s*(?P<old>[+-]?\d+(?:\.\d+)?)\s*->\s*"
    r"(?P<new>[+-]?\d+(?:\.\d+)?)"
)
ACCOUNT_PATTERN = re.compile(r"【(?P<account>\d+)】")


@dataclass(frozen=True)
class HSignal:
    position: int
    announced_quantity: int


@dataclass(frozen=True)
class SixStrategySignal:
    account: str
    strategy_code: str
    raw_strategy_code: str
    previous_position: int
    new_position: int


@dataclass(frozen=True)
class Decision:
    ready: bool
    h_position: int | None
    e_net: int
    f_net: int
    e_direction: int
    f_direction: int
    consensus: int
    target_position: int | None
    relation: str
    reason: str
    missing_strategies: tuple[str, ...]


def _parse_discrete_position(value: str) -> int | None:
    try:
        number = float(value)
    except ValueError:
        return None
    if number not in {-1.0, 0.0, 1.0}:
        return None
    return int(number)


def _sign(value: int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def parse_h_signal(text: str) -> HSignal | None:
    if H_REQUIRED_MARKER not in text:
        return None
    match = H_POSITION_PATTERN.search(text)
    if not match:
        return None
    position = 1 if match.group("side") == "多" else -1
    return HSignal(
        position=position,
        announced_quantity=int(match.group("quantity")),
    )


def parse_six_strategy_signal(
    text: str,
    *,
    required_account: str | None = None,
) -> SixStrategySignal | None:
    if "訊號通知" not in text or "群益" not in text:
        return None
    match = SIX_POSITION_PATTERN.search(text)
    if not match:
        return None

    raw_code = match.group("strategy").strip()
    strategy_code = STRATEGY_ALIASES.get(raw_code, raw_code)
    if strategy_code not in ALL_STRATEGIES:
        return None

    account_match = ACCOUNT_PATTERN.search(text)
    account = account_match.group("account") if account_match else ""
    if required_account and account != required_account:
        return None

    previous_position = _parse_discrete_position(match.group("old"))
    new_position = _parse_discrete_position(match.group("new"))
    if previous_position is None or new_position is None:
        return None
    if previous_position == new_position:
        return None

    return SixStrategySignal(
        account=account,
        strategy_code=strategy_code,
        raw_strategy_code=raw_code,
        previous_position=previous_position,
        new_position=new_position,
    )


def evaluate_strategy(
    h_position: int | None,
    strategy_positions: Mapping[str, int],
) -> Decision:
    missing = tuple(code for code in ALL_STRATEGIES if code not in strategy_positions)
    if h_position not in {-1, 1}:
        return Decision(
            ready=False,
            h_position=h_position,
            e_net=0,
            f_net=0,
            e_direction=0,
            f_direction=0,
            consensus=0,
            target_position=None,
            relation="not_ready",
            reason="尚未取得浩克3目前多空方向",
            missing_strategies=missing,
        )
    if missing:
        return Decision(
            ready=False,
            h_position=h_position,
            e_net=0,
            f_net=0,
            e_direction=0,
            f_direction=0,
            consensus=0,
            target_position=None,
            relation="not_ready",
            reason=f"尚缺少{len(missing)}套E/F策略初始持倉",
            missing_strategies=missing,
        )

    normalized_positions: dict[str, int] = {}
    for code in ALL_STRATEGIES:
        position = int(strategy_positions[code])
        if position not in {-1, 0, 1}:
            raise ValueError(f"{code}持倉必須是-1、0或1，目前為{position}")
        normalized_positions[code] = position

    e_net = sum(normalized_positions[code] for code in PORTFOLIO_E)
    f_net = sum(normalized_positions[code] for code in PORTFOLIO_F)
    e_direction = _sign(e_net)
    f_direction = _sign(f_net)
    consensus = e_direction if e_direction == f_direction and e_direction != 0 else 0

    if consensus == -h_position:
        target_position = 0
        relation = "opposite"
        reason = "E、F形成一致共識且與H反向，永豐降為空手"
    elif consensus == h_position:
        target_position = 2 * h_position
        relation = "same"
        reason = "E、F形成一致共識且與H同向，永豐持有2口"
    else:
        target_position = h_position
        relation = "neutral"
        reason = "E、F沒有一致共識，永豐只跟H持有1口"

    return Decision(
        ready=True,
        h_position=h_position,
        e_net=e_net,
        f_net=f_net,
        e_direction=e_direction,
        f_direction=f_direction,
        consensus=consensus,
        target_position=target_position,
        relation=relation,
        reason=reason,
        missing_strategies=(),
    )


def position_text(position: int | None) -> str:
    if position is None:
        return "未知"
    if position > 0:
        return f"多{position}口"
    if position < 0:
        return f"空{abs(position)}口"
    return "空手"


def simulated_order_action(previous: int, target: int) -> tuple[str, str, int]:
    """Describe the signed target-position adjustment used by Discord simulation."""
    if previous == target:
        return "維持部位", "不送單", 0

    delta = target - previous
    side = "買進" if delta > 0 else "賣出"
    quantity = abs(delta)
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
    return action, side, quantity


def position_event_action(previous: int | None, new: int) -> str:
    """Describe one strategy's -1/0/1 position transition."""
    if new not in {-1, 0, 1} or previous not in {None, -1, 0, 1}:
        raise ValueError(f"無效的部位變化: {previous} -> {new}")
    if previous == new:
        return "部位不變"
    if previous is None:
        if new > 0:
            return "初始多單"
        if new < 0:
            return "初始空單"
        return "初始空手"
    if previous == 0:
        return "多單進場" if new > 0 else "空單進場"
    if new == 0:
        return "多單平倉" if previous > 0 else "空單平倉"
    return "空單平倉並轉多" if new > 0 else "多單平倉並轉空"


def _csv_position(value: object) -> int | None:
    try:
        position = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    return position if position in {-1, 0, 1} else None


def load_latest_h_position(path: Path) -> int | None:
    """Read the latest valid H position from the append-only H event file."""
    if not path.exists():
        return None
    latest: int | None = None
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            position = _csv_position(row.get("new_position"))
            if position in {-1, 1}:
                latest = position
    return latest


def load_latest_ef_positions(path: Path) -> dict[str, int]:
    """Rebuild all latest E/F positions from the append-only EF event file."""
    latest: dict[str, int] = {}
    if not path.exists():
        return latest
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            code = STRATEGY_ALIASES.get(
                str(row.get("strategy_code") or "").strip(),
                str(row.get("strategy_code") or "").strip(),
            )
            position = _csv_position(row.get("new_position"))
            if code in ALL_STRATEGIES and position is not None:
                latest[code] = position
    return latest
