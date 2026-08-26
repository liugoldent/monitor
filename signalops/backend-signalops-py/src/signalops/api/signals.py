from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from signalops.db import get_session
from signalops.repository import list_signal_events
from signalops.schemas import SignalEvent, SignalEventPage

router = APIRouter(prefix="/api/v1/signals", tags=["策略事件"])


@router.get("", response_model=SignalEventPage, summary="查詢匿名化策略事件")
def get_signals(
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(default=24, ge=1, le=100),
    cursor: str | None = None,
    strategy_code: str | None = Query(default=None, min_length=1, max_length=80),
    action: Literal["enter", "exit", "reverse"] | None = None,
) -> SignalEventPage:
    try:
        rows, next_cursor = list_signal_events(
            session,
            limit=limit,
            cursor=cursor,
            strategy_code=strategy_code,
            action=action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SignalEventPage(
        items=[SignalEvent.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )
