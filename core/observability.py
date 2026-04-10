"""
core/observability.py - Structured logging, metrics, tracing helpers (Phase 5)
"""

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

import config

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
except Exception:  # pragma: no cover
    Counter = Histogram = Gauge = None
    generate_latest = None
    CONTENT_TYPE_LATEST = "text/plain"

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except Exception:  # pragma: no cover
    trace = None

logger = logging.getLogger(__name__)

REQUEST_COUNTER = Counter(
    "yolohome_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
) if Counter else None

REQUEST_LATENCY = Histogram(
    "yolohome_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
) if Histogram else None

ACTIVE_REQUESTS = Gauge(
    "yolohome_http_active_requests",
    "Active HTTP requests",
) if Gauge else None


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "thread": record.threadName,
            "message": record.getMessage(),
        }
        trace_id = getattr(record, "trace_id", None)
        if trace_id:
            payload["trace_id"] = trace_id
        extra_event = getattr(record, "event", None)
        if extra_event:
            payload["event"] = extra_event
        return json.dumps(payload, ensure_ascii=False)


def configure_structured_logging(root_logger: Optional[logging.Logger] = None):
    if not getattr(config, "LOG_STRUCTURED", False):
        return
    root = root_logger or logging.getLogger()
    formatter = JsonLogFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)


def init_tracing(service_name: str = "yolohome-gateway"):
    if not getattr(config, "TRACING_ENABLED", False):
        return
    if trace is None:
        logger.warning("[Tracing] opentelemetry packages not available")
        return

    try:
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=getattr(config, "OTLP_ENDPOINT", "http://localhost:4318/v1/traces"))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        logger.info("[Tracing] OpenTelemetry initialized")
    except Exception as exc:
        logger.warning("[Tracing] init failed: %s", exc)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        request.state.trace_id = trace_id

        if ACTIVE_REQUESTS:
            ACTIVE_REQUESTS.inc()

        path = request.url.path
        method = request.method
        status_code = 500

        tracer = None
        span_ctx = None
        if trace is not None and getattr(config, "TRACING_ENABLED", False):
            tracer = trace.get_tracer("yolohome-http")
            span_ctx = tracer.start_as_current_span(f"{method} {path}")
            span_ctx.__enter__()

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            duration = time.perf_counter() - start
            if REQUEST_COUNTER:
                REQUEST_COUNTER.labels(method=method, path=path, status=str(status_code)).inc()
            if REQUEST_LATENCY:
                REQUEST_LATENCY.labels(method=method, path=path).observe(duration)
            if ACTIVE_REQUESTS:
                ACTIVE_REQUESTS.dec()

            logger.info(
                "http_request",
                extra={
                    "event": "http_request",
                    "trace_id": trace_id,
                },
            )

            if span_ctx is not None:
                try:
                    span_ctx.__exit__(None, None, None)
                except Exception:
                    pass


def metrics_response() -> Response:
    if generate_latest is None:
        return Response("metrics unavailable", media_type="text/plain", status_code=503)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
