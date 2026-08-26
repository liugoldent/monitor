import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "SignalOps 策略事件 API"
    database_url: str = os.getenv(
        "SIGNALOPS_DATABASE_URL",
        "postgresql+psycopg://signalops:signalops@localhost:5434/signalops",
    )
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv("SIGNALOPS_CORS_ORIGINS", "http://localhost:5373").split(",")
        if origin.strip()
    )
    websocket_poll_seconds: float = float(os.getenv("SIGNALOPS_WS_POLL_SECONDS", "2"))
    websocket_heartbeat_seconds: float = float(os.getenv("SIGNALOPS_WS_HEARTBEAT_SECONDS", "15"))
    assistant_mode: str = os.getenv("SIGNALOPS_ASSISTANT_MODE", "auto")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("SIGNALOPS_OPENAI_MODEL", "gpt-5.4-mini")
    kafka_brokers: str = os.getenv("SIGNALOPS_KAFKA_BROKERS", "localhost:19092")
    kafka_topic: str = os.getenv("SIGNALOPS_KAFKA_TOPIC", "signal.events.v1")
    outbox_poll_seconds: float = float(os.getenv("SIGNALOPS_OUTBOX_POLL_SECONDS", "2"))
    otlp_endpoint: str | None = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")


settings = Settings()
