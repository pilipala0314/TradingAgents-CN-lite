"""ResiliencePolicy — 装饰器链编排器。

洋葱模型：circuit → retry → rate_limit → bulkhead → fn
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .base import BulkheadConfig, CircuitBreakerConfig, RateLimitConfig, RetryConfig
from .bulkhead import Bulkhead
from .circuit_breaker import CircuitBreaker
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
        bulkhead: BulkheadConfig | Bulkhead | None = None,
    ) -> None:
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
        self.bulkhead: Bulkhead | None = (
            bulkhead if isinstance(bulkhead, Bulkhead)
            else (Bulkhead(bulkhead) if bulkhead else None)
        )

    async def execute(
        self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any,
    ) -> T:
        """按 circuit → retry → rate_limit → bulkhead → fn 顺序执行。"""
        async def call_inner() -> T:
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
        result: dict[str, dict[str, int]] = {}
        if self.retry is not None:
            result["retry"] = self.retry.stats.to_dict()
        if self.rate_limit is not None:
            result["rate_limit"] = self.rate_limit.stats.to_dict()
        if self.circuit is not None:
            result["circuit"] = self.circuit.stats.to_dict()
        if self.bulkhead is not None:
            result["bulkhead"] = self.bulkhead.stats.to_dict()
        return result


def policy_from_dict(
    config: dict[str, Any] | None,
) -> ResiliencePolicy:
    """从 dict 构造 ResiliencePolicy。"""
    if not config:
        return ResiliencePolicy()
    retry_cfg = RetryConfig(**config["retry"]) if "retry" in config else None
    rl_cfg = RateLimitConfig(**config["rate_limit"]) if "rate_limit" in config else None
    cb_cfg = CircuitBreakerConfig(**config["circuit"]) if "circuit" in config else None
    bh_cfg = BulkheadConfig(**config["bulkhead"]) if "bulkhead" in config else None
    return ResiliencePolicy(
        retry=retry_cfg, rate_limit=rl_cfg, circuit=cb_cfg,
        bulkhead=bh_cfg,
    )
