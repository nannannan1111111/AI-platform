"""Small, dependency-free observability primitives for the web and worker.

The production deployment can scrape the metrics endpoint with Prometheus (or
translate the same exposition format into Tencent Cloud monitoring).  Keeping
the registry in-process is intentional: metrics are diagnostic signals, while
PostgreSQL remains the source of truth for tasks and payments.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping
from threading import Lock

from fastapi import Request
from fastapi.responses import PlainTextResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_DATABASE_URL = re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|redis)://[^\s\"']+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|authorization|cookie|database[_-]?url)\s*[:=]\s*[^\s,;]+"
)
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "database_url",
    "image",
    "images",
    "password",
    "prompt",
    "secret",
    "token",
}
_ALLOWED_LOG_FIELDS = {
    "attempt_id",
    "event",
    "method",
    "provider_request_id",
    "request_id",
    "route",
    "security_event",
    "status",
    "status_code",
    "task_id",
    "worker_index",
}
_LOG = logging.getLogger(__name__)


def new_request_id() -> str:
    """Return a compact, log-safe correlation identifier."""
    return uuid.uuid4().hex


def valid_request_id(value: str | None) -> str | None:
    """Accept only bounded header values; reject CR/LF and arbitrary content."""
    if value is None:
        return None
    candidate = value.strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else None


def redact_text(value: str) -> str:
    """Remove credentials, connection strings, and bearer material from text."""
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    redacted = _DATABASE_URL.sub("[REDACTED_DATABASE_URL]", redacted)
    return _SECRET_ASSIGNMENT.sub(lambda match: match.group(0).split("=", 1)[0].split(":", 1)[0] + "=[REDACTED]", redacted)


def safe_log_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Project ``extra`` fields through an allowlist and recursively redact them."""
    result: dict[str, object] = {}
    for key, value in fields.items():
        normalized = str(key).casefold()
        if normalized not in _ALLOWED_LOG_FIELDS:
            continue
        if normalized in _SENSITIVE_KEYS:
            result[normalized] = "[REDACTED]"
        elif isinstance(value, str):
            result[normalized] = redact_text(value)
        else:
            result[normalized] = value
    return result


class JsonLogFormatter(logging.Formatter):
    """JSON formatter with a conservative field allowlist."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize one record while omitting non-allowlisted extras."""
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        payload.update(safe_log_fields({key: value for key, value in record.__dict__.items() if key not in _LOG_RECORD_KEYS}))
        if record.exc_info:
            exception_type = record.exc_info[0]
            if exception_type is not None:
                payload["exception_type"] = exception_type.__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SensitiveDataFilter(logging.Filter):
    """Defence-in-depth filter for handlers that do not use ``JsonLogFormatter``."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the rendered message before another handler processes it."""
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


def install_structured_logging(level: int = logging.INFO) -> None:
    """Configure one JSON stderr handler for a container process."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(SensitiveDataFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


_LOG_RECORD_KEYS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def _label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class MetricsRegistry:
    """Thread-safe counters, gauges and a bounded latency histogram."""

    def __init__(self) -> None:
        """Initialize empty metric families and their synchronization lock."""
        self._lock = Lock()
        self._counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: defaultdict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)

    @staticmethod
    def _key(name: str, labels: Mapping[str, object] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
        normalized = tuple(sorted((str(key), str(value)) for key, value in (labels or {}).items()))
        return name, normalized

    def inc(self, name: str, amount: float = 1, *, labels: Mapping[str, object] | None = None) -> None:
        """Increment a counter by a non-negative amount."""
        if amount < 0:
            raise ValueError("counter increments must be non-negative")
        with self._lock:
            self._counters[self._key(name, labels)] += amount

    def set(self, name: str, value: float, *, labels: Mapping[str, object] | None = None) -> None:
        """Set a gauge value."""
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def observe(self, name: str, value: float, *, labels: Mapping[str, object] | None = None) -> None:
        """Record a bounded sample for a latency summary."""
        with self._lock:
            values = self._histograms[self._key(name, labels)]
            values.append(value)
            if len(values) > 1000:
                del values[: len(values) - 1000]

    def render(self) -> str:
        """Render valid Prometheus text exposition without unbounded labels."""
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {key: tuple(values) for key, values in self._histograms.items()}
        lines: list[str] = []
        seen_types: set[str] = set()

        def emit(name: str, kind: str, labels: tuple[tuple[str, str], ...], value: float) -> None:
            if name not in seen_types:
                lines.extend((f"# TYPE {name} {kind}",))
                seen_types.add(name)
            rendered_labels = ""
            if labels:
                rendered_labels = "{" + ",".join(f'{key}="{_label_value(val)}"' for key, val in labels) + "}"
            lines.append(f"{name}{rendered_labels} {value:g}")

        for (name, labels), value in sorted(counters.items()):
            emit(name, "counter", labels, value)
        for (name, labels), value in sorted(gauges.items()):
            emit(name, "gauge", labels, value)
        for (name, labels), values in sorted(histograms.items()):
            if not values:
                continue
            emit(name, "summary", labels + (("quantile", "0.5"),), sorted(values)[len(values) // 2])
            emit(name, "summary", labels + (("quantile", "0.95"),), sorted(values)[min(len(values) - 1, int(len(values) * 0.95))])
        return "\n".join(lines) + ("\n" if lines else "")


METRICS = MetricsRegistry()


class RequestObservabilityMiddleware:
    """Attach a request ID and record coarse HTTP latency/status metrics."""

    def __init__(self, app: ASGIApp, metrics: MetricsRegistry = METRICS) -> None:
        """Store the wrapped application and registry."""
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Add correlation headers and record the completed HTTP exchange."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers", ())).get(REQUEST_ID_HEADER.encode())
        request_id = valid_request_id(incoming.decode("latin-1") if incoming else None) or new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_code = 500

        async def send_with_observability(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_observability)
        finally:
            duration = time.perf_counter() - started
            self.metrics.inc("http_requests_total", labels={"method": scope.get("method", "UNKNOWN"), "status": status_code})
            self.metrics.observe("http_request_duration_seconds", duration, labels={"method": scope.get("method", "UNKNOWN")})
            _LOG.info(
                "http request completed",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method", "UNKNOWN"),
                    "status_code": status_code,
                },
            )


def metrics_response(request: Request, *, token: str | None, metrics: MetricsRegistry = METRICS) -> PlainTextResponse:
    """Return a protected Prometheus scrape response."""
    if not token:
        return PlainTextResponse("not found\n", status_code=404)
    authorization = request.headers.get("authorization", "")
    provided = request.headers.get("x-metrics-token", "")
    if authorization.casefold().startswith("bearer "):
        provided = authorization[7:].strip()
    if not hmac.compare_digest(provided, token):
        return PlainTextResponse("unauthorized\n", status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")
