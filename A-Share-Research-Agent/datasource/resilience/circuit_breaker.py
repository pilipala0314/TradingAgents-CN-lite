"""CircuitBreaker — 熔断器（滑动窗口 + 3 状态）。

失败窗口语义：时间窗（failure_window_sec）× 次数窗（window_size）双重裁剪，
窗口内累计失败达阈值即熔断；与恢复期时长解耦。
HALF_OPEN 状态仅放行单个探测请求，避免并发批量试水。
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
        self._half_open_probing: bool = False  # HALF_OPEN 单探测标记
        self._lock = asyncio.Lock()
        self.stats = ResilienceStats()

    def _maybe_open_to_half_open(self) -> None:
        """恢复期已到：OPEN → HALF_OPEN（幂等，可安全并发调用）。"""
        if (
            self._state is CircuitState.OPEN
            and time.monotonic() - self._opened_at >= self.config.recovery_timeout_sec
        ):
            self._state = CircuitState.HALF_OPEN
            logger.info("circuit: OPEN → HALF_OPEN（恢复期已到）")

    @property
    def state(self) -> CircuitState:
        self._maybe_open_to_half_open()
        return self._state

    async def execute(
        self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any,
    ) -> T:
        self.stats.total_calls += 1
        async with self._lock:
            self._maybe_open_to_half_open()
            if self._state is CircuitState.OPEN:
                self.stats.circuit_rejections += 1
                self.stats.failed += 1
                raise ResilienceError(
                    "circuit", "熔断器已 OPEN，直接拒绝",
                    context={"state": self._state.value},
                )
            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_probing:
                    # 已有探测请求在飞：其余并发请求直接拒绝，避免批量试水
                    self.stats.circuit_rejections += 1
                    self.stats.failed += 1
                    raise ResilienceError(
                        "circuit", "熔断器 HALF_OPEN，探测中，拒绝并发请求",
                        context={"state": self._state.value},
                    )
                self._half_open_probing = True
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
            self._half_open_probing = False
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                logger.info("circuit: HALF_OPEN → CLOSED（试水成功）")

    async def _on_failure(self, exc: BaseException) -> None:
        async with self._lock:
            now = time.monotonic()
            self._failures.append(now)
            # 时间窗裁剪（与恢复期解耦，避免慢速持续故障永不触发熔断）
            cutoff = now - self.config.failure_window_sec
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()
            recent_failures = len(self._failures)
            self._half_open_probing = False
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = now
                logger.warning("circuit: HALF_OPEN → OPEN（试水失败: %s）", exc)
            elif recent_failures >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = now
                logger.warning("circuit: CLOSED → OPEN（窗口内 %d 次失败）", recent_failures)
