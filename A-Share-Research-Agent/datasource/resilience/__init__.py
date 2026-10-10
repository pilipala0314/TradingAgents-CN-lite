"""datasource.resilience — 韧性层：数据源统一防护。

模块结构：
- base.py           配置 schema / 异常 / 统计
- retry.py          RetryPolicy（指数退避 + 抖动）
- rate_limit.py     RateLimiter（令牌桶）
- circuit_breaker.py CircuitBreaker（滑动窗口 + 3 状态）
- bulkhead.py       Bulkhead（信号量并发隔离）
- policy.py         ResiliencePolicy 装饰器编排器
"""
from __future__ import annotations

from .base import (
    BulkheadConfig,
    CircuitBreakerConfig,
    RateLimitConfig,
    ResilienceError,
    ResilienceStats,
    ResilientEndpointStats,
    RetryConfig,
)
from .bulkhead import Bulkhead
from .circuit_breaker import CircuitBreaker, CircuitState
from .policy import ResiliencePolicy, policy_from_dict
from .rate_limit import RateLimiter
from .retry import RetryPolicy

__all__ = [
    "Bulkhead",
    "BulkheadConfig",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "RateLimitConfig",
    "RateLimiter",
    "ResilienceError",
    "ResiliencePolicy",
    "ResilienceStats",
    "ResilientEndpointStats",
    "RetryConfig",
    "RetryPolicy",
    "policy_from_dict",
]
