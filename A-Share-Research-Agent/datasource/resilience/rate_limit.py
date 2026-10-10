"""RateLimiter — 令牌桶限流。"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .base import RateLimitConfig, ResilienceStats

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimiter:
    """令牌桶限流器。"""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._tokens = float(self.config.burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self.stats = ResilienceStats()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self.config.burst), self._tokens + elapsed * self.config.rps)
        self._last_refill = now

    async def acquire(self) -> None:
        """获取一个令牌；不足时预扣并等待后返回（预扣模式防超发）。"""
        if self.config.rps <= 0:
            return  # rps<=0 视为不限流（同时避免下方除零）
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # 预扣最后一个令牌：等待期间其他协程不会抢走
            wait = (1.0 - self._tokens) / self.config.rps
            self._tokens = max(0.0, self._tokens - 1.0)
        self.stats.rate_limited_waits += 1
        logger.debug("rate_limit: 等待 %0.2fs 取令牌", wait)
        await asyncio.sleep(wait)

    async def execute(
        self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any,
    ) -> T:
        self.stats.total_calls += 1
        await self.acquire()
        try:
            result = await fn(*args, **kwargs)
            self.stats.success += 1
            return result
        except Exception:
            self.stats.failed += 1
            raise
