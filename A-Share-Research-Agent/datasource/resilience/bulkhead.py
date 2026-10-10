"""Bulkhead — 并发隔离（信号量）。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .base import BulkheadConfig, ResilienceStats

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Bulkhead:
    """信号量并发隔离。"""

    def __init__(self, config: BulkheadConfig | None = None) -> None:
        self.config = config or BulkheadConfig()
        self._sem = asyncio.Semaphore(self.config.max_concurrent)
        self.stats = ResilienceStats()

    async def execute(
        self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any,
    ) -> T:
        self.stats.total_calls += 1
        await self._sem.acquire()
        try:
            self.stats.success += 1
            return await fn(*args, **kwargs)
        except Exception:
            self.stats.failed += 1
            raise
        finally:
            self._sem.release()
