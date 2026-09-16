import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# 1. request latency
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path", "status_code"],
)

# 2. tool execution duration
TOOL_EXECUTION_DURATION = Histogram(
    "tool_execution_duration_seconds",
    "Diagnostic tool execution latency",
    ["tool_name"],
)

# 3. tool failures
TOOL_EXECUTION_FAILURES = Counter(
    "tool_execution_failures_total",
    "Diagnostic tool execution failures",
    ["tool_name", "error_code"],
)

# 4. retrieval (pgvector) duration
RETRIEVAL_DURATION = Histogram(
    "retrieval_duration_seconds",
    "pgvector similarity retrieval latency",
    ["evidence_type"],
)

# 5. ingestion count
INGESTION_RECORDS = Counter(
    "ingestion_records_total",
    "Records ingested",
    ["record_type"],
)

# 6. investigation count
INVESTIGATIONS = Counter(
    "investigations_total",
    "Investigations by terminal/transitional status",
    ["status"],
)

# 7. LLM call duration
LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "Reasoning provider call latency",
    ["provider"],
)

# 8. LLM call count
LLM_CALLS = Counter(
    "llm_calls_total",
    "Reasoning provider calls",
    ["provider", "status"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        route = request.scope.get("route")
        path = route.path if route is not None else request.url.path
        HTTP_REQUEST_DURATION.labels(
            method=request.method, path=path, status_code=str(response.status_code)
        ).observe(duration)
        return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
