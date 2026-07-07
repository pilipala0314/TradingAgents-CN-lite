"""core/telemetry — 轻量级分布式 trace（P3.4）。

设计目标：
- 不依赖 opentelemetry-sdk / opentelemetry-api（无外部依赖，纯 stdlib）
- 接口与 OTel 风格一致：start_span / end_span / current_span / get_trace_id
- 写入 prometheus 指标（latency histogram）+ 当前 span 注入 FastAPI request.state
- 64-bit trace_id 随机生成（hex 16 字符）

适用：
- 单进程 API 链路追踪
- LLM 调用、DataRouter 调用、回测 run 的父子关系
- 跨进程 / 跨语言不在本期目标（需要 OTel collector）
"""
from __future__ import annotations

import logging
import secrets
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .metrics import LLM_LATENCY

logger = logging.getLogger(__name__)

# 当前激活的 span（ContextVar 隔离并发请求）
_current_span: ContextVar[Span | None] = ContextVar("current_span", default=None)


def _new_trace_id() -> str:
    return secrets.token_hex(8)  # 16 hex chars = 64 bit


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]  # 16 hex chars


@dataclass
class Span:
    """一个 trace 节点。

    - trace_id  跨 span 共享（同一请求）
    - span_id   唯一
    - parent_id 上级 span（None 表示 root）
    - name      span 名（"HTTP POST /research/run" / "DataRouter.get_market_bar"）
    - start_ts / end_ts 毫秒
    - attributes 额外属性（dict，例 "endpoint"="get_market_bar"）
    - status    "ok" / "error"
    """

    name: str
    trace_id: str
    span_id: str
    parent_id: str | None = None
    start_ts: float = 0.0
    end_ts: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        if self.end_ts <= 0:
            return 0.0
        return (self.end_ts - self.start_ts) * 1000.0

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: str, error: str | None = None) -> None:
        self.status = status
        if error:
            self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start_ms": round(self.start_ts * 1000, 3),
            "end_ms": round(self.end_ts * 1000, 3),
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status,
            "error": self.error,
            "attributes": dict(self.attributes),
        }


class Tracer:
    """全局 trace 管理器（单例）。

    使用：
        tracer = Tracer.get()
        with tracer.start_span("DataRouter.get_market_bar") as span:
            span.set_attribute("symbol", "600519")
            ...
    """

    _instance: Tracer | None = None

    @classmethod
    def get(cls) -> Tracer:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._completed_spans: list[Span] = []  # 测试 / 调试用

    def start_span(self, name: str, **attrs: Any) -> SpanContext:
        parent = _current_span.get()
        if parent is not None:
            trace_id = parent.trace_id
            parent_id = parent.span_id
        else:
            trace_id = _new_trace_id()
            parent_id = None
        span = Span(
            name=name,
            trace_id=trace_id,
            span_id=_new_span_id(),
            parent_id=parent_id,
            start_ts=time.time(),
            attributes=dict(attrs),
        )
        return SpanContext(span, self)

    def _on_end(self, span: Span) -> None:
        self._completed_spans.append(span)
        # 防止无限增长
        if len(self._completed_spans) > 5000:
            self._completed_spans = self._completed_spans[-2000:]
        # 调试日志（trace 模式下才开）
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("span end: %s dur=%.1fms status=%s", span.name, span.duration_ms, span.status)

    def completed(self, limit: int = 100) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._completed_spans[-limit:]]

    def clear(self) -> None:
        self._completed_spans.clear()


class SpanContext:
    """span 上下文管理器。

    进入 → 设为 current_span，结束 → 写 prometheus 指标 + 回到父 span。
    """

    def __init__(self, span: Span, tracer: Tracer) -> None:
        self._span = span
        self._tracer = tracer
        self._token: Any = None

    def __enter__(self) -> Span:
        self._token = _current_span.set(self._span)
        return self._span

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if exc_val is not None:
                self._span.set_status("error", error=str(exc_val))
            self._span.end_ts = time.time()
            # LLM span 写指标
            if self._span.name.startswith("llm."):
                layer = self._span.attributes.get("layer", "unknown")
                LLM_LATENCY.labels(layer=layer).observe(
                    max(self._span.end_ts - self._span.start_ts, 0.001),
                )
            self._tracer._on_end(self._span)
        finally:
            if self._token is not None:
                _current_span.reset(self._token)


def current_span() -> Span | None:
    """取当前激活的 span（没有则 None）。"""
    return _current_span.get()


def current_trace_id() -> str | None:
    s = _current_span.get()
    return s.trace_id if s else None


# 便捷函数
def trace(name: str, **attrs: Any) -> SpanContext:
    """快捷：tracer.get().start_span(name, **attrs)"""
    return Tracer.get().start_span(name, **attrs)
