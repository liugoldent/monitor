from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from signalops.db import get_session
from signalops.repository import get_business_analytics, get_signal_overview, list_current_positions
from signalops.schemas import BusinessAnalytics, CurrentPosition, SignalOverview

router = APIRouter(prefix="/api/v1", tags=["策略總覽"])


@router.get(
    "/overview",
    response_model=SignalOverview,
    summary="取得策略事件與目前持倉總覽",
)
def get_overview(
    session: Annotated[Session, Depends(get_session)],
) -> SignalOverview:
    return get_signal_overview(session)


@router.get(
    "/positions",
    response_model=list[CurrentPosition],
    summary="取得每個策略的最新持倉",
)
def get_positions(
    session: Annotated[Session, Depends(get_session)],
) -> list[CurrentPosition]:
    return list_current_positions(session)


@router.get(
    "/analytics",
    response_model=BusinessAnalytics,
    summary="取得策略營運 BI 指標與事件趨勢",
)
def get_analytics(
    session: Annotated[Session, Depends(get_session)],
    periods: int = 12,
) -> BusinessAnalytics:
    return get_business_analytics(session, periods=max(1, min(periods, 36)))
