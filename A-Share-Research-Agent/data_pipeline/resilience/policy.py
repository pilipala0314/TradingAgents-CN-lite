"""ResiliencePolicy — 装饰器链编排器。

把多个策略按"洋葱模型"组合（从外到内）：
  circuit → retry → rate_limit → bulkhead → ip_pool → fn

调用方式：
```python
policy = ResiliencePolicy(
    retry=RetryConfig(max_attempts=3),
    rate_limit=RateLimitConfig(rps=2.0),
    circuit=CircuitBreakerConfig(failure_threshold=5),
    ip_pool=ProxyEnvIpPool(),
    bulkhead=BulkheadConfig(max_concurrent=10),
)
result = await policy.execute(my_async_fn, arg1, arg2, kw=value)
```

优雅原则：
- 任一子策略都可单独 disable（传 None）
- 装饰器链构造时延迟实例化（不传就不创建）
- 统一 stats 聚合（policy.stats() 返回所有子策略的合并）
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .base import (
    BulkheadConfig,
    CircuitBreakerConfig,
    RateLimitConfig,
    RetryConfig,
)
from .bulkhead import Bulkhead
from .circuit_breaker import CircuitBreaker
from .ip_pool import CffiImpersonateIpPool, IpPool, ProxyEnvIpPool
from .rate_limit import RateLimiter
from .retry import RetryPolicy

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ResiliencePolicy:
    """韧性策略编排器。"""

    def __init__(
        self,
        *,
        retry: RetryConfig | RetryPolicy | None = None,
        rate_limit: RateLimitConfig | RateLimiter | None = None,
        circuit: CircuitBreakerConfig | CircuitBreaker | None = None,
        ip_pool: IpPool | ProxyEnvIpPool | CffiImpersonateIpPool | None = None,
        bulkhead: BulkheadConfig | Bulkhead | None = None,
        # IP 池策略：CffiImpersonate 避免循环引用
        cffi_impersonate: bool = False,
    ) -> None:
        # 把 Config 转成 Policy
        self.retry: RetryPolicy | None = (
            retry if isinstance(retry, RetryPolicy) else (RetryPolicy(retry) if retry else None)
        )
        self.rate_limit: RateLimiter | None = (
            rate_limit if isinstance(rate_limit, RateLimiter)
            else (RateLimiter(rate_limit) if rate_limit else None)
        )
        self.circuit: CircuitBreaker | None = (
            circuit if isinstance(circuit, CircuitBreaker)
            else (CircuitBreaker(circuit) if circuit else None)
        )
        if ip_pool is not None:
            self.ip_pool: IpPool | None = ip_pool
        elif cffi_impersonate:
            # CffiImpersonateIpPool 已在模块顶部 import，无循环引用。
            self.ip_pool = CffiImpersonateIpPool()
        else:
            self.ip_pool = None
        self.bulkhead: Bulkhead | None = (
            bulkhead if isinstance(bulkhead, Bulkhead)
            else (Bulkhead(bulkhead) if bulkhead else None)
        )

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """按 circuit → retry → rate_limit → bulkhead → fn 顺序执行。"""
        # 装饰器链：从外到内
        # 最外层：circuit
        async def call_inner() -> T:
            # retry 包住 rate_limit + bulkhead
            async def call_with_rate_limit_and_bulkhead() -> T:
                if self.rate_limit is not None:
                    async def call_with_bulkhead() -> T:
                        if self.bulkhead is not None:
                            return await self.bulkhead.execute(fn, *args, **kwargs)
                        return await fn(*args, **kwargs)
                    return await self.rate_limit.execute(call_with_bulkhead)
                if self.bulkhead is not None:
                    return await self.bulkhead.execute(fn, *args, **kwargs)
                return await fn(*args, **kwargs)

            if self.retry is not None:
                return await self.retry.execute(call_with_rate_limit_and_bulkhead)
            return await call_with_rate_limit_and_bulkhead()

        if self.circuit is not None:
            return await self.circuit.execute(call_inner)
        return await call_inner()

    def stats(self) -> dict[str, dict[str, int]]:
        """聚合所有子策略的统计。"""
        result: dict[str, dict[str, int]] = {}
        if self.retry is not None:
            result["retry"] = self.retry.stats.to_dict()
        if self.rate_limit is not None:
            result["rate_limit"] = self.rate_limit.stats.to_dict()
        if self.circuit is not None:
            result["circuit"] = self.circuit.stats.to_dict()
        if self.ip_pool is not None:
            result["ip_pool"] = self.ip_pool.stats()
        if self.bulkhead is not None:
            result["bulkhead"] = self.bulkhead.stats.to_dict()
        return result


# ── 便捷构造：from_dict ────────────────────────────────────────────

def policy_from_dict(
    config: dict[str, Any] | None,
    *,
    default_cffi: bool = False,
) -> ResiliencePolicy:
    """从 dict 构造 ResiliencePolicy（用于 DataRouter 配置注入）。

    dict 形态（任一字段可省）：
    ```python
    {
        "retry": {"max_attempts": 3, "initial_delay_sec": 0.5},
        "rate_limit": {"rps": 2.0, "burst": 5},
        "circuit": {"failure_threshold": 5, "recovery_timeout_sec": 30},
        "ip_pool": "proxy_env",  # or "cffi_impersonate" or False
        "bulkhead": {"max_concurrent": 10},
    }
    ```
    """
    if not config:
        return ResiliencePolicy()
    retry_cfg = RetryConfig(**config["retry"]) if "retry" in config else None
    rl_cfg = RateLimitConfig(**config["rate_limit"]) if "rate_limit" in config else None
    cb_cfg = CircuitBreakerConfig(**config["circuit"]) if "circuit" in config else None
    bh_cfg = BulkheadConfig(**config["bulkhead"]) if "bulkhead" in config else None
    ip_choice = config.get("ip_pool")
    cffi = default_cffi or ip_choice == "cffi_impersonate"
    return ResiliencePolicy(
        retry=retry_cfg,
        rate_limit=rl_cfg,
        circuit=cb_cfg,
        bulkhead=bh_cfg,
        cffi_impersonate=cffi,
    )
