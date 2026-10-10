"""RetryPolicy — 指数退避 + 抖动。"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .base import ResilienceError, ResilienceStats, RetryConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryPolicy:
    """重试策略（指数退避 + 抖动）。"""

    def __init__(self, config: RetryConfig | None = None) -> None:
        self.config = config or RetryConfig()
        self.stats = ResilienceStats()

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        cfg = self.config
        self.stats.total_calls += 1
        last_exc: BaseException | None = None
        for attempt in range(1, cfg.max_attempts + 1):
            try:
                result = await fn(*args, **kwargs)
                if attempt > 1:
                    logger.info("retry: 第 %d/%d 次重试成功", attempt, cfg.max_attempts)
                self.stats.success += 1
                return result
            except Exception as exc:
                last_exc = exc
                if cfg.retry_on is not None and not isinstance(exc, cfg.retry_on):
                    self.stats.failed += 1
                    raise
                if attempt < cfg.max_attempts:
                    delay = self._compute_delay(attempt)
                    logger.warning(
                        "retry: 第 %d/%d 次失败: %s，%0.2fs 后重试",
                        attempt, cfg.max_attempts, exc, delay,
                    )
                    self.stats.retries += 1
                    await asyncio.sleep(delay)
                else:
                    logger.error("retry: %d 次重试用尽: %s", cfg.max_attempts, exc)
        self.stats.failed += 1
        raise ResilienceError(
            "retry", f"重试 {cfg.max_attempts} 次后仍失败",
            attempts=cfg.max_attempts, last_error=last_exc,
            context={"max_attempts": cfg.max_attempts},
        ) from last_exc

    def _compute_delay(self, attempt: int) -> float:
        delay = self.config.initial_delay_sec * (self.config.backoff_factor ** (attempt - 1))
        delay = min(delay, self.config.max_delay_sec)
        if self.config.jitter:
            jitter = delay * 0.25
            delay = delay + random.uniform(-jitter, jitter)
        return max(0.0, delay)
