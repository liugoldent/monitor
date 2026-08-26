import time

from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, make_asgi_app

from signalops.config import settings

HTTP_REQUESTS = Counter(
    "signalops_http_requests_total",
    "SignalOps HTTP 請求總數",
    ["method", "path", "status"],
)
HTTP_DURATION = Histogram(
    "signalops_http_request_duration_seconds",
    "SignalOps HTTP 請求時間",
    ["method", "path"],
)


def configure_observability(app: FastAPI) -> None:
    app.mount("/metrics", make_asgi_app())

    @app.middleware("http")
    async def record_http_metrics(request: Request, call_next):
        started_at = time.perf_counter()
        response = await call_next(request)
        path = request.scope.get("route").path if request.scope.get("route") else request.url.path
        HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, path).observe(time.perf_counter() - started_at)
        return response

    if settings.otlp_endpoint:
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": "signalops-api",
                    "service.version": "0.2.0",
                }
            )
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
