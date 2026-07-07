"""韧性层 — 数据源统一防护。

模块结构：
- `base.py`           Protocol / 异常 / 统计 / 配置 schema
- `retry.py`          RetryPolicy（指数退避 + 抖动）
- `rate_limit.py`     RateLimiter（令牌桶）
- `circuit_breaker.py` CircuitBreaker（滑动窗口 + 3 状态）
- `ip_pool.py`        IpPool Protocol + ProxyEnvIpPool + CffiImpersonateIpPool
- `bulkhead.py`       Bulkhead（信号量并发隔离）
- `policy.py`         ResiliencePolicy 装饰器编排器

设计原则（用户要求"优雅、不搞复杂"）：
- 每个策略类 < 100 行
- 配置用 `dataclass` 显式声明（不用 dict 配置）
- 策略之间通过 `ResiliencePolicy` 装饰器链组合，不互相依赖
- DataRouter 通过 `endpoint_resilience(method, provider)` 注入
"""
from __future__ import annotations

from .base import (
    BulkheadConfig,
    CircuitBreakerConfig,
    IpPoolConfig,
    RateLimitConfig,
    ResilienceError,
    ResilienceStats,
    ResilientEndpointStats,
    RetryConfig,
)
from .bulkhead import Bulkhead
from .circuit_breaker import CircuitBreaker, CircuitState
from .ip_pool import CffiImpersonateIpPool, IpPool, IpSelection, ProxyEnvIpPool
from .policy import ResiliencePolicy, policy_from_dict
from .rate_limit import RateLimiter
from .retry import RetryPolicy

__all__ = [
    "Bulkhead",
    "BulkheadConfig",
    "CffiImpersonateIpPool",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "IpPool",
    "IpPoolConfig",
    "IpSelection",
    "ProxyEnvIpPool",
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
