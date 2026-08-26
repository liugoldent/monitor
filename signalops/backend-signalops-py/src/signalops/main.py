from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from signalops.api.assistant import router as assistant_router
from signalops.api.overview import router as overview_router
from signalops.api.signals import router as signals_router
from signalops.api.stream import router as stream_router
from signalops.config import settings
from signalops.db import engine
from signalops.observability import configure_observability

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="唯讀、保護隱私的策略事件與持倉查詢 API。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(signals_router)
app.include_router(overview_router)
app.include_router(stream_router)
app.include_router(assistant_router)
configure_observability(app)


@app.get("/healthz", tags=["維運"], summary="檢查 API 程序是否存活")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["維運"], summary="檢查資料庫是否可用")
def readiness() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}
