"""apps/api/middleware — P3.4 监控中间件。

- TracingMiddleware：每个请求 → root span（写入 contextvar）→ request.state
- MetricsMiddleware：每个请求 → http_request_duration / total / errors

path label 裁剪：避免高基数（去掉 query string + 把 {uuid} 类归一化）。
"""
from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from core.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUEST_ERRORS,
    HTTP_REQUEST_TOTAL,
)
from core.telemetry import Tracer

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEX_RE = re.compile(r"[0-9a-f]{16,}", re.I)
_JOBID_RE = re.compile(r"/jobs/[0-9a-f-]{16,}", re.I)


def _normalize_path(path: str) -> str:
    """把动态段归一化，避免 Prometheus 标签爆炸。

    /research/run/abc-123-uuid-456  → /research/run/{id}
    """
    p = path.split("?")[0]
    p = _UUID_RE.sub("{id}", p)
    p = _HEX_RE.sub("{hex}", p)
    p = _JOBID_RE.sub("/jobs/{id}", p)
    return p or "/"


class TracingMiddleware(BaseHTTPMiddleware):
    """为每个请求建一个 root span，注入到 request.state。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = _normalize_path(request.url.path)
        name = f"HTTP {request.method} {path}"
        ctx = Tracer.get().start_span(name, http_method=request.method, http_path=path)
        with ctx as span:
            request.state.span = span
            request.state.trace_id = span.trace_id
            response = await call_next(request)
            span.set_attribute("http.status_code", response.status_code)
            return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录每个 HTTP 请求的耗时、计数、错误数。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = _normalize_path(request.url.path)
        method = request.method
        # /metrics 自身不计入指标（避免自循环膨胀）
        if path == "/metrics":
            return await call_next(request)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        except Exception:
            status = "500"
            raise
        finally:
            dur = time.perf_counter() - start
            HTTP_REQUEST_DURATION.labels(method=method, path=path, status=status).observe(dur)
            HTTP_REQUEST_TOTAL.labels(method=method, path=path, status=status).inc()
            if status.startswith("4") or status.startswith("5"):
                HTTP_REQUEST_ERRORS.labels(method=method, path=path, status=status).inc()
