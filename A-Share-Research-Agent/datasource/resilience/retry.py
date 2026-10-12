"""RetryPolicy — 指数退避 + 抖动。

重试是**唯一权威层**（HTTP 栈内部一律不重试，见 providers/_shared/http_client）。

成本约束（本项目按请求付费走代理）：每次重试 = 一次真实请求 = 一次付费。
因此**只重试「可能自愈」的瞬态故障**，确定性失败一律不重试：

- ``NonRetryableProviderError``（显式标记的参数/逻辑错）→ 不重试；
- HTTP 4xx 客户端错误（403 WAF / 404 等，408/429 除外）→ 不重试；
- 其余（连接中断、超时、5xx、429 限流）→ 重试（隧道每请求换 IP，重试即换出口）。

判定沿异常因果链下钻（provider 常把根因包进 ProviderError，需看到 ``__cause__``）。
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from datasource.errors import NonRetryableProviderError

from .base import ResilienceError, ResilienceStats, RetryConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 4xx 中「值得重试」的例外：408 请求超时、429 限流（换 IP 重试可能成功）
_RETRYABLE_4XX = frozenset({408, 429})
_MAX_CAUSE_DEPTH = 8


def is_retryable(exc: BaseException) -> bool:
    """该异常是否值得重试（沿因果链判定，命中任一不可重试信号即 False）。"""
    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < _MAX_CAUSE_DEPTH:
        if isinstance(cur, NonRetryableProviderError):
            return False
        status = getattr(getattr(cur, "response", None), "status_code", None)
        if isinstance(status, int) and 400 <= status < 500 and status not in _RETRYABLE_4XX:
            return False
        cur = cur.__cause__ or cur.__context__
        depth += 1
    return True


class RetryPolicy:
    """重试策略（指数退避 + 抖动；仅重试瞬态故障）。"""

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
                # 显式 retry_on 白名单优先（与默认可重试性判定取交集）
                if cfg.retry_on is not None and not isinstance(exc, cfg.retry_on):
                    self.stats.failed += 1
                    raise
                # 不可重试（确定性失败）→ 立即上抛，绝不浪费付费请求
                if not is_retryable(exc):
                    logger.info("retry: 不可重试错误，立即上抛（不再消耗请求）: %s", exc)
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
