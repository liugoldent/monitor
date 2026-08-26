from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SignalEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    schema_version: Literal[1] = 1
    occurred_at: datetime
    received_at: datetime
    source: Literal["legacy_csv", "webhook", "replay"]
    instrument: str = Field(min_length=1, max_length=32)
    strategy_code: str = Field(min_length=1, max_length=80)
    strategy_name: str = Field(min_length=1, max_length=160)
    account_ref: str | None = Field(default=None, pattern=r"^acct_[a-f0-9]{16}$")
    previous_position: int = Field(ge=-1, le=1)
    new_position: int = Field(ge=-1, le=1)
    action: Literal["enter", "exit", "reverse"]
    side: Literal["bull", "bear"]
    quantity: Decimal = Field(gt=0)
    reference_price: Decimal | None = Field(default=None, gt=0)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        previous = self.previous_position
        current = self.new_position

        valid_transition = {
            "enter": previous == 0 and current in {-1, 1},
            "exit": previous in {-1, 1} and current == 0,
            "reverse": previous in {-1, 1} and current == -previous,
        }[self.action]
        if not valid_transition:
            raise ValueError(f"{self.action} 的持倉轉換無效：{previous} -> {current}")

        directional_position = previous if self.action == "exit" else current
        expected_side = "bull" if directional_position == 1 else "bear"
        if self.side != expected_side:
            raise ValueError(f"方向 {self.side} 與持倉轉換方向 {expected_side} 不一致")
        return self


class SignalEventPage(BaseModel):
    items: list[SignalEvent]
    next_cursor: str | None = None


class CurrentPosition(BaseModel):
    event_id: UUID
    strategy_code: str
    strategy_name: str
    instrument: str
    position: int = Field(ge=-1, le=1)
    position_label: Literal["long", "short", "flat"]
    quantity: Decimal = Field(gt=0)
    updated_at: datetime


class OverviewCounts(BaseModel):
    total_events: int
    strategies: int
    entries: int
    exits: int
    reversals: int
    long_positions: int
    short_positions: int
    flat_positions: int


class StrategySummary(BaseModel):
    strategy_code: str
    strategy_name: str
    event_count: int
    entries: int
    exits: int
    reversals: int
    current_position: int = Field(ge=-1, le=1)
    last_event_at: datetime


class SignalOverview(BaseModel):
    generated_at: datetime
    last_event_at: datetime | None
    counts: OverviewCounts
    positions: list[CurrentPosition]
    strategies: list[StrategySummary]


class BusinessKpis(BaseModel):
    active_strategies: int
    exposure_rate: float = Field(ge=0, le=1)
    reversal_rate: float = Field(ge=0, le=1)
    average_events_per_strategy: float = Field(ge=0)
    reference_price_coverage: float = Field(ge=0, le=1)


class ActivityPoint(BaseModel):
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    total: int
    entries: int
    exits: int
    reversals: int


class TransitionCount(BaseModel):
    previous_position: int = Field(ge=-1, le=1)
    new_position: int = Field(ge=-1, le=1)
    count: int


class DataQualitySummary(BaseModel):
    total_events: int
    missing_reference_price: int
    reference_price_coverage: float = Field(ge=0, le=1)
    last_event_at: datetime | None


class BusinessAnalytics(BaseModel):
    generated_at: datetime
    periods: int
    kpis: BusinessKpis
    activity: list[ActivityPoint]
    transitions: list[TransitionCount]
    data_quality: DataQualitySummary
    limitations: list[str]
