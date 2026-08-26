import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from signalops.config import settings
from signalops.db import SessionLocal
from signalops.repository import get_latest_signal_event, list_events_after
from signalops.schemas import SignalEvent

router = APIRouter(tags=["即時事件"])


@router.websocket("/api/v1/stream")
async def stream_signals(websocket: WebSocket) -> None:
    """以 WebSocket 推送新增的 SignalEvent；不接受任何交易指令。"""
    await websocket.accept()
    with SessionLocal() as session:
        latest = get_latest_signal_event(session)

    last_time = latest.occurred_at if latest else datetime.now(UTC)
    last_id = latest.id if latest else UUID(int=0)
    await websocket.send_json(
        {
            "type": "connected",
            "message": "已連線至唯讀事件串流",
            "cursor": f"{last_time.isoformat()}|{last_id}",
        }
    )

    heartbeat_at = asyncio.get_running_loop().time()
    try:
        while True:
            await asyncio.sleep(settings.websocket_poll_seconds)
            with SessionLocal() as session:
                events = list_events_after(
                    session,
                    occurred_at=last_time,
                    event_id=last_id,
                )
            for row in events:
                event = SignalEvent.model_validate(row)
                await websocket.send_json(
                    {"type": "signal_event", "data": event.model_dump(mode="json")}
                )
                last_time, last_id = row.occurred_at, row.id

            now = asyncio.get_running_loop().time()
            if now - heartbeat_at >= settings.websocket_heartbeat_seconds:
                await websocket.send_json(
                    {"type": "heartbeat", "sent_at": datetime.now(UTC).isoformat()}
                )
                heartbeat_at = now
    except WebSocketDisconnect:
        return
