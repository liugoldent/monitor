from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from signalops.schemas import SignalEvent


def make_event(**overrides: object) -> SignalEvent:
    values = {
        "id": uuid4(),
        "occurred_at": datetime.now(UTC),
        "received_at": datetime.now(UTC),
        "source": "legacy_csv",
        "instrument": "MXF",
        "strategy_code": "S1",
        "strategy_name": "Strategy 1",
        "previous_position": 0,
        "new_position": 1,
        "action": "enter",
        "side": "bull",
        "quantity": 1,
    }
    values.update(overrides)
    return SignalEvent.model_validate(values)


@pytest.mark.parametrize(
    ("previous_position", "new_position", "action", "side"),
    [
        (0, 1, "enter", "bull"),
        (0, -1, "enter", "bear"),
        (1, 0, "exit", "bull"),
        (-1, 0, "exit", "bear"),
        (1, -1, "reverse", "bear"),
        (-1, 1, "reverse", "bull"),
    ],
)
def test_valid_position_transitions(
    previous_position: int, new_position: int, action: str, side: str
) -> None:
    event = make_event(
        previous_position=previous_position,
        new_position=new_position,
        action=action,
        side=side,
    )
    assert event.new_position == new_position


def test_rejects_transition_that_does_not_match_action() -> None:
    with pytest.raises(ValidationError, match="enter 的持倉轉換無效"):
        make_event(previous_position=1, new_position=-1, action="enter", side="bear")


def test_rejects_side_that_does_not_match_direction() -> None:
    with pytest.raises(ValidationError, match="與持倉轉換方向 bull 不一致"):
        make_event(previous_position=0, new_position=1, action="enter", side="bear")
