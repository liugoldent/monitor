from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from signalops.assistant.service import assistant_event_stream

router = APIRouter(prefix="/api/v1/assistant", tags=["策略小助手"])


class AssistantRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


@router.post(
    "/query",
    summary="以 SSE 串流唯讀策略問答",
    response_class=StreamingResponse,
)
def query_assistant(request: AssistantRequest) -> StreamingResponse:
    return StreamingResponse(
        assistant_event_stream(request.question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
