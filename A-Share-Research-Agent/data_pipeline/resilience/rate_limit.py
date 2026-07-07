"""RateLimiter — 令牌桶限流。

逻辑：
- 桶容量 `burst`，每秒补充 `rps` 个令牌
- 取令牌前 await 等待（不直接 reject，符合"优雅"原则）
- 用尽时仍可继续 1 次（兼容 burst 场景）
"""
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
        """按经过时间补充令牌。"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            float(self.config.burst),
            self._tokens + elapsed * self.config.rps,
        )
        self._last_refill = now

    async def acquire(self) -> None:
        """取 1 个令牌；不够时等待。"""
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # 等待时长 = 缺 1 个令牌所需时间
            wait = (1.0 - self._tokens) / self.config.rps
        self.stats.rate_limited_waits += 1
        logger.debug("rate_limit: 等待 %0.2fs 取令牌", wait)
        await asyncio.sleep(wait)
        # 二次取
        async with self._lock:
            self._refill()
            self._tokens = max(0.0, self._tokens - 1.0)

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """acquire() 后执行 fn。"""
        self.stats.total_calls += 1
        await self.acquire()
        try:
            result = await fn(*args, **kwargs)
            self.stats.success += 1
            return result
        except Exception:
            self.stats.failed += 1
            raise
