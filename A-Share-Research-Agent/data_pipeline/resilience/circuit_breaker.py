"""CircuitBreaker — 熔断器（滑动窗口 + 3 状态）。

状态机：
- CLOSED     正常调用
- OPEN       直接拒绝（抛 ResilienceError），等待 `recovery_timeout_sec` 后变 HALF_OPEN
- HALF_OPEN  放 1 个请求试水；成功 → CLOSED，失败 → OPEN
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, TypeVar

from .base import CircuitBreakerConfig, ResilienceError, ResilienceStats

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """熔断器。"""

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self.config = config or CircuitBreakerConfig()
        self._state: CircuitState = CircuitState.CLOSED
        self._failures: deque[float] = deque(maxlen=self.config.window_size)
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()
        self.stats = ResilienceStats()

    @property
    def state(self) -> CircuitState:
        """读取状态时会自动做 OPEN→HALF_OPEN 转换。"""
        if (
            self._state is CircuitState.OPEN
            and time.monotonic() - self._opened_at >= self.config.recovery_timeout_sec
        ):
            self._state = CircuitState.HALF_OPEN
            logger.info("circuit: OPEN → HALF_OPEN（恢复期已到）")
        return self._state

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        self.stats.total_calls += 1
        async with self._lock:
            current = self.state
            if current is CircuitState.OPEN:
                self.stats.circuit_rejections += 1
                self.stats.failed += 1
                raise ResilienceError(
                    "circuit",
                    "熔断器已 OPEN，直接拒绝",
                    context={"state": current.value},
                )

        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            await self._on_failure(exc)
            self.stats.failed += 1
            raise
        else:
            await self._on_success()
            self.stats.success += 1
            return result

    async def _on_success(self) -> None:
        async with self._lock:
            self._failures.clear()
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info("circuit: HALF_OPEN → CLOSED（试水成功）")

    async def _on_failure(self, exc: BaseException) -> None:
        async with self._lock:
            now = time.monotonic()
            self._failures.append(now)
            # 滑动窗口：清理窗口外
            cutoff = now - self.config.recovery_timeout_sec
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()
            recent_failures = len(self._failures)
            if self._state is CircuitState.HALF_OPEN:
                # 试水失败 → 重新 OPEN
                self._state = CircuitState.OPEN
                self._opened_at = now
                logger.warning("circuit: HALF_OPEN → OPEN（试水失败: %s）", exc)
            elif recent_failures >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = now
                logger.warning(
                    "circuit: CLOSED → OPEN（窗口内 %d 次失败）", recent_failures,
                )
