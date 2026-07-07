"""RetryPolicy — 指数退避 + 抖动。

逻辑：
- 第 N 次失败后等待 `initial * factor^(N-1)` 秒，上限 `max_delay_sec`
- 加 ±25% 抖动避免雪崩
- 用尽后抛 `ResilienceError("retry", ...)`
"""
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
        """执行 fn，失败时按策略重试。"""
        cfg = self.config
        self.stats.total_calls += 1
        last_exc: BaseException | None = None
        for attempt in range(1, cfg.max_attempts + 1):
            try:
                result = await fn(*args, **kwargs)
                if attempt > 1:
                    logger.info(
                        "retry: 第 %d/%d 次重试成功", attempt, cfg.max_attempts,
                    )
                self.stats.success += 1
                return result
            except Exception as exc:
                last_exc = exc
                # 类型过滤
                if cfg.retry_on is not None and not isinstance(exc, cfg.retry_on):
                    # 不在重试白名单 → 直接抛
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
                    logger.error(
                        "retry: %d 次重试用尽，最后错误: %s",
                        cfg.max_attempts, exc,
                    )
        # 用尽
        self.stats.failed += 1
        raise ResilienceError(
            "retry",
            f"重试 {cfg.max_attempts} 次后仍失败",
            attempts=cfg.max_attempts,
            last_error=last_exc,
            context={"max_attempts": cfg.max_attempts},
        ) from last_exc

    def _compute_delay(self, attempt: int) -> float:
        """计算第 N 次重试前的延迟（秒）。"""
        delay = self.config.initial_delay_sec * (self.config.backoff_factor ** (attempt - 1))
        delay = min(delay, self.config.max_delay_sec)
        if self.config.jitter:
            # ±25% 抖动
            jitter = delay * 0.25
            delay = delay + random.uniform(-jitter, jitter)
        return max(0.0, delay)
