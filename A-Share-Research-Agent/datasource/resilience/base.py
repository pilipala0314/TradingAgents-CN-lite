"""韧性层基类 — 配置 schema + 异常 + 统计。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from datasource.errors import ResilienceError


# ── 配置 schema ─────────────────────────────────────────────────────

@dataclass
class RetryConfig:
    """重试配置。"""
    max_attempts: int = 3
    initial_delay_sec: float = 0.5
    max_delay_sec: float = 10.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retry_on: tuple[type[BaseException], ...] | None = None


@dataclass
class RateLimitConfig:
    """限流配置（令牌桶）。"""
    rps: float = 5.0
    burst: int = 10


@dataclass
class CircuitBreakerConfig:
    """熔断器配置。

    失败计数窗口 = 滑动时间窗（failure_window_sec）× 调用次数窗（window_size）双重裁剪：
    窗口内累计失败达 failure_threshold 次即熔断，与恢复期时长解耦。
    """
    failure_threshold: int = 5
    recovery_timeout_sec: float = 30.0
    window_size: int = 20
    failure_window_sec: float = 300.0


@dataclass
class BulkheadConfig:
    """并发隔离配置。"""
    max_concurrent: int = 10


# ── 统计 ─────────────────────────────────────────────────────────────

@dataclass
class ResilienceStats:
    """单一策略实例的统计。"""
    total_calls: int = 0
    success: int = 0
    failed: int = 0
    retries: int = 0
    rate_limited_waits: int = 0
    circuit_rejections: int = 0
    ip_switches: int = 0
    bulkhead_rejections: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total_calls": self.total_calls,
            "success": self.success,
            "failed": self.failed,
            "retries": self.retries,
            "rate_limited_waits": self.rate_limited_waits,
            "circuit_rejections": self.circuit_rejections,
            "ip_switches": self.ip_switches,
            "bulkhead_rejections": self.bulkhead_rejections,
        }


@dataclass
class ResilientEndpointStats:
    """一个 (method:provider) 端点的韧性统计聚合。"""
    method: str
    provider: str
    stats: dict[str, ResilienceStats] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "provider": self.provider,
            "by_strategy": {k: v.to_dict() for k, v in self.stats.items()},
        }
