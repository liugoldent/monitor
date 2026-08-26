import base64
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.orm import Session

from signalops.models import SignalEventModel
from signalops.schemas import (
    ActivityPoint,
    BusinessAnalytics,
    BusinessKpis,
    CurrentPosition,
    DataQualitySummary,
    OverviewCounts,
    SignalOverview,
    StrategySummary,
    TransitionCount,
)


def encode_cursor(occurred_at: datetime, event_id: UUID) -> str:
    value = f"{occurred_at.isoformat()}|{event_id}".encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding).decode()
        occurred_at, event_id = decoded.rsplit("|", maxsplit=1)
        return datetime.fromisoformat(occurred_at), UUID(event_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("無效的分頁游標") from exc


def list_signal_events(
    session: Session,
    *,
    limit: int,
    cursor: str | None,
    strategy_code: str | None,
    action: str | None,
) -> tuple[list[SignalEventModel], str | None]:
    query = select(SignalEventModel)

    if strategy_code:
        query = query.where(SignalEventModel.strategy_code == strategy_code)
    if action:
        query = query.where(SignalEventModel.action == action)
    if cursor:
        cursor_time, cursor_id = decode_cursor(cursor)
        query = query.where(
            or_(
                SignalEventModel.occurred_at < cursor_time,
                and_(
                    SignalEventModel.occurred_at == cursor_time,
                    SignalEventModel.id < cursor_id,
                ),
            )
        )

    query = query.order_by(SignalEventModel.occurred_at.desc(), SignalEventModel.id.desc()).limit(
        limit + 1
    )
    rows = list(session.scalars(query))
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(last.occurred_at, last.id)
    return items, next_cursor


def list_events_after(
    session: Session,
    *,
    occurred_at: datetime,
    event_id: UUID,
    limit: int = 100,
) -> list[SignalEventModel]:
    query = (
        select(SignalEventModel)
        .where(
            or_(
                SignalEventModel.occurred_at > occurred_at,
                and_(
                    SignalEventModel.occurred_at == occurred_at,
                    SignalEventModel.id > event_id,
                ),
            )
        )
        .order_by(SignalEventModel.occurred_at, SignalEventModel.id)
        .limit(limit)
    )
    return list(session.scalars(query))


def get_latest_signal_event(session: Session) -> SignalEventModel | None:
    return session.scalar(
        select(SignalEventModel)
        .order_by(SignalEventModel.occurred_at.desc(), SignalEventModel.id.desc())
        .limit(1)
    )


def _latest_event_rows(session: Session) -> list[dict[str, object]]:
    ranked = select(
        SignalEventModel.id.label("event_id"),
        SignalEventModel.strategy_code,
        SignalEventModel.strategy_name,
        SignalEventModel.instrument,
        SignalEventModel.new_position.label("position"),
        SignalEventModel.quantity,
        SignalEventModel.occurred_at.label("updated_at"),
        func.row_number()
        .over(
            partition_by=SignalEventModel.strategy_code,
            order_by=(SignalEventModel.occurred_at.desc(), SignalEventModel.id.desc()),
        )
        .label("row_number"),
    ).subquery()
    query = select(ranked).where(ranked.c.row_number == 1).order_by(ranked.c.strategy_code)
    return [dict(row) for row in session.execute(query).mappings()]


def list_current_positions(session: Session) -> list[CurrentPosition]:
    positions = []
    for row in _latest_event_rows(session):
        position = int(row["position"])
        positions.append(
            CurrentPosition(
                event_id=row["event_id"],
                strategy_code=row["strategy_code"],
                strategy_name=row["strategy_name"],
                instrument=row["instrument"],
                position=position,
                position_label={-1: "short", 0: "flat", 1: "long"}[position],
                quantity=row["quantity"],
                updated_at=row["updated_at"],
            )
        )
    return positions


def get_signal_overview(session: Session) -> SignalOverview:
    total_events, strategy_count, last_event_at = session.execute(
        select(
            func.count(SignalEventModel.id),
            func.count(distinct(SignalEventModel.strategy_code)),
            func.max(SignalEventModel.occurred_at),
        )
    ).one()
    action_counts = {
        action: count
        for action, count in session.execute(
            select(SignalEventModel.action, func.count(SignalEventModel.id)).group_by(
                SignalEventModel.action
            )
        )
    }
    positions = list_current_positions(session)
    position_counts = {
        value: sum(position.position == value for position in positions) for value in (-1, 0, 1)
    }

    strategy_rows = session.execute(
        select(
            SignalEventModel.strategy_code,
            func.max(SignalEventModel.strategy_name).label("strategy_name"),
            func.count(SignalEventModel.id).label("event_count"),
            func.sum(case((SignalEventModel.action == "enter", 1), else_=0)).label("entries"),
            func.sum(case((SignalEventModel.action == "exit", 1), else_=0)).label("exits"),
            func.sum(case((SignalEventModel.action == "reverse", 1), else_=0)).label("reversals"),
            func.max(SignalEventModel.occurred_at).label("last_event_at"),
        )
        .group_by(SignalEventModel.strategy_code)
        .order_by(func.count(SignalEventModel.id).desc(), SignalEventModel.strategy_code)
    ).mappings()
    position_by_strategy = {position.strategy_code: position.position for position in positions}
    strategies = [
        StrategySummary(
            **dict(row),
            current_position=position_by_strategy[row["strategy_code"]],
        )
        for row in strategy_rows
    ]
    return SignalOverview(
        generated_at=datetime.now(UTC),
        last_event_at=last_event_at,
        counts=OverviewCounts(
            total_events=total_events,
            strategies=strategy_count,
            entries=action_counts.get("enter", 0),
            exits=action_counts.get("exit", 0),
            reversals=action_counts.get("reverse", 0),
            long_positions=position_counts[1],
            short_positions=position_counts[-1],
            flat_positions=position_counts[0],
        ),
        positions=positions,
        strategies=strategies,
    )


def get_business_analytics(session: Session, *, periods: int = 12) -> BusinessAnalytics:
    overview = get_signal_overview(session)
    counts = overview.counts
    active_strategies = counts.long_positions + counts.short_positions
    reference_prices = (
        session.scalar(
            select(func.count(SignalEventModel.id)).where(
                SignalEventModel.reference_price.is_not(None)
            )
        )
        or 0
    )
    total = counts.total_events
    coverage = reference_prices / total if total else 0.0

    year = func.extract("year", SignalEventModel.occurred_at).label("year")
    month = func.extract("month", SignalEventModel.occurred_at).label("month")
    activity_rows = list(
        session.execute(
            select(
                year,
                month,
                func.count(SignalEventModel.id).label("total"),
                func.sum(case((SignalEventModel.action == "enter", 1), else_=0)).label("entries"),
                func.sum(case((SignalEventModel.action == "exit", 1), else_=0)).label("exits"),
                func.sum(case((SignalEventModel.action == "reverse", 1), else_=0)).label(
                    "reversals"
                ),
            )
            .group_by(year, month)
            .order_by(year, month)
        ).mappings()
    )[-periods:]
    activity = [
        ActivityPoint(
            period=f"{int(row['year']):04d}-{int(row['month']):02d}",
            total=row["total"],
            entries=row["entries"],
            exits=row["exits"],
            reversals=row["reversals"],
        )
        for row in activity_rows
    ]

    transition_rows = session.execute(
        select(
            SignalEventModel.previous_position,
            SignalEventModel.new_position,
            func.count(SignalEventModel.id).label("count"),
        )
        .group_by(SignalEventModel.previous_position, SignalEventModel.new_position)
        .order_by(SignalEventModel.previous_position, SignalEventModel.new_position)
    ).mappings()

    return BusinessAnalytics(
        generated_at=datetime.now(UTC),
        periods=periods,
        kpis=BusinessKpis(
            active_strategies=active_strategies,
            exposure_rate=active_strategies / counts.strategies if counts.strategies else 0,
            reversal_rate=counts.reversals / total if total else 0,
            average_events_per_strategy=total / counts.strategies if counts.strategies else 0,
            reference_price_coverage=coverage,
        ),
        activity=activity,
        transitions=[TransitionCount(**dict(row)) for row in transition_rows],
        data_quality=DataQualitySummary(
            total_events=total,
            missing_reference_price=total - reference_prices,
            reference_price_coverage=coverage,
            last_event_at=overview.last_event_at,
        ),
        limitations=[
            "目前來源資料缺少可靠成交價，因此不計算損益、勝率與報酬率。",
            "曝險比例代表有方向持倉的策略占比，不等於資金曝險比例。",
        ],
    )
