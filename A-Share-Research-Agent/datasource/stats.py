"""datasource.stats — 端点健康度统计 + 诊断。

包含：
- ProviderAttemptDetail: 单次 provider 调用的结构化诊断
- LastErrorRecord: 最近 N 条错误记录
- HourBucket: 24h 分桶健康度
- EndpointErrorTracker: 错误队列 + 分桶追踪器
- EndpointStats: 单端点 (method:provider) 的完整健康度统计
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


# ── 单次 provider 调用诊断 ─────────────────────────────────────────

@dataclass
class ProviderAttemptDetail:
    """单次 provider 调用的结构化诊断。"""

    method: str
    provider: str
    attempted: bool = True
    success: bool = False
    latency_ms: float = 0.0
    error_type: str | None = None
    error_message: str | None = None
    resilience_strategy: str | None = None
    resilience_attempts: int = 0
    started_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "provider": self.provider,
            "attempted": self.attempted,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 3),
            "error_type": self.error_type,
            "error_message": self.error_message,
            "resilience_strategy": self.resilience_strategy,
            "resilience_attempts": self.resilience_attempts,
            "started_at": self.started_at,
        }

    @classmethod
    def from_exception(
        cls,
        *,
        method: str,
        provider: str,
        exc: BaseException,
        latency_ms: float,
        started_at: float,
        resilience_strategy: str | None = None,
        resilience_attempts: int = 0,
        attempted: bool = True,
    ) -> "ProviderAttemptDetail":
        err_msg = str(exc)
        if len(err_msg) > 240:
            err_msg = err_msg[:240] + "..."
        return cls(
            method=method, provider=provider, attempted=attempted,
            success=False, latency_ms=latency_ms,
            error_type=type(exc).__name__, error_message=err_msg,
            resilience_strategy=resilience_strategy,
            resilience_attempts=resilience_attempts, started_at=started_at,
        )

    @classmethod
    def from_success(
        cls,
        *,
        method: str,
        provider: str,
        latency_ms: float,
        started_at: float,
        resilience_attempts: int = 0,
    ) -> "ProviderAttemptDetail":
        return cls(
            method=method, provider=provider, attempted=True,
            success=True, latency_ms=latency_ms,
            started_at=started_at, resilience_attempts=resilience_attempts,
        )


# ── 最近错误记录 ────────────────────────────────────────────────

@dataclass
class LastErrorRecord:
    """单条最近错误。"""

    ts: float
    method: str
    provider: str
    error_type: str
    error_message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "method": self.method,
            "provider": self.provider,
            "error_type": self.error_type,
            "error_message": self.error_message[:240] if self.error_message else "",
        }


# ── 24h 分桶健康度 ──────────────────────────────────────────────

@dataclass
class HourBucket:
    """单小时分桶健康度。"""

    hour_ts: float
    calls: int = 0
    success: int = 0
    failure: int = 0

    @property
    def success_rate(self) -> float:
        return self.success / self.calls if self.calls else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hour_ts": self.hour_ts,
            "calls": self.calls,
            "success": self.success,
            "failure": self.failure,
            "success_rate": round(self.success_rate, 3),
        }


# ── 端点错误追踪器 ──────────────────────────────────────────────

class EndpointErrorTracker:
    """单端点的最近错误 + 24h 分桶追踪器。"""

    MAX_LAST_ERRORS = 5
    MAX_HOUR_BUCKETS = 24

    def __init__(self) -> None:
        self.last_errors: deque[LastErrorRecord] = deque(maxlen=self.MAX_LAST_ERRORS)
        self.hour_buckets: deque[HourBucket] = deque(maxlen=self.MAX_HOUR_BUCKETS)
        self._active_bucket: HourBucket | None = None
        self._active_hour_ts: float = 0.0

    def record_success(self, *, method: str, provider: str, ts: float | None = None) -> None:
        now = ts if ts is not None else time.time()
        self._ensure_bucket(now)
        assert self._active_bucket is not None
        self._active_bucket.calls += 1
        self._active_bucket.success += 1

    def record_failure(
        self, *, method: str, provider: str, error_type: str,
        error_message: str, ts: float | None = None,
    ) -> None:
        now = ts if ts is not None else time.time()
        self._ensure_bucket(now)
        assert self._active_bucket is not None
        self._active_bucket.calls += 1
        self._active_bucket.failure += 1
        self.last_errors.append(LastErrorRecord(
            ts=now, method=method, provider=provider,
            error_type=error_type, error_message=error_message[:240],
        ))

    def _ensure_bucket(self, ts: float) -> None:
        hour_ts = (ts // 3600) * 3600
        if self._active_bucket is None or hour_ts != self._active_hour_ts:
            self._active_bucket = HourBucket(hour_ts=hour_ts)
            self._active_hour_ts = hour_ts
            self.hour_buckets.append(self._active_bucket)

    def reset(self) -> None:
        self.last_errors.clear()
        self.hour_buckets.clear()
        self._active_bucket = None
        self._active_hour_ts = 0.0


# ── 端点健康度统计 ──────────────────────────────────────────────

@dataclass
class EndpointStats:
    """单端点 (method:provider) 的健康度统计。"""

    method: str
    provider: str
    total: int = 0
    success: int = 0
    failure: int = 0
    fallback_count: int = 0
    last_error: str = ""
    last_status: str = "idle"
    last_called_at: float = 0.0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    error_tracker: EndpointErrorTracker = field(default_factory=EndpointErrorTracker)

    def record(
        self,
        *,
        success: bool,
        latency_ms: float,
        error: str = "",
        error_type: str = "",
        is_fallback: bool = False,
    ) -> None:
        self.total += 1
        self.last_called_at = time.time()
        self.latencies_ms.append(latency_ms)
        if success:
            self.success += 1
            self.last_status = "success"
            self.last_error = ""
            self.error_tracker.record_success(method=self.method, provider=self.provider)
        else:
            self.failure += 1
            self.last_status = "failure"
            self.last_error = error
            self.error_tracker.record_failure(
                method=self.method, provider=self.provider,
                error_type=error_type or "Unknown", error_message=error,
            )
        if is_fallback:
            self.fallback_count += 1

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)
        return sorted_lat[idx]

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return sum(self.latencies_ms) / len(self.latencies_ms)

    @property
    def max_latency_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def fallback_rate(self) -> float:
        return self.fallback_count / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "provider": self.provider,
            "total": self.total,
            "success": self.success,
            "failure": self.failure,
            "success_rate": round(self.success_rate, 3),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "max_latency_ms": round(self.max_latency_ms, 1),
            "fallback_count": self.fallback_count,
            "fallback_rate": round(self.fallback_rate, 3),
            "last_error": self.last_error[:200] if self.last_error else "",
            "last_status": self.last_status,
            "last_called_at": self.last_called_at,
            "last_errors": [e.to_dict() for e in self.error_tracker.last_errors],
            "hour_buckets": [b.to_dict() for b in self.error_tracker.hour_buckets],
        }
